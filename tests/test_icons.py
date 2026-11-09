"""Slice 13: hub's one table of glyphs.

Each glyph is one cell wide, has no emoji presentation, and has a plain word
and a label. Plain mode prints no glyph from the table.
"""
import json
import os
import unicodedata

from conftest import PLUGIN
from steps import and_, given, then

# The code points in the Basic Multilingual Plane that draw as an emoji by default (Emoji_Presentation=Yes).
EMOJI_DEFAULT = [(0x231A, 0x231B), (0x23E9, 0x23EC), (0x23F0, 0x23F0), (0x23F3, 0x23F3), (0x25FD, 0x25FE),
                 (0x2614, 0x2615), (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693), (0x26A1, 0x26A1),
                 (0x26AA, 0x26AB), (0x26BD, 0x26BE), (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4),
                 (0x26EA, 0x26EA), (0x26F2, 0x26F3), (0x26F5, 0x26F5), (0x26FA, 0x26FA), (0x26FD, 0x26FD),
                 (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C), (0x274E, 0x274E),
                 (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
                 (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55)]


def is_emoji_default(character):
    point = ord(character)
    return point >= 0x1F000 or any(low <= point <= high for low, high in EMOJI_DEFAULT)


def test_every_glyph_is_one_cell_wide_and_has_a_plain_word(home):
    with given("icons.json"):
        with open(os.path.join(PLUGIN, "lib", "hub", "icons.json"), encoding="utf-8") as handle:
            icons = json.load(handle)["icons"]
    with then("it holds the 15 glyphs, none is wide or full width, none has an emoji form, "
              "and each has a plain word and a label"):
        assert len(icons) == 15
        for icon in icons:
            for character in icon["glyph"]:
                assert unicodedata.east_asian_width(character) not in ("W", "F"), icon
                assert not is_emoji_default(character) and character != "️", icon
            assert icon["plain"].strip() and icon["label"].strip() and icon["meaning"].strip(), icon
    with and_("hubctl board --plain prints no glyph from the table"):
        home.install()
        home.hubctl("on", "chat_board")
        home.write_json(os.path.join(home.state, "board", "chats", "s1.json"),
                        {"session": "s1", "name": "API", "state": "needs_you", "since": 0,
                         "question": "Which retry limit?", "machine": "here"})
        plain = home.hubctl("board", "--plain").stdout
        symbols = {icon["glyph"] for icon in icons if not icon["glyph"].isascii()}
        assert "API" in plain and "needs you" in plain
        assert not [symbol for symbol in symbols if symbol in plain], plain
