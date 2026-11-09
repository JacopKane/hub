#!/usr/bin/env python3
"""A local stand-in for the Messages API, for the lab tests only.

mock_api.py LOG_DIR SCRIPT PORT

It answers with scripted replies, so the real claude binary runs with no
model and no sign-in, and it logs every request body to
LOG_DIR/req-NNN.json. A request that offers the Bash tool is the main loop.
For it, the first rule in SCRIPT["rules"] whose "match" text is in the last
user turn wins. A rule reply is {"text": "..."} or {"tool": "Bash",
"input": {...}}. A last turn that holds only tool results gets "done". Every
other request, such as a summary or a title, is a side call and gets
SCRIPT["side_reply"] as text, or "ok".
"""
import itertools
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_DIR = sys.argv[1]
SCRIPT_PATH = sys.argv[2]
PORT = int(sys.argv[3])
counter = itertools.count(1)
lock = threading.Lock()


def load_script():
    try:
        with open(SCRIPT_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def last_user_turn(body):
    turns = [message for message in body.get("messages") or [] if message.get("role") == "user"]
    if not turns:
        return "", False
    content = turns[-1].get("content")
    if isinstance(content, str):
        return content, False
    texts, only_results = [], True
    for block in content or []:
        if block.get("type") == "text":
            texts.append(block.get("text", ""))
            if not block.get("text", "").startswith("<system-reminder>"):
                only_results = False
        elif block.get("type") != "tool_result":
            only_results = False
    has_result = any(block.get("type") == "tool_result" for block in content or [])
    return "\n".join(texts), has_result and only_results


def is_main_loop(body):
    return any(tool.get("name") == "Bash" for tool in body.get("tools") or [])


def pick_reply(body):
    script = load_script()
    if not is_main_loop(body):
        return {"text": script.get("side_reply", "ok")}
    text, only_results = last_user_turn(body)
    if only_results:
        return {"text": "done"}
    for rule in script.get("rules", []):
        if rule["match"] in text:
            return rule["reply"]
    return {"text": "ok"}


def blocks_for(reply, number):
    if "tool" in reply:
        block = {"type": "tool_use", "id": f"toolu_lab{number:04d}", "name": reply["tool"], "input": reply["input"]}
        return [block], "tool_use"
    return [{"type": "text", "text": reply["text"]}], "end_turn"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_json(200, {"data": [], "has_more": False})

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        with lock:
            number = next(counter)
        path = self.path.split("?")[0]
        with open(os.path.join(LOG_DIR, f"req-{number:03d}.json"), "w") as handle:
            json.dump({"path": path, "main_loop": is_main_loop(body), "body": body}, handle)
        if path.endswith("/count_tokens"):
            return self.send_json(200, {"input_tokens": 100})
        if not path.endswith("/v1/messages"):
            return self.send_json(200, {})
        content, stop = blocks_for(pick_reply(body), number)
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        message = {"id": f"msg_lab{number}", "type": "message", "role": "assistant",
                   "model": body.get("model", "mock"), "content": content, "stop_reason": stop,
                   "stop_sequence": None, "usage": usage}
        if not body.get("stream"):
            return self.send_json(200, message)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def send(event, data):
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())

        send("message_start", {"type": "message_start", "message": dict(message, content=[], stop_reason=None)})
        for index, block in enumerate(content):
            if block["type"] == "text":
                start = {"type": "text", "text": ""}
                delta = {"type": "text_delta", "text": block["text"]}
            else:
                start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
                delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            send("content_block_start", {"type": "content_block_start", "index": index, "content_block": start})
            send("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
            send("content_block_stop", {"type": "content_block_stop", "index": index})
        send("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                               "usage": {"output_tokens": 5}})
        send("message_stop", {"type": "message_stop"})
        self.wfile.flush()


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
