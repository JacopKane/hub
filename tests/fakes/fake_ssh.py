#!/usr/bin/env python3
"""A stand-in for ssh in the PR test gate scenarios. It never connects.

`ssh [options] -G -- [user@]<host>` prints the settings ssh would use for the
host, as the real one does after it reads its config. The JSON file in
$FAKE_SSH_CONFIG says which Host aliases name another host:

- hosts: {alias: the HostName ssh connects to}; any other host names itself
- users: {user: {alias: HostName}}, as a Match user block, for a destination
  that names that user before an @; it comes before hosts
- fail: hosts whose config ssh can not read, which exit 255

Two options before -G change that, as they do for the real ssh: -F <file> reads
the JSON file it names in place of $FAKE_SSH_CONFIG, and -o HostName=<name> (or
-oHostName=<name>) names the host for any alias.

Each call's words go to $FAKE_SSH_LOG, one JSON list a line. Every other call
exits 255, as when ssh can not connect.
"""
import json
import os
import sys


def read_config(path):
    if path and os.path.exists(path):
        with open(path) as handle:
            return json.load(handle)
    return {}


def read_options(options):
    """(the config file -F names or None, the HostName -o sets or None), or None for any other option."""
    config_file, hostname, index = None, None, 0
    while index < len(options):
        option = options[index]
        if option in ("-F", "-o") and index + 1 < len(options):
            value, index = options[index + 1], index + 2
        elif option.startswith("-o") and len(option) > 2:
            value, index = option[2:], index + 1
        else:
            return None
        if option == "-F":
            config_file = value
        else:
            key, _equals, setting = value.partition("=")
            if key.lower() != "hostname" or not setting:
                return None
            hostname = setting
    return config_file, hostname


words = sys.argv[1:]
if os.environ.get("FAKE_SSH_LOG"):
    with open(os.environ["FAKE_SSH_LOG"], "a") as handle:
        handle.write(json.dumps(words) + "\n")
options = words[:-3]
read = read_options(options) if words[-3:-1] == ["-G", "--"] else None
config = read_config(read[0] or os.environ.get("FAKE_SSH_CONFIG")) if read else {}
user, _at, host = (words[-1:] or [""])[0].rpartition("@")
if read is None or host in config.get("fail", []):
    print("ssh: could not read the config", file=sys.stderr)
    sys.exit(255)
matched = config.get("users", {}).get(user, {})
hostname = read[1] or matched.get(host) or config.get("hosts", {}).get(host, host)
print(f"host {host}\nuser {user or 'local'}\nhostname {hostname}\nport 22")
