#!/usr/bin/env bash
# Copies the hub plugin to no-mods/hub next to this script and leaves out its mod,
# so the run sees hub as Claude Code without mods would see it.
# Takes the plugin folder as its one argument, the repo's plugins/hub by default.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
source="${1:-$here/../../plugins/hub}"
copy="$here/no-mods/hub"
rm -rf "$here/no-mods"
mkdir -p "$here/no-mods"
cp -R "$source" "$copy"
rm -rf "$copy/mod"
find "$copy" -name __pycache__ -type d -prune -exec rm -rf {} +
python3 -I - "$copy" <<'PY'
import json
import os
import sys

root = sys.argv[1]
for name, key in (("hooks/hooks.json", "modules"), (".claude-plugin/plugin.json", "types")):
    path = os.path.join(root, name)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    data.pop(key, None)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
PY
