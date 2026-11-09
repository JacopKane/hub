"""A small condition language for `default`, `relevant_if` and `offer_when`.

It is parsed by hand and never passed to eval. It knows dot names into the
profile (`profile.tools.cursor`), the facts in FACTS, numbers, quoted strings,
bare words as strings (`on`, `warn`), the comparisons, `and`, `or`, `not`,
`in`, parentheses and `x if condition else y`. A name followed by `(` is
refused, so a manifest can never call a function.
"""
import re

FACTS = {"tool", "mods_run", "claude_code_version", "repos_with_remote"}
KEYWORDS = {"and", "or", "not", "in", "if", "else"}
CONSTANTS = {"true": True, "false": False, "none": None}
COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}

TOKEN = re.compile(
    r"\s*(?:(?P<number>\d+(?:\.\d+)?(?![\w.]))|(?P<string>'[^']*'|\"[^\"]*\")"
    r"|(?P<op>==|!=|<=|>=|<|>|\(|\))|(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*))"
)
VERSION = re.compile(r"^\d+(?:\.\d+)+$")


class ConditionError(ValueError):
    pass


def tokenize(text):
    tokens, position = [], 0
    text = text.rstrip()
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match or match.end() == position:
            raise ConditionError(f"unexpected text at {text[position:position + 12]!r}")
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
        position = match.end()
    return tokens


class Parser:
    def __init__(self, text):
        self.tokens = tokenize(text)
        self.index = 0

    def peek(self, offset=0):
        spot = self.index + offset
        return self.tokens[spot] if spot < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.index += 1
        return token

    def word(self, value):
        kind, text = self.peek()
        return kind == "name" and text == value

    def expect_word(self, value):
        if not self.word(value):
            raise ConditionError(f"expected {value!r}")
        self.take()

    def parse(self):
        if not self.tokens:
            raise ConditionError("empty condition")
        tree = self.ternary()
        if self.index != len(self.tokens):
            raise ConditionError(f"unexpected {self.peek()[1]!r}")
        return tree

    def ternary(self):
        body = self.either()
        if self.word("if"):
            self.take()
            test = self.either()
            self.expect_word("else")
            return ("if", test, body, self.ternary())
        return body

    def either(self):
        left = self.both()
        while self.word("or"):
            self.take()
            left = ("or", left, self.both())
        return left

    def both(self):
        left = self.negation()
        while self.word("and"):
            self.take()
            left = ("and", left, self.negation())
        return left

    def negation(self):
        if self.word("not"):
            self.take()
            return ("not", self.negation())
        return self.comparison()

    def comparison(self):
        left = self.atom()
        kind, text = self.peek()
        if kind == "op" and text in COMPARISONS:
            self.take()
            return ("compare", text, left, self.atom())
        if self.word("in"):
            self.take()
            return ("compare", "in", left, self.atom())
        if self.word("not") and self.peek(1) == ("name", "in"):
            self.take()
            self.take()
            return ("compare", "not in", left, self.atom())
        return left

    def atom(self):
        kind, text = self.take()
        if kind == "number":
            return ("value", float(text) if "." in text else int(text))
        if kind == "string":
            return ("value", text[1:-1])
        if kind == "op" and text == "(":
            inner = self.ternary()
            if self.take() != ("op", ")"):
                raise ConditionError("missing ')'")
            return inner
        if kind == "name":
            if self.peek() == ("op", "("):
                raise ConditionError(f"{text!r} calls a function, which conditions can not do")
            if text in KEYWORDS:
                raise ConditionError(f"unexpected {text!r}")
            if text in CONSTANTS:
                return ("value", CONSTANTS[text])
            if text.startswith("profile."):
                return ("profile", text.split(".")[1:])
            if text in FACTS:
                return ("fact", text)
            if "." in text:
                raise ConditionError(f"unknown name {text!r}")
            return ("value", text)
        raise ConditionError(f"unexpected {text!r}" if text else "the condition ends too early")


def compile_condition(text):
    return Parser(text).parse()


def _order(value):
    if isinstance(value, str) and VERSION.match(value):
        return tuple(int(part) for part in value.split("."))
    return value


def _compare(operator, left, right):
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator in ("in", "not in"):
        try:
            found = left in right
        except TypeError:
            found = False
        return found if operator == "in" else not found
    if left is None or right is None:
        return False
    left, right = _order(left), _order(right)
    try:
        return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[operator]
    except TypeError:
        return False


def evaluate(tree, profile, facts):
    kind = tree[0]
    if kind == "value":
        return tree[1]
    if kind == "fact":
        return facts.get(tree[1])
    if kind == "profile":
        value = profile
        for key in tree[1]:
            value = value.get(key) if isinstance(value, dict) else None
        return value
    if kind == "not":
        return not evaluate(tree[1], profile, facts)
    if kind == "and":
        return bool(evaluate(tree[1], profile, facts)) and bool(evaluate(tree[2], profile, facts))
    if kind == "or":
        return bool(evaluate(tree[1], profile, facts)) or bool(evaluate(tree[2], profile, facts))
    if kind == "if":
        branch = tree[2] if evaluate(tree[1], profile, facts) else tree[3]
        return evaluate(branch, profile, facts)
    if kind == "compare":
        return _compare(tree[1], evaluate(tree[2], profile, facts), evaluate(tree[3], profile, facts))
    raise ConditionError(f"unknown node {kind}")
