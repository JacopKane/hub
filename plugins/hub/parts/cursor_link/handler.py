"""The cursor_link part gives Cursor the same parts.

plugin_files(plugin_root, prefix) renders the files of hub's Cursor plugin
from the Claude Code plugin's manifest, its skills and the tree:

    .cursor-plugin/plugin.json   the manifest, whose description says Cursor support is beta
    cursor/hooks.json            one entry for each event a part handles in Cursor
    cursor/skills/hub/SKILL.md   the hubctl skill as /hub, since Cursor has no mod
                                 to own that name
    cursor/skills/<name>/SKILL.md  each other skill under its own name

Each command starts the Cursor adapter or hubctl under prefix. The repo ships the files
with the prefix ./plugins/hub. Apply writes them into ~/.cursor/plugins/local/hub
with the absolute path of the installed copy, because Cursor skips a linked
plugin folder outside plugins/local.

At Cursor's sessionStart, handle() gives the start text apply built for Cursor.
Cursor has no output styles, so style_text() gives apply the body of hub's
style, with only the sections that run in Cursor, to put first in that text.
"""
import json
import os
import posixpath
import shlex

from hub import build, files, slots, tree as tree_module
from hub.adapters import cursor_hooks

MANIFEST = ".cursor-plugin/plugin.json"
STYLE_PART = "hub_style"
HOOKS = "cursor/hooks.json"
SKILLS = "cursor/skills/"
CLAUDE_SKILLS = "skills"
CURSOR_NAMES = {"hubctl": "hub"}  # Cursor has no mod to own /hub, so the hubctl skill takes it
CLAUDE_HUBCTL = "python3 -I ${CLAUDE_PLUGIN_ROOT}/bin/hubctl"
CURSOR_SKILL_KEYS = ("name", "description", "disable-model-invocation")
FAIL_CLOSED = {"preToolUse"}  # a crash in the adapter refuses the call
CLAUDE_CODE_ONLY = ("types",)  # only Claude Code's mod reads it, and Cursor runs no mods
BETA_SENTENCE = "Cursor support is in beta and untested."


def _dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def plugin_files(plugin_root, prefix):
    """{path in the plugin folder: text} for Cursor, with commands under prefix."""
    with open(os.path.join(plugin_root, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    for key in CLAUDE_CODE_ONLY:
        manifest.pop(key, None)
    if BETA_SENTENCE not in manifest["description"]:
        manifest["description"] = f"{manifest['description'].rstrip()} {BETA_SENTENCE}"
    manifest["hooks"] = "./" + HOOKS
    manifest["skills"] = "./" + SKILLS
    parts = tree_module.load(os.path.join(plugin_root, "parts"))
    cursor_names = {claude: cursor for cursor, claude in cursor_hooks.TOOL_NAMES.items()}
    events = {}
    for part in parts.parts:
        if "cursor" not in part.runs_in:
            continue
        for event, spec in part.handlers.items():
            if not tree_module.handler_runs_in(spec, "cursor"):
                continue
            events.setdefault(event, set()).update(spec.get("tools", []))
    adapter = shlex.quote(posixpath.join(prefix, "lib", "hub", "adapters", "cursor_hooks.py"))
    hooks = {}
    for event in sorted(events, key=list(tree_module.CURSOR_EVENT_NAMES).index):
        name = tree_module.CURSOR_EVENT_NAMES[event]
        entry = {"matcher": "|".join(sorted(cursor_names.get(tool, tool) for tool in events[event])),
                 "command": f"python3 -I {adapter} {name}"}
        if name in FAIL_CLOSED:
            entry["failClosed"] = True
        hooks[name] = [entry]
    rendered = {MANIFEST: _dump(manifest), HOOKS: _dump({"version": 1, "hooks": hooks})}
    folder = os.path.join(plugin_root, CLAUDE_SKILLS)
    for name in sorted(os.listdir(folder)):
        if os.path.isfile(os.path.join(folder, name, "SKILL.md")):
            cursor_name = CURSOR_NAMES.get(name, name)
            rendered[f"{SKILLS}{cursor_name}/SKILL.md"] = cursor_skill(plugin_root, prefix, name, cursor_name)
    return rendered


def cursor_skill(plugin_root, prefix, name, cursor_name):
    """A skill for Cursor, named cursor_name, with only the keys Cursor reads and hubctl under prefix."""
    with open(os.path.join(plugin_root, CLAUDE_SKILLS, name, "SKILL.md"), encoding="utf-8") as handle:
        lines, body = build.split(handle.read())
    head = [f"name: {cursor_name}" if line.partition(":")[0].strip() == "name" else line
            for line in build.keep_only(lines, CURSOR_SKILL_KEYS)]
    hubctl = "python3 -I " + shlex.quote(posixpath.join(prefix, "bin", "hubctl"))
    return build.join(head, body.replace(CLAUDE_HUBCTL, hubctl))


def handle(event, hook_input, flags, answer):
    if event == "SessionStart" and flags.runs("cursor_link.text_slots"):
        answer.add_context(slots.start_text(slots.read("cursor")))


def style_text(tree, flags):
    """The style's body for Cursor: no front matter, no markers, only the sections that run."""
    part = tree.get(STYLE_PART)
    shipped = os.path.join(files.PARTS_DIR, STYLE_PART, "style.md")
    text = files.read_text(shipped)
    if part is None or not text or not flags.get(STYLE_PART, {}).get("runs"):
        return ""
    built, _changed = build.build(text, part, flags, shipped)
    return build.split(built)[1].strip()
