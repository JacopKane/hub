"""question_check: a question dialog opens only when it carries what the user needs to answer it."""
from dialogs import FULL_OPTIONS, FULL_QUESTION, ask, question
from steps import and_, given, then, when

BARE = [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}]


def test_a_question_without_context_is_refused_and_a_full_one_opens(home):
    with given("question_check is on"):
        home.install()
        home.hubctl("on", "question_check")

    with when("a chat opens a dialog whose question has 40 characters and whose options are bare"):
        result = ask(home, [question("Should the page use the new tree layout?", BARE)])
    with then("it is refused, and the reason names the short parts and what to add"):
        assert result.refused
        assert "question 1" in result.reason and 'option "Yes"' in result.reason and 'option "No"' in result.reason
        assert "context" in result.reason and "example" in result.reason

    with when("every part is there"):
        result = ask(home, [question()])
    with then("it opens with no note"):
        assert result.ran and not result.context

    with when("the question is a little under the minimum, within the near miss"):
        short = FULL_QUESTION[:240]
        result = ask(home, [question(short)])
    with then("it opens, with one soft note that names the short parts"):
        assert result.ran
        assert "question 1 (240 of 250 characters)" in result.context
        assert result.context.count("question_check") == 1

    with when("an option names an item only by a code, such as \"item 8a\""):
        options = [dict(FULL_OPTIONS[0]), dict(FULL_OPTIONS[1], label="Do item 8a first")]
        result = ask(home, [question(options=options)])
    with then("it is refused, and the reason quotes the code"):
        assert result.refused and '"item 8a"' in result.reason
    with and_("a code in brackets after a plain name is fine"):
        options = [dict(FULL_OPTIONS[0]), dict(FULL_OPTIONS[1], label="Revoke on disconnect (R7)")]
        assert ask(home, [question(options=options)]).ran

    with when("a question is about code and has no path"):
        no_path = FULL_QUESTION.replace("in plugins/web/settings.py", "in the settings code")
        result = ask(home, [question(no_path)])
    with then("it is refused, and the reason asks for a code line with its path"):
        assert result.refused and "path" in result.reason
    with when("the user turns off question_check.paths, and the same question comes again"):
        home.hubctl("off", "question_check.paths")
        result = ask(home, [question(no_path)])
    with then("it opens"):
        assert result.ran

    with when("the user turns question_check off"):
        home.hubctl("off", "question_check")
        result = ask(home, [question("Ok?", BARE)])
    with then("even a bare dialog opens"):
        assert result.ran and not result.context


PRODUCT_QUESTION = (
    "The settings page lists every part as a tree today, and the new change strip needs a place on it. For example, "
    "after a switch the strip shows one line with an undo button above the tree, so the user sees what changed "
    "without scrolling. Which layout should the page use from now on?"
)


def test_a_question_about_the_product_needs_no_code_path(home):
    with given("question_check is on"):
        home.install()
        home.hubctl("on", "question_check")
    with when("a long question with an example but no code and no path is asked"):
        assert len(PRODUCT_QUESTION) >= 250
        result = ask(home, [question(PRODUCT_QUESTION)])
    with then("it opens"):
        assert result.ran and not result.refused


def test_the_option_order_never_refuses_a_question(home):
    with given("question_check is on"):
        home.install()
        home.hubctl("on", "question_check")
    with when("a Skip option comes first and the recommended one second"):
        skip = {"label": "Skip", "description": "Leave the layout as it is and ask again at the next release."}
        result = ask(home, [question(PRODUCT_QUESTION, options=[skip] + [dict(option) for option in FULL_OPTIONS])])
    with then("it opens"):
        assert result.ran and not result.refused
    with when("the recommended option is not first"):
        result = ask(home, [question(PRODUCT_QUESTION, options=list(reversed(FULL_OPTIONS)))])
    with then("it still opens"):
        assert result.ran and not result.refused


def test_a_question_that_names_code_without_a_path_is_still_refused(home):
    with given("question_check is on"):
        home.install()
        home.hubctl("on", "question_check")
    with when("the question names a function in backticks and gives no path"):
        about_code = PRODUCT_QUESTION.replace("the new change strip", "the new change strip, drawn by `render_strip`,")
        result = ask(home, [question(about_code)])
    with then("it is refused, and the reason asks for the path"):
        assert result.refused and "path" in result.reason
