"""The task list as text, plain text or JSON.

    tasks [--width N] [--plain] [--json] [--brief]
    tasks done
    tasks show <name>
"""
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import files, tasks  # noqa: E402

if __name__ == "__main__":
    sys.exit(tasks.main(sys.argv[1:], files.state_path("task_list"), hook_verb=False))
