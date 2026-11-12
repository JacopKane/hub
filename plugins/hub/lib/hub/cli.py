"""hub's internal step, which the switch skill and the mod run. lib/hub_switch.py only starts this.

    hubctl show [--all] [--tool claude_code|cursor]     the screen as text, or the old table with --json
    hubctl screen --json [--all] [--tool ...] [--band]  the screen model every door draws
    hubctl on|off <flag>       hubctl set <flag> <value>...       hubctl pin <flag>
    hubctl undo [id]           hubctl no <part>
    hubctl apply               hubctl doctor [--json]             hubctl update
    hubctl offers              hubctl take <part>                 hubctl uninstall
    hubctl setup --answer FILE | --reply JSON
    hubctl profile [scan | set <path> <value>]
    hubctl tuner decide --answer FILE|-                 hubctl tuner ask
    hubctl tuner give-back --since TIME

The change verbs take --scope user|project|local, or --project and --local,
and --json. A value typed here is set by the user, so it is pinned. Inside a
tool call it is set by the model, and it is never a pin. Turning on a beta
flag prints a one-line note that it is untested. A part
adds its own commands in its part.json, under `commands`, so a new part needs
no change here.
"""
import argparse
import json
import os
import subprocess
import sys

from . import apply, build, changes, doctor, files, links, resolve, screen, settings, slots, textview, tree as tree_module
from . import profile, protect, setup, tuner, uninstall

UNINSTALL_ASK = 'Only the user can uninstall hub, by asking in their own words, such as "uninstall hub". Ask the user.'


def add_scope(command):
    command.add_argument("--scope", choices=settings.SCOPES, default="user")
    command.add_argument("--project", dest="scope", action="store_const", const="project",
                         help="the same as --scope project")
    command.add_argument("--local", dest="scope", action="store_const", const="local",
                         help="the same as --scope local")


def add_view(command):
    command.add_argument("--all", action="store_true", help="also list hidden flags, with the reason")
    command.add_argument("--json", action="store_true", help="print JSON")
    command.add_argument("--tool", choices=tree_module.TOOLS, default="claude_code")


def part_commands():
    """{name: (part, spec)} from every part.json, or {} when the tree has a problem (show reports it)."""
    try:
        tree = tree_module.load(files.PARTS_DIR)
    except tree_module.TreeError:
        return {}
    return {name: (part, spec) for part in tree.parts for name, spec in part.data.get("commands", {}).items()}


def parser(extra):
    top = argparse.ArgumentParser(prog="hub", description="Switch hub's parts on and off.")
    commands = top.add_subparsers(dest="command", required=True, metavar="command")
    add_view(commands.add_parser("show", help="the screen as text: changes, parts and their flags"))
    command = commands.add_parser("screen", help="the screen model as JSON, for the band and the pane")
    add_view(command)
    command.add_argument("--band", action="store_true",
                         help="the mod's read for the band: it notes the line the band shows, so no chat repeats it")
    for name in ("on", "off"):
        command = commands.add_parser(name, help=f"switch a flag {name}")
        command.add_argument("flag")
        add_scope(command)
        command.add_argument("--json", action="store_true")
    command = commands.add_parser("set", help="set a flag to a value, or a list flag to its values")
    command.add_argument("flag")
    command.add_argument("values", nargs="+")
    add_scope(command)
    command.add_argument("--json", action="store_true")
    command = commands.add_parser("pin", help="keep a flag's value, so setup and the tuner leave it")
    command.add_argument("flag")
    add_scope(command)
    command.add_argument("--json", action="store_true")
    command = commands.add_parser("no", help="say no to an offered part; it stays off")
    command.add_argument("part")
    command.add_argument("--json", action="store_true")
    command = commands.add_parser("undo", help="take back the last change, or the change with this id, pinned")
    command.add_argument("id", nargs="?")
    command.add_argument("--json", action="store_true")
    commands.add_parser("apply", help="check the parts and write the resolved state")
    command = commands.add_parser("doctor", help="check the setup and name what needs you")
    command.add_argument("--json", action="store_true")
    commands.add_parser("update", help="update the hub plugin in Claude Code, then apply")
    command = commands.add_parser("mod-command", help="note which name the mod's slash command got (the mod runs it)")
    command.add_argument("--json", action="store_true", required=True, help="read the record as JSON on stdin")
    commands.add_parser("offers", help="list your agents that hold a hub agent of the same name")
    command = commands.add_parser("take", help="hold your agent of the same name and use hub's")
    command.add_argument("part")
    commands.add_parser("uninstall", help="remove hub's links, copies and state, and keep your files")
    command = commands.add_parser("setup", help="turn the setup agent's answer into questions and apply the replies")
    step = command.add_mutually_exclusive_group()
    step.add_argument("--answer", metavar="FILE", help="the setup agent's JSON answer")
    step.add_argument("--reply", metavar="JSON", help='the answers, such as {"use": "work"}')
    command = commands.add_parser("profile", help="print the situation profile, scan this machine into it, or set a fact")
    command.add_argument("words", nargs="*", metavar="scan | set <path> <value>")
    command = commands.add_parser("tuner", help="apply the changes the tuner's model picked, print the model "
                                  "request for the mod (ask), or hand the mod's list to the next chat (give-back)")
    command.add_argument("step", choices=["decide", "ask", "give-back"])
    command.add_argument("--answer", metavar="FILE", help='{"pick": [flag ids]}, or - for stdin')
    command.add_argument("--since", metavar="TIME", help="give-back: the time of the list the mod tried")
    for name, (part, spec) in sorted(extra.items()):
        command = commands.add_parser(name, help=f"{spec['does']} (from {part.id})")
        command.add_argument("words", nargs=argparse.REMAINDER)
    return top


def shown(data, tool, everything):
    flags = {}
    for flag_id, record in data["tools"][tool]["flags"].items():
        if not everything and not record["relevant"]:
            continue
        flags[flag_id] = {"value": record["set"], "effective": record["value"], "runs": record["runs"],
                          "by": record["by"], "scope": record["scope"],
                          "relevant": record["relevant"], "hidden_because": record["hidden_because"]}
    from . import slots
    return {"tool": tool, "flags": flags, "start_text": slots.start_text(slots.read(tool)),
            "cost": apply.cost(tool)}


def print_lines(lines):
    for line in lines:
        print(line)


def print_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def value_for(node, values):
    if node.type == "list":
        return values
    if len(values) != 1:
        raise changes.ChangeError(f"{node.id} takes one value")
    return values[0]


def changed(args, line, notes, flag_id):
    row = screen.row_for(flag_id, os.getcwd())
    if args.json:
        print_json({"change": line, "row": row, "notes": notes})
    else:
        print_lines(notes)
        print(textview.row_line(dict(row, depth=0, name=row["id"])))
        print(textview.UNDO_LINE)


def run_part_command(part, spec, words):
    """Run a part's own command when the part is on. Checking that reads the flags and sets nothing up."""
    if uninstall.is_out():
        raise uninstall.Uninstalled(uninstall.OUT)
    flags = resolve.in_memory(os.getcwd())["claude_code"]["flags"]
    if not flags[part.id]["runs"]:
        raise changes.ChangeError(f"{part.id} is off, so its command does not run. Switch it on by saying "
                                  f"\"turn on {part.id}\".")
    script = os.path.join(files.PARTS_DIR, part.id, spec["script"])
    program = [sys.executable, "-I", script] if script.endswith(".py") else [script]
    return subprocess.run([*program, *words]).returncode


def update():
    plugin = uninstall.plugin_ids()[0]
    try:
        done = subprocess.run(["claude", "plugin", "update", plugin], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise changes.ChangeError(f"claude plugin update {plugin} did not run ({error})") from None
    if done.returncode != 0:
        raise changes.ChangeError(f"claude plugin update {plugin} failed: {done.stderr.strip() or done.stdout.strip()}")
    with files.locked():
        lines = apply.run()
    news = [entry for entry in screen.model(os.getcwd())["strip"] if entry["kind"] in ("update", "offer")]
    lines += [f"{entry['text']}. {', or '.join(textview.command(button) for button in entry['buttons'])}"
              for entry in news]
    return lines or ["hub is up to date."]


def profile_command(words):
    if not words:
        print_json(profile.read())
    elif words == ["scan"]:
        from . import scan
        with files.locked():
            profile.write(profile.from_scan(scan.run(os.getcwd())))
            apply.run()
        print_json(profile.read())
    elif len(words) == 3 and words[0] == "set":
        changes.refuse_in_tool_call("set a profile fact, which pins it")
        try:
            value = json.loads(words[2])
        except ValueError:
            value = words[2]
        with files.locked():
            old, new = profile.set_fact(words[1], value)
            apply.run()
        print(f"{words[1]} is {json.dumps(new, ensure_ascii=False)} now, and was {json.dumps(old, ensure_ascii=False)}. "
              "It is pinned, so a scan and the tuner leave it.")
    else:
        raise profile.ProfileError("use profile, profile scan, or profile set <path> <value>")
    return 0


def read_answer(path):
    try:
        text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
        return json.loads(text)
    except (OSError, ValueError) as error:
        raise changes.ChangeError(f"could not read the tuner answer: {error}") from None


def main(argv=None):
    extra = part_commands()
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in extra:
        # A part command takes every word after its name, leading options too, such as hubctl board --json.
        args = parser(extra).parse_args(argv[:1])
        args.words = argv[1:]
    else:
        args = parser(extra).parse_args(argv)
    try:
        if args.command == "show":
            if args.json:
                print_json(shown(resolve.current(os.getcwd()), args.tool, args.all))
            else:
                sys.stdout.write(textview.render(screen.model(os.getcwd(), args.tool, args.all)))
        elif args.command == "screen":
            model = screen.model(os.getcwd(), args.tool, args.all)
            print_json(tuner.band_read(model) if args.band else model)
        elif args.command in ("on", "off", "set"):
            node = tree_module.load(files.PARTS_DIR).get(args.flag)
            if node is None:
                raise changes.ChangeError(f"no flag named {args.flag!r}")
            if args.command == "set":
                value = value_for(node, args.values)
            else:
                value = "off" if args.command == "off" else changes.on_value(node)
            notes = []
            line = changes.set_flag(args.flag, value, scope=args.scope, notes=notes)
            if node.stage == tree_module.BETA and resolve.is_on(node, value):
                notes.insert(0, tree_module.BETA_NOTE.format(part=node.part.id))
            changed(args, line, notes, args.flag)
        elif args.command == "pin":
            notes = []
            line = changes.pin(args.flag, scope=args.scope, notes=notes)
            changed(args, line, notes, args.flag)
        elif args.command == "no":
            offer = changes.decline_offer(args.part)
            if args.json:
                print_json({"declined": offer})
            else:
                print(f"You said no to {args.part}. It stays off. {textview.UNDO_LINE}")
        elif args.command == "undo":
            line = changes.undo(line_id=args.id)
            if args.json:
                print_json({"undid": line})
            elif line.get("action") == "take_offer":
                print(f"moved {line['held']} back to {line['source']}")
            elif line.get("action") == "decline_offer":
                print(f"the offer of {line['part']} is back")
            else:
                print(f"restored {line['flag']} to {json.dumps(line['old'], ensure_ascii=False)}")
        elif args.command == "apply":
            if uninstall.is_out():
                print(uninstall.OUT)
            else:
                with files.locked():
                    print_lines(apply.run())
        elif args.command == "doctor":
            found = doctor.report(os.getcwd())
            if args.json:
                print_json(found)
            else:
                sys.stdout.write(doctor.render(found))
        elif args.command == "update":
            print_lines(update())
        elif args.command == "mod-command":
            try:
                doctor.note_command(json.loads(sys.stdin.read()), os.getcwd())
            except (AttributeError, ValueError) as error:
                print(f"hub: {error}", file=sys.stderr)
                return 1
        elif args.command == "offers":
            resolve.current(os.getcwd())
            found = links.offers()
            if not found:
                print("no offers")
            for offer in found:
                print(f"{offer['part']}: your agent {offer['path']} has the same name, so hub made no "
                      f"{offer['link']}. Say \"use hub's {offer['part']}\" to hold your file and use hub's. "
                      f"Say \"undo that\" to move it back.")
        elif args.command == "take":
            notes = []
            line = changes.take_offer(args.part, notes=notes)
            print(f"moved {line['source']} to {line['held']}")
            print_lines(notes)
        elif args.command == "uninstall":
            if files.in_tool_call() and not protect.take_uninstall_ticket():
                raise changes.ChangeError(UNINSTALL_ASK)
            print_lines(uninstall.run())
        elif args.command == "setup":
            if [bool(args.answer), args.reply is not None].count(True) != 1:
                raise setup.SetupError("use setup --answer FILE or setup --reply JSON")
            if args.answer:
                print_json(setup.take_answer(args.answer, os.getcwd()))
            else:
                print_lines(setup.take_reply(args.reply, os.getcwd()))
        elif args.command == "profile":
            return profile_command(args.words)
        elif args.command == "tuner" and args.step == "ask":
            print_json(tuner.ask(os.getcwd()))
        elif args.command == "tuner" and args.step == "give-back":
            if not args.since:
                raise changes.ChangeError("tuner give-back needs --since, the time of the list the mod tried")
            print("the next chat gets the tuner's list" if tuner.give_back(args.since)
                  else "nothing to give back, because no list or a newer list waits")
        elif args.command == "tuner":
            if not args.answer:
                raise changes.ChangeError("tuner decide needs --answer FILE, or --answer - to read stdin")
            print_lines([tuner.notice_text(line) for line in tuner.decide(read_answer(args.answer), os.getcwd())]
                        or ["the tuner changed nothing"])
        else:
            part, spec = extra[args.command]
            return run_part_command(part, spec, args.words)
    except tree_module.TreeError as error:
        for problem in error.problems:
            print(problem, file=sys.stderr)
        return 1
    except (changes.ChangeError, settings.SettingsError, links.LinkError, build.BuildError,
            uninstall.Uninstalled, setup.SetupError, profile.ProfileError) as error:
        print(f"hub: {error}", file=sys.stderr)
        return 1
    return 0
