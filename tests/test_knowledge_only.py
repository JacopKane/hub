"""knowledge_only: a prompt that only shares material gets a reminder to explain, not act."""
from dialogs import prompt
from steps import and_, given, then, when

PASTE = "<pasted_content>" + "The release moves to Thursday. " * 20 + "</pasted_content>"


def test_a_pasted_page_with_no_ask_gets_a_reminder_to_explain_not_act(home):
    with given("knowledge_only is on"):
        home.install()
        home.hubctl("on", "knowledge_only")

    with when("Alex's prompt is mostly a pasted block, a link, or an image with \"fyi\""):
        pasted = prompt(home, "see " + PASTE)
        link = prompt(home, "https://example.com/release-notes")
        image = prompt(home, "fyi [Image #1]")
        marker = prompt(home, "btw the docs page moved, update your notes")
    with then("each prompt carries the reminder"):
        for result in (pasted, link, image, marker):
            assert "explain" in result.context.lower() and "knowledge_only" in result.context

    with when("Alex types an ask about the same page"):
        result = prompt(home, "Update the docs page from this: https://example.com/release-notes")
    with then("no reminder is added"):
        assert not result.context

    with when("the prompt comes from a task notification, another session or a slash command"):
        results = [prompt(home, "<task-notification>https://example.com/run/7</task-notification>"),
                   prompt(home, "Another Claude session sent a message: https://example.com/pr/4"),
                   prompt(home, "/review https://example.com/pr/4")]
    with then("no reminder is added"):
        assert [result.context for result in results] == ["", "", ""]

    with when("the user takes \"btw\" off the marker list"):
        home.hubctl("set", "knowledge_only.detect_shared_material.markers", "fyi", "side info", "just notifying")
        result = prompt(home, "btw the docs page moved, update your notes")
    with then("an ask with that word gets no reminder"):
        assert not result.context
    with and_("a marker still on the list works"):
        assert prompt(home, "side info: the docs page moved").context

    with when("the user turns the reminder off"):
        home.hubctl("off", "knowledge_only.reminder")
        result = prompt(home, "https://example.com/release-notes")
    with then("nothing is added"):
        assert not result.context
