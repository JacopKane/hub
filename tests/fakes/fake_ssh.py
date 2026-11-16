#!/usr/bin/env python3
"""A stand-in for ssh in the PR test gate scenarios. It never connects.

`ssh [options] -G -- [user@]<host> [command]` prints the settings ssh would
use for the host, as the real one does after it reads its config. The JSON file in
$FAKE_SSH_CONFIG says which Host aliases name another host:

- hosts: {alias: the HostName ssh connects to}; any other host names itself
- users: {user: {alias: HostName}}, as a Match user block, for a destination
  that names that user before an @; it comes before hosts
- fail: hosts whose config ssh can not read, which exit 255
- ports: {port: {alias: HostName}}, as a Match exec block on %p, for the port
  ssh connects to (22 unless -p names one); it comes after users
- tags: {tag: {alias: HostName}}, as a Match tagged block for the tag -P names
- jumps: {jump host: {alias: HostName}}, as a Match exec block on %j for the
  jump host -J names
- sessions: {"none": {alias: HostName}}, as a Match sessiontype block, for -N
  or -W, which ask for no session; "exec" stands for a call with a command and
  neither of them
- commands: {pattern: {alias: HostName}}, as a Match command block, for a call
  whose command, its words joined by spaces, the glob pattern matches
- users, tags, jumps, sessions and commands come before ports, in that order

Options before -G change that, as they do for the real ssh: -F <file> reads
the JSON file it names in place of $FAKE_SSH_CONFIG, -o HostName=<name> (or
-oHostName=<name>) names the host for any alias, -p <port> names the port, -l
<user> the user, in place of the one before the @, -P <tag> the tag and -J
<host> the jump host. Each also comes glued, as in -p<port>. As in the real
ssh, the first -p, -l or -P wins, and a second -J fails.

Each call's words go to $FAKE_SSH_LOG, one JSON list a line. Every other call
exits 255, as when ssh can not connect.
"""
import fnmatch
import json
import os
import sys


def read_config(path):
    if path and os.path.exists(path):
        with open(path) as handle:
            return json.load(handle)
    return {}


VALUE_OPTIONS = {"-F": "config_file", "-p": "port", "-l": "user", "-P": "tag", "-J": "jump", "-W": "forward"}


def read_options(options):
    """{the setting each option names: config_file, hostname, port, user, tag, jump, session}, or None for any
    other option or a second -J."""
    read, index = {}, 0
    while index < len(options):
        option = options[index]
        if option == "-N":
            read, index = {**read, "session": "none"}, index + 1
            continue
        if (option in VALUE_OPTIONS or option == "-o") and index + 1 < len(options):
            value, index = options[index + 1], index + 2
        elif option[:2] in (*VALUE_OPTIONS, "-o") and len(option) > 2:
            option, value, index = option[:2], option[2:], index + 1
        else:
            return None
        if option == "-o":
            key, _equals, setting = value.partition("=")
            if key.lower() != "hostname" or not setting:
                return None
            read = {"hostname": setting, **read}
        elif option == "-J" and "jump" in read:
            return None
        elif option == "-W":
            read = {"session": "none", **read}
        else:
            read = {VALUE_OPTIONS[option]: value, **read}
    return read


words = sys.argv[1:]
if os.environ.get("FAKE_SSH_LOG"):
    with open(os.environ["FAKE_SSH_LOG"], "a") as handle:
        handle.write(json.dumps(words) + "\n")
asks_config = next((index for index in range(len(words) - 2) if words[index:index + 2] == ["-G", "--"]), None)
options, after = (words[:asks_config], words[asks_config + 2:]) if asks_config is not None else ([], [])
read = read_options(options) if asks_config is not None else None
config = read_config(read.get("config_file") or os.environ.get("FAKE_SSH_CONFIG")) if read is not None else {}
named_user, _at, host = (after[:1] or [""])[0].rpartition("@")
command = " ".join(after[1:])
if read is None or host in config.get("fail", []):
    print("ssh: could not read the config", file=sys.stderr)
    sys.exit(255)
port, user = read.get("port", "22"), read.get("user", named_user)
session = read.get("session") or ("exec" if command else None)
by_command = next((hosts for pattern, hosts in config.get("commands", {}).items()
                   if command and fnmatch.fnmatchcase(command, pattern)), {})
blocks = [*(config.get(name, {}).get(key, {}) for name, key in
            (("users", user), ("tags", read.get("tag")), ("jumps", read.get("jump")), ("sessions", session))),
          by_command, config.get("ports", {}).get(port, {})]
hostname = read.get("hostname") or next((block[host] for block in blocks if host in block),
                                        config.get("hosts", {}).get(host, host))
print(f"host {host}\nuser {user or 'local'}\nhostname {hostname}\nport {port}")
