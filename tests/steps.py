"""Named steps for behavior scenarios.

    with given("hub is installed in a fresh HOME"):
        home.install()

Each step prints its title, and a failure inside it carries the title, so a
failed scenario says which step broke.
"""
import contextlib


@contextlib.contextmanager
def _step(word, title):
    print(f"{word} {title}")
    try:
        yield
    except Exception as error:
        note = f"in step: {word} {title}"
        if hasattr(error, "add_note"):
            error.add_note(note)
        else:  # Python before 3.11
            error.args = (*error.args, note)
        raise


def given(title):
    return _step("given", title)


def when(title):
    return _step("when", title)


def then(title):
    return _step("then", title)


def and_(title):
    return _step("and", title)
