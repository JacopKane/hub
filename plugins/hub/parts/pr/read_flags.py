"""Flag records for a working folder, read without applying hub.

hub.resolve.current applies hub first when resolved.json is missing or
stale, and an apply writes state files and links. pr-check and pr-night do
not apply hub, so they resolve the same scopes in memory instead, first the
user's settings and then the repo's .hub files. With no settings, every
flag has its default. Reading the user's settings still moves them from
the folder's old name on first use, as every hub step does
(files.move_old_config).
"""
from hub import files, resolve, settings, tree as tree_module


def flags(cwd):
    """{flag id: record} for Claude Code, as resolve.current would give them."""
    tree = tree_module.load(files.PARTS_DIR)
    offers = files.read_json(files.state_path("offers.json"), {"offers": []})
    offered = {offer["id"] for offer in offers["offers"]}
    return resolve.resolve(tree, settings.layers(cwd), resolve.load_profile(), offered)["claude_code"]["flags"]
