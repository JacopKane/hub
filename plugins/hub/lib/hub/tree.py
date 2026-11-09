"""Load every part.json into one tree and check it.

The standard library has no JSON Schema validator, so the checks are code.
schema/part.schema.json lists the same fields for editors, and a test keeps
the two equal. Every problem is reported with its file and id, and nothing in
a manifest is ever run.
"""
import json
import os
import re

from . import conditions

FAMILIES = ["core", "setup", "tiers", "chats", "questions", "gates", "rules", "learning",
            "housekeeping", "screens", "tools", "notify"]
PART_KINDS = {"core", "hook", "agent", "skill", "rule", "style", "mod", "extra"}
NODE_KINDS = {"behavior", "instruction", "option", "group"}
TYPES = {"switch", "mode", "list", "text", "number", "choice"}
TOOLS = ["claude_code", "cursor"]
COSTS = {"none", "listing", "start text", "per use"}
# guard: a project file may raise it, never lower it, and a guarded list adds up over the scopes.
# loosens: on makes the checks weaker, so a project file may only set it off. Children take guard.
PRIVACY = {"guard", "loosens", "none"}
CURSOR_EVENT_NAMES = {
    "PreToolUse": "preToolUse", "PostToolUse": "postToolUse", "SessionStart": "sessionStart",
    "SessionEnd": "sessionEnd",
    "UserPromptSubmit": "beforeSubmitPrompt", "Stop": "stop", "SubagentStop": "subagentStop",
    "PreCompact": "preCompact",
}
# Events only Claude Code sends. A handler for one of them may not run in Cursor.
CLAUDE_ONLY_EVENTS = ("Notification", "PermissionRequest", "PermissionDenied")
# Every event a part may handle, in the order the hooks files list them.
EVENTS = (*CURSOR_EVENT_NAMES, *CLAUDE_ONLY_EVENTS)
SWITCH_VALUES = ["off", "on"]
# A beta part or flag is untested. It defaults off, is never offered, and every door names its group.
STAGES = {"beta"}
BETA, BETA_LABEL = "beta", "Beta, untested"
BETA_NOTE = "{part} is in beta and untested. Type /hub off {part} to turn it off."

NODE_FIELDS = {"id", "kind", "type", "does", "default", "values", "runs_in", "needs", "needs_any",
               "relevant_if", "signals", "cost", "text", "privacy", "reason_not_in", "range", "check",
               "stage", "children"}
PART_FIELDS = NODE_FIELDS | {"$schema", "family", "offer_when", "handlers", "requires", "keeps_on_uninstall",
                             "commands", "jobs"}
JOB_FIELDS = {"name", "flag", "script", "args", "every_seconds", "at_login", "keep_alive", "start", "stop"}
# hubctl's own verbs. A part command may not take one of these names.
CORE_VERBS = {"show", "screen", "on", "off", "set", "pin", "no", "undo", "apply", "doctor", "update",
              "offers", "take", "uninstall", "profile", "setup", "tuner", "open", "mail", "mod-command"}


def pattern_problem(value):
    """Why a text with check "pattern" can not be used, or None when it can."""
    if not isinstance(value, str):
        return "it is not a text"
    if not value.strip():
        return "it is empty"
    try:
        re.compile(value)
    except re.error as error:
        return f"it is not a valid pattern ({error})"
    return None


class TreeError(Exception):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("\n".join(problems))


class Node:
    def __init__(self, data, parent, part, source):
        self.data = data
        self.parent = parent
        self.part = part or self
        self.source = source
        self.name = data.get("id", "")
        self.id = f"{parent.id}.{self.name}" if parent else self.name
        self.kind = data.get("kind", "behavior" if parent else "")
        self.does = data.get("does", "")
        self.type = data.get("type") or ("mode" if "values" in data and not parent else "switch")
        self.values = data.get("values", SWITCH_VALUES if self.type == "switch" else [])
        self.needs = data.get("needs", [])
        self.needs_any = data.get("needs_any", [])
        self.signals = data.get("signals", [])
        self.text = data.get("text")
        self.children = []
        self.family = data.get("family", parent.family if parent else "")
        self.handlers = data.get("handlers", {}) if not parent else {}
        own = data.get("runs_in")
        self.runs_in = own if own is not None else (parent.runs_in if parent else list(TOOLS))
        inherited = "guard" if parent is not None and parent.privacy == "guard" else "none"
        self.privacy = data.get("privacy", inherited)
        self.stage = data.get("stage", parent.stage if parent else None)
        self.default_expr = None
        self.relevant_expr = None
        self.offer_expr = None

    @property
    def mode_owner(self):
        """The nearest ancestor whose value is a mode, which caps this node."""
        node = self.parent
        while node is not None:
            if node.type == "mode":
                return node
            node = node.parent
        return None

    def allowed_values(self):
        owner = self.mode_owner
        if self.type == "switch" and owner is not None:
            return ["off", "on"] + [value for value in owner.values if value != "off"]
        return self.values

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class Tree:
    def __init__(self, parts):
        self.parts = parts
        self.nodes = {}
        for part in parts:
            for node in part.walk():
                self.nodes[node.id] = node

    def get(self, flag_id):
        return self.nodes.get(flag_id)


def handler_runs_in(spec, tool):
    """Whether a part's handler spec runs in a tool. A spec without runs_in runs where its part runs."""
    return tool in spec.get("runs_in", TOOLS)


def manifest_paths(parts_dir):
    try:
        names = sorted(os.listdir(parts_dir))
    except FileNotFoundError:
        return []
    return [os.path.join(parts_dir, name, "part.json") for name in names
            if os.path.isfile(os.path.join(parts_dir, name, "part.json"))]


def _compile(node, field, problems, where):
    text = node.data.get(field)
    if text is None:
        return None
    if not isinstance(text, str):
        problems.append(f"{where}: {node.id}.{field} must be a text condition")
        return None
    try:
        return conditions.compile_condition(text)
    except conditions.ConditionError as error:
        problems.append(f"{where}: {node.id}.{field}: {error}")
        return None


def _check_node(node, problems, part_dir):
    where = node.source
    data = node.data
    allowed = PART_FIELDS if node.parent is None else NODE_FIELDS
    for key in data:
        if key not in allowed:
            problems.append(f"{where}: {node.id or '?'}: unknown field {key!r}")
    if not isinstance(node.name, str) or not node.name or not node.name.replace("_", "a").isalnum() \
            or node.name != node.name.lower():
        problems.append(f"{where}: {node.id or '?'}: the id must be snake_case")
    if node.parent is None:
        if node.kind not in PART_KINDS:
            problems.append(f"{where}: {node.id}: kind must be one of {sorted(PART_KINDS)}")
        if node.family not in FAMILIES:
            problems.append(f"{where}: {node.id}: family must be one of {FAMILIES}")
        for event, spec in node.handlers.items():
            if event not in EVENTS:
                problems.append(f"{where}: {node.id}: unknown event {event!r}")
            if not isinstance(spec, dict) or not isinstance(spec.get("tools", []), list):
                problems.append(f"{where}: {node.id}: handlers.{event} needs a tools list")
                continue
            runs_in = spec.get("runs_in", TOOLS)
            if not isinstance(runs_in, list) or any(tool not in TOOLS for tool in runs_in):
                problems.append(f"{where}: {node.id}: handlers.{event}.runs_in names an unknown tool in {runs_in!r}")
            elif event in CLAUDE_ONLY_EVENTS and "cursor" in node.runs_in and "cursor" in runs_in:
                problems.append(f"{where}: {node.id}: Cursor has no {event} event, so the handler can not run "
                                "in Cursor")
        if node.handlers and not os.path.isfile(os.path.join(part_dir, "handler.py")):
            problems.append(f"{where}: {node.id}: handlers are named but handler.py is missing")
    elif node.kind not in NODE_KINDS:
        problems.append(f"{where}: {node.id}: kind must be one of {sorted(NODE_KINDS)}")
    if node.type not in TYPES:
        problems.append(f"{where}: {node.id}: type must be one of {sorted(TYPES)}")
    if node.type in ("mode", "choice") and not node.values:
        problems.append(f"{where}: {node.id}: a {node.type} needs a list of values")
    elif node.type == "mode" and "off" not in node.values:
        problems.append(f"{where}: {node.id}: a mode needs off among its values")
    for tool in data.get("reason_not_in", {}):
        if tool not in TOOLS:
            problems.append(f"{where}: {node.id}: reason_not_in names an unknown tool {tool!r}")
    if node.privacy not in PRIVACY:
        problems.append(f"{where}: {node.id}: privacy must be one of {sorted(PRIVACY)}")
    if data.get("cost") is not None and data["cost"] not in COSTS:
        problems.append(f"{where}: {node.id}: cost must be one of {sorted(COSTS)}")
    for tool in node.runs_in:
        if tool not in TOOLS:
            problems.append(f"{where}: {node.id}: runs_in names an unknown tool {tool!r}")
    if "range" in data:
        bounds = data["range"]
        if node.type != "number" or not (isinstance(bounds, list) and len(bounds) == 2
                                         and all(isinstance(bound, int) for bound in bounds)
                                         and bounds[0] <= bounds[1]):
            problems.append(f"{where}: {node.id}: range must be [lowest, highest] on a number")
        elif isinstance(data.get("default"), int) and not bounds[0] <= data["default"] <= bounds[1]:
            problems.append(f"{where}: {node.id}: default {data['default']} is outside {bounds}")
    if "check" in data:
        if data["check"] != "pattern" or node.type != "text":
            problems.append(f"{where}: {node.id}: check must be \"pattern\" on a text")
        else:
            problem = pattern_problem(data.get("default", ""))
            if problem:
                problems.append(f"{where}: {node.id}: the default pattern can not be used, because {problem}")
    _check_stage(node, problems, where)
    if node.parent is None:
        _check_commands(node, problems, where, part_dir)
    if node.text is not None and not os.path.isfile(os.path.join(part_dir, node.text)):
        problems.append(f"{where}: {node.id}: text file {node.text!r} is missing")
    default = data.get("default")
    if isinstance(default, str) and node.type in ("switch", "mode", "choice"):
        node.default_expr = _compile(node, "default", problems, where)
        if node.default_expr and node.default_expr[0] == "value" and node.default_expr[1] not in node.allowed_values():
            problems.append(f"{where}: {node.id}: default {default!r} is not one of {node.allowed_values()}")
    node.relevant_expr = _compile(node, "relevant_if", problems, where)
    node.offer_expr = _compile(node, "offer_when", problems, where)


def _check_stage(node, problems, where):
    """A beta flag defaults off and a beta part is never offered, so only the user turns one on.

    A flag that runs only in Cursor must be beta, because hub's Cursor support is untested.
    """
    data = node.data
    if "stage" in data:
        if data["stage"] not in STAGES:
            problems.append(f"{where}: {node.id}: stage must be one of {sorted(STAGES)}")
        elif data.get("default", "off" if node.parent is None else "on") != "off":
            problems.append(f"{where}: {node.id}: a beta flag must default off, so only the user turns it on")
    if "stage" in data and node.parent is not None and node.parent.stage != BETA:
        problems.append(f"{where}: {node.id}: a beta flag needs a beta part, because the doors mark beta on the part")
    if node.stage == BETA and node.parent is None and "offer_when" in data:
        problems.append(f"{where}: {node.id}: a beta part is never offered, so it can not have offer_when")
    if node.runs_in == ["cursor"] and node.stage != BETA:
        problems.append(f"{where}: {node.id}: it runs only in Cursor, so it must be beta (\"stage\": \"beta\")")


def _check_commands(node, problems, where, part_dir):
    commands = node.data.get("commands", {})
    if not isinstance(commands, dict):
        problems.append(f"{where}: {node.id}: commands must map a name to its script and what it does")
        return
    for name, spec in commands.items():
        if name in CORE_VERBS:
            problems.append(f"{where}: {node.id}: the command {name!r} is a core verb of hub's step, so a part can not "
                            "take that name")
        elif not name.replace("_", "a").replace("-", "a").isalnum() or name != name.lower():
            problems.append(f"{where}: {node.id}: the command {name!r} must be lowercase words")
        if not isinstance(spec, dict) or set(spec) != {"script", "does"}:
            problems.append(f"{where}: {node.id}: commands.{name} needs a script and a does line, and nothing else")
            continue
        script = os.path.normpath(os.path.join(part_dir, str(spec["script"])))
        if not script.startswith(os.path.normpath(part_dir) + os.sep) or not os.path.isfile(script):
            problems.append(f"{where}: {node.id}: commands.{name}: the script {spec['script']!r} is not a file "
                            "in the part's folder")


def _build(data, parent, part, source, problems, part_dir):
    if not isinstance(data, dict):
        problems.append(f"{source}: a node must be an object")
        return None
    node = Node(data, parent, part, source)
    _check_node(node, problems, part_dir)
    seen = set()
    for child in data.get("children", []):
        built = _build(child, node, node.part, source, problems, part_dir)
        if built is None:
            continue
        if built.name in seen:
            problems.append(f"{source}: {built.id}: the id is used twice")
        seen.add(built.name)
        node.children.append(built)
    return node


def load(parts_dir):
    problems, parts = [], []
    for path in manifest_paths(parts_dir):
        source = os.path.relpath(path, os.path.dirname(parts_dir))
        folder = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except ValueError as error:
            problems.append(f"{source}: not valid JSON ({error})")
            continue
        part = _build(data, None, None, source, problems, os.path.dirname(path))
        if part is None:
            continue
        if part.id != folder:
            problems.append(f"{source}: {part.id}: the id must match its folder {folder!r}")
        parts.append(part)
    order = {family: index for index, family in enumerate(FAMILIES)}
    parts.sort(key=lambda part: (order.get(part.family, len(order)), part.id))
    tree = Tree(parts)
    for node in tree.nodes.values():
        for need in node.needs + node.needs_any:
            if need not in tree.nodes:
                problems.append(f"{node.source}: {node.id}: needs names an unknown id {need!r}")
            elif need == node.id:
                problems.append(f"{node.source}: {node.id}: a flag can not need itself")
    owners = {}
    for part in parts:
        for name in part.data.get("commands", {}) if isinstance(part.data.get("commands"), dict) else {}:
            if name in owners:
                problems.append(f"{part.source}: {part.id}: the command {name!r} is also named by {owners[name]}")
            owners.setdefault(name, part.id)
    _check_jobs(parts, tree, problems, parts_dir)
    if problems:
        raise TreeError(problems)
    return tree


def _check_jobs(parts, tree, problems, parts_dir):
    """A part's jobs: a known flag of the same part, a script in its folder, a whole number or a number flag."""
    names = set()
    for part in parts:
        jobs = part.data.get("jobs", [])
        if not isinstance(jobs, list):
            problems.append(f"{part.source}: {part.id}: jobs must be a list")
            continue
        for job in jobs:
            where = f"{part.source}: {part.id}: job {job.get('name', '?') if isinstance(job, dict) else '?'}"
            if not isinstance(job, dict):
                problems.append(f"{where}: a job must be an object")
                continue
            for key in job:
                if key not in JOB_FIELDS:
                    problems.append(f"{where}: unknown field {key!r}")
            name = job.get("name")
            if not isinstance(name, str) or not name or not name.replace("-", "a").isalnum() or name != name.lower():
                problems.append(f"{where}: the name must be lowercase words joined by -")
            elif name in names:
                problems.append(f"{where}: the name is used twice")
            names.add(name)
            flag = job.get("flag")
            if flag != part.id and not (isinstance(flag, str) and flag.startswith(part.id + ".")) \
                    or flag not in tree.nodes:
                problems.append(f"{where}: flag {flag!r} is not a flag of this part")
            script = job.get("script")
            if not isinstance(script, str) or os.path.isabs(script) or ".." in script.split("/") \
                    or not os.path.isfile(os.path.join(parts_dir, part.id, script)):
                problems.append(f"{where}: script {script!r} is not a file in the part's folder")
            every = job.get("every_seconds", 0)
            if isinstance(every, str):
                node = tree.nodes.get(every)
                if node is None or node.type != "number":
                    problems.append(f"{where}: every_seconds {every!r} is not a number flag")
            elif isinstance(every, bool) or not isinstance(every, int) or every < 0:
                problems.append(f"{where}: every_seconds must be a whole number or a number flag")
            for key in ("args", "start", "stop"):
                value = job.get(key, [])
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    problems.append(f"{where}: {key} must be a list of texts")
            for key in ("at_login", "keep_alive"):
                if not isinstance(job.get(key, False), bool):
                    problems.append(f"{where}: {key} must be true or false")
