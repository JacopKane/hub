"""The review loop of each area, as text. It only reads.

    review-loop show [area]

Each line names an area, whether it runs or is stopped, its failed reviews
in a row, and its reviewer starts since the last pass. Nothing here lifts a
stop. Only the user's own typed answer does.
"""
import json
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import files  # noqa: E402

FOLDER = "review_loop"
USAGE = "Use: review-loop show [area]"
NONE_YET = "No review has run under an acceptance list yet."


def states():
    folder = files.state_path(FOLDER)
    try:
        names = sorted(name for name in os.listdir(folder) if name.endswith(".json"))
    except OSError:
        return []
    found = []
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and state.get("area"):
            found.append(state)
    return found


def plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def line_of(state):
    stop = state.get("stop")
    status = "stopped, waiting for the user's answer" if stop else "running"
    failed = plural(int(state.get("streak", 0)), "failed review")
    starts = plural(int(state.get("starts", 0)), "review")
    return f"{state['area']}: {status}, {failed} in a row, {starts} started since the last pass."


def main(words):
    if not words or words[0] != "show" or len(words) > 2:
        print(USAGE, file=sys.stderr)
        return 2
    wanted = [state for state in states() if len(words) == 1 or state["area"] == words[1]]
    print("\n".join(line_of(state) for state in wanted) if wanted else NONE_YET)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
