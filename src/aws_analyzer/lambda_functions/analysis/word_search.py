"""
Words to search a function's logs for, joined with AND, OR and NOT: `KeyError OR Timeout`, `PutItem -retrying`,
`(AccessDenied OR Throttling) AND NOT retrying`. The operators are written in capitals (and `-word` leaves a word
out), so a pasted error message such as `No such file or directory` stays one phrase; words next to each other are a
phrase, and quotes keep one together: `"Task timed out" OR KeyError`.

CloudWatch matches one line at a time, so a search is checked twice: CloudWatch is asked for the lines with any of
the wanted words (WordSearch.pattern), and the whole search is then asked of each run's lines (WordSearch.matches),
where an AND can hold over two lines of the same run. Nothing in it is ever run as code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Union

from ..models import LogEvent
from .errors import _phrase
from .logs import split_runs
from .query import AllOf, AnyOf, Not


class SearchError(ValueError):
    """
    Words with AND, OR or NOT that can't be read: says what's wrong, and shows a search that works.
    """


SEARCH_EXAMPLE = "KeyError OR Timeout"

AND, OR, NOT = "AND", "OR", "NOT"

_TOKEN_RE = re.compile(r'\s*(?:(?P<open>\()|(?P<quoted>-?"[^"]*")|(?P<word>[^\s"]+|"))')

_EXCLUDED_WORD_RE = re.compile(r"^-\w")

_FILTER_PATTERN_STARTS = ("{", "[", "?")

_NEEDS_QUOTES_RE = re.compile(r'[\s()]|^-')


@dataclass
class Phrase:
    """
    Text a line must hold (exactly, in the case CloudWatch matches it).
    """

    text: str


SearchNode = Union[Phrase, Not, AllOf, AnyOf]


@dataclass
class _SearchToken:
    kind: str
    text: str = ""
    quoted: bool = False


def _closing_brackets(word: str) -> tuple[str, int]:
    """
    A word with the brackets that close a group taken off its end: 'KeyError))' -> ('KeyError', 2). A word's own
    brackets stay, so 'handler(event)' is one word.
    """
    closing = 0
    while word.endswith(")") and word.count(")") > word.count("("):
        word = word[:-1]
        closing += 1
    return word, closing


def _word_token(word: str) -> _SearchToken:
    if word in (AND, OR, NOT):
        return _SearchToken(word)
    if _EXCLUDED_WORD_RE.match(word):
        return _SearchToken("excluded", word[1:])
    return _SearchToken("word", word)


def _search_tokens(text: str) -> list[_SearchToken]:
    """
    The search's words, operators and brackets, in order. A quoted phrase is one token, and a `-` before it or a
    word leaves it out.
    """
    found: list[_SearchToken] = []
    for match in _TOKEN_RE.finditer(text):
        kind = match.lastgroup
        if kind == "open":
            found.append(_SearchToken("("))
            continue
        raw = match.group(kind)
        if kind == "quoted":
            kind = "excluded" if raw.startswith("-") else "word"
            found.append(_SearchToken(kind, raw.lstrip("-")[1:-1], quoted=True))
            continue
        word, closing = _closing_brackets(raw)
        if word:
            found.append(_word_token(word))
        found += [_SearchToken(")") for _ in range(closing)]
    return found


def _uses_operators(tokens: list[_SearchToken]) -> bool:
    return any(token.kind in (AND, OR, NOT, "excluded") for token in tokens)


class _SearchParser:
    """
    Reads tokens into a tree: OR binds loosest, then AND (written, or two parts side by side), then NOT; brackets
    group, and bare words side by side are one phrase.
    """

    def __init__(self, text: str, tokens: list[_SearchToken]):
        self.text = text
        self.tokens = tokens
        self.at = 0

    def peek(self) -> _SearchToken | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self) -> _SearchToken:
        token = self.peek()
        if token is None:
            last = self.tokens[-1].kind if self.tokens else ""
            raise SearchError(f"{last} at the end needs a word after it, as in {SEARCH_EXAMPLE!r}")
        self.at += 1
        return token

    def search(self) -> SearchNode:
        node = self.any_of()
        leftover = self.peek()
        if leftover is not None:
            raise SearchError(f"A ')' in {self.text!r} has no '(' before it; take it out, or quote the words that hold "
                              'it: "handler(event)"')
        return node

    def any_of(self) -> SearchNode:
        parts = [self.all_of()]
        while self._next_is(OR):
            self.take()
            parts.append(self.all_of())
        return parts[0] if len(parts) == 1 else AnyOf(parts)

    def all_of(self) -> SearchNode:
        parts = [self.negated()]
        while self._next_is(AND) or self._starts_a_part():
            if self._next_is(AND):
                self.take()
            parts.append(self.negated())
        return parts[0] if len(parts) == 1 else AllOf(parts)

    def negated(self) -> SearchNode:
        if self._next_is(NOT):
            self.take()
            return Not(self.negated())
        return self.atom()

    def atom(self) -> SearchNode:
        token = self.take()
        if token.kind == "(":
            inner = self.any_of()
            if not self._next_is(")"):
                raise SearchError(f"A '(' in {self.text!r} isn't closed: add the ')'")
            self.take()
            return inner
        if token.kind == "excluded":
            return Not(self._phrase(token.text))
        if token.kind == "word":
            return self._phrase(self._joined_words(token))
        raise SearchError(f"{token.kind or token.text!r} needs a word before it, as in {SEARCH_EXAMPLE!r}")

    def _joined_words(self, first: _SearchToken) -> str:
        words = [first.text]
        while not first.quoted and (token := self.peek()) is not None and token.kind == "word" and not token.quoted:
            words.append(self.take().text)
        return " ".join(words)

    def _phrase(self, text: str) -> Phrase:
        if not text:
            raise SearchError(f'Empty quotes ("") in {self.text!r}: put the words to look for between them')
        return Phrase(text)

    def _next_is(self, kind: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == kind

    def _starts_a_part(self) -> bool:
        token = self.peek()
        return token is not None and token.kind in ("word", "excluded", "(", NOT)


def _phrases_of(node: SearchNode, *, wanted: bool) -> list[str]:
    """
    The phrases the search looks for (wanted=True) or leaves out (False), in order.
    """
    if isinstance(node, Phrase):
        return [node.text] if wanted else []
    if isinstance(node, Not):
        return _phrases_of(node.inner, wanted=not wanted)
    return [text for part in node.parts for text in _phrases_of(part, wanted=wanted)]


def _search_holds(node: SearchNode, texts: list[str]) -> bool:
    if isinstance(node, Phrase):
        return any(node.text in text for text in texts)
    if isinstance(node, Not):
        return not _search_holds(node.inner, texts)
    if isinstance(node, AllOf):
        return all(_search_holds(part, texts) for part in node.parts)
    return any(_search_holds(part, texts) for part in node.parts)


def _folded(node: SearchNode) -> SearchNode:
    if isinstance(node, Phrase):
        return Phrase(node.text.lower())
    if isinstance(node, Not):
        return Not(_folded(node.inner))
    return type(node)([_folded(part) for part in node.parts])


def _quoted(text: str) -> str:
    return f'"{text}"' if _NEEDS_QUOTES_RE.search(text) or text in (AND, OR, NOT) else text


def _written(node: SearchNode, *, inside: bool = False) -> str:
    if isinstance(node, Phrase):
        return _quoted(node.text)
    if isinstance(node, Not):
        return f"{NOT} {_written(node.inner, inside=True)}"
    joiner = f" {AND} " if isinstance(node, AllOf) else f" {OR} "
    text = joiner.join(_written(part, inside=True) for part in node.parts)
    return f"({text})" if inside else text


def _cloudwatch_term(text: str) -> str:
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


@dataclass
class SearchTerms:
    """
    A search of one shape, which chips can draw and change: the wanted phrases joined by one joiner (AND or OR), and
    the phrases left out. text() writes it back as a search.
    """

    joiner: str
    wanted: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)

    def text(self) -> str:
        """
        The search these terms are, as it's typed: 'KeyError OR Timeout', '(a OR b) AND NOT c'.
        """
        wanted = f" {self.joiner} ".join(_quoted(text) for text in self.wanted)
        if self.joiner == OR and len(self.wanted) > 1 and self.left_out:
            wanted = f"({wanted})"
        parts = [wanted] if wanted else []
        parts += [f"{NOT} {_quoted(text)}" for text in self.left_out]
        return f" {AND} ".join(parts)

    def flipped(self) -> SearchTerms:
        """
        The same terms with the other joiner: AND for OR, OR for AND.
        """
        return SearchTerms(OR if self.joiner == AND else AND, list(self.wanted), list(self.left_out))

    def toggled(self, phrase: str) -> SearchTerms:
        """
        The phrase moved from wanted to left out, or back.
        """
        if phrase in self.wanted:
            return SearchTerms(self.joiner, [t for t in self.wanted if t != phrase], [*self.left_out, phrase])
        return SearchTerms(self.joiner, [*self.wanted, phrase], [t for t in self.left_out if t != phrase])

    def without(self, phrase: str) -> SearchTerms:
        """
        The terms with the phrase taken out.
        """
        return SearchTerms(self.joiner, [t for t in self.wanted if t != phrase],
                           [t for t in self.left_out if t != phrase])


def _left_out_phrase(node: SearchNode) -> str | None:
    if isinstance(node, Not) and isinstance(node.inner, Phrase):
        return node.inner.text
    return None


def _wanted_group(node: SearchNode) -> tuple[str, list[str]] | None:
    """
    (joiner, phrases) when the node is a phrase, or phrases joined by OR; else None.
    """
    if isinstance(node, Phrase):
        return AND, [node.text]
    if isinstance(node, AnyOf) and all(isinstance(part, Phrase) for part in node.parts):
        return OR, [part.text for part in node.parts]
    return None


def _terms_of(node: SearchNode) -> SearchTerms | None:
    """
    The search as SearchTerms when it has their shape: phrases joined by AND or by OR, with phrases left out.
    """
    parts = node.parts if isinstance(node, AllOf) else [node]
    left_out = [text for text in map(_left_out_phrase, parts) if text is not None]
    rest = [part for part in parts if _left_out_phrase(part) is None]
    if all(isinstance(part, Phrase) for part in rest):
        return SearchTerms(AND, [part.text for part in rest], left_out)
    group = _wanted_group(rest[0]) if len(rest) == 1 else None
    if group is None:
        return None
    return SearchTerms(group[0], group[1], left_out)


@dataclass
class WordSearch:
    """
    Words joined with AND, OR and NOT, parsed: .matches(lines) says whether a run's lines hold it, .pattern() is what
    CloudWatch is asked for, and .terms() the search as chips when it has their shape.
    """

    text: str
    tree: SearchNode

    @property
    def wanted(self) -> list[str]:
        """
        The phrases it looks for, in order, once each (what a line is marked for).
        """
        return list(dict.fromkeys(_phrases_of(self.tree, wanted=True)))

    @property
    def left_out(self) -> list[str]:
        """
        The phrases a run mustn't have, in order, once each.
        """
        return list(dict.fromkeys(_phrases_of(self.tree, wanted=False)))

    def matches(self, texts: Iterable[str], *, ignore_case: bool = False) -> bool:
        """
        Whether these texts (a run's lines) hold the search: each phrase is looked for in every text, so an AND can
        hold over two lines. Case-sensitive, as CloudWatch is, unless ignore_case.
        """
        if ignore_case:
            return _search_holds(_folded(self.tree), [text.lower() for text in texts])
        return _search_holds(self.tree, list(texts))

    def mentions(self, text: str) -> bool:
        """
        Whether a line holds one of the phrases the search looks for.
        """
        return any(phrase in text for phrase in self.wanted)

    def pattern(self) -> str | None:
        """
        The CloudWatch Logs filter pattern for the lines that may belong to a matching run: any of the wanted
        phrases ('?"KeyError" ?"Timeout"'). None when it only leaves words out: every line is read then.
        """
        wanted = self.wanted
        if len(wanted) == 1:
            return _cloudwatch_term(wanted[0])
        return " ".join(f"?{_cloudwatch_term(text)}" for text in wanted) or None

    def written(self) -> str:
        """
        The search written out in full, operators in capitals and groups in brackets: 'KeyError OR Timeout'.
        """
        return _written(self.tree)

    def terms(self) -> SearchTerms | None:
        """
        The search as chips (SearchTerms), or None when it's grouped in a way chips can't draw
        ('(a OR b) AND (c OR d)').
        """
        return _terms_of(self.tree)


def is_filter_pattern(text: str) -> bool:
    """
    Whether a search is a CloudWatch Logs filter pattern of its own ('{ $.level = "ERROR" }', '?ERROR ?WARN'),
    passed to CloudWatch as it is.
    """
    stripped = text.strip()
    return stripped.startswith(_FILTER_PATTERN_STARTS) or " ?" in stripped


def word_search(text: str | None) -> WordSearch | None:
    """
    'KeyError OR Timeout' -> WordSearch. None for text with no AND, OR, NOT or -word (one phrase, searched as it
    is), for a CloudWatch filter pattern, and for nothing. Raises SearchError, which says what's wrong.
    """
    cleaned = str(text or "").strip()
    if not cleaned or is_filter_pattern(cleaned):
        return None
    tokens = _search_tokens(cleaned)
    if not _uses_operators(tokens):
        return None
    if cleaned.count('"') % 2:
        raise SearchError(f"A quote in {cleaned!r} isn't closed: add the other \" (quotes keep a phrase together)")
    return WordSearch(cleaned, _SearchParser(cleaned, tokens).search())


def runs_matching(events: Iterable[LogEvent], search: WordSearch) -> list[LogEvent]:
    """
    The lines of the runs whose lines hold the search, oldest first, each line with a wanted phrase marked. When a
    run holds it with no such line (`a OR NOT b`), no line is marked, so every run kept still shows.
    """
    kept = [run for run in split_runs(events) if search.matches(event.message for event in run.events)]
    lines = sorted((event for run in kept for event in run.events), key=lambda event: event.time)
    every_run_marked = all(any(search.mentions(event.message) for event in run.events) for run in kept)
    for event in lines:
        event.matched = every_run_marked and search.mentions(event.message)
    return lines


def search_hits(hits: list[LogEvent], search: str | None, words: WordSearch | None) -> list[LogEvent]:
    """
    The lines a CloudWatch search found that do hold what was searched for (a wanted phrase, or the plain search's
    phrase), checked again here for log stores that only roughly apply filter patterns.
    """
    if words is not None:
        return [event for event in hits if words.mentions(event.message)]
    phrase = _phrase(search)
    return [event for event in hits if phrase in event.message] if phrase else hits
