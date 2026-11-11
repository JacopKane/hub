"""The part's texts, read from text/ with their {values} filled in."""
import os

TEXT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "text")


def text(name, **values):
    with open(os.path.join(TEXT_DIR, name), encoding="utf-8") as handle:
        found = handle.read().strip()
    for key, value in values.items():
        found = found.replace("{" + key + "}", str(value))
    return found
