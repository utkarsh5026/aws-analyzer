"""
The explorer's Everything tab: a line per queue, SNS topic and EventBridge rule in the region, with a search box, a
chip per kind (problems, queues, dead-letter queues, topics, rules) and columns that sort by a click, under the
region's cards and findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Next, _Note, _Table, _Text
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if

from ..analysis import (
    describe_pattern, queue_findings, queue_report_from, rule_findings, rule_report_from, topic_findings,
    topic_report_from,
)
from ..helpers import seconds_words
from ..models import Overview, Queue, Rule, Topic
from .frame import _item_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import MessagingExplorer


_ITEM_COLUMNS = (
    ("name", "Queue, topic or rule", "Sort by name"),
    ("traffic", "In", "Sort by what came in over the window: sent to a queue, published to a topic, matched by a rule"),
    ("failed", "Failed", "Sort by the deliveries that failed (topics and rules)"),
    ("waiting", "Waiting", "Sort by the messages waiting in a queue"),
    ("problems", "Problems", "Sort by how many warnings it has"),
)

_ITEM_CHIPS = (
    ("", "All", "all"),
    ("problems", "Problems", "warn"),
    ("queue", "Queues", "none"),
    ("dlq", "Dead-letter queues", "bad"),
    ("topic", "Topics", "none"),
    ("rule", "Rules", "none"),
)

_CHIP_TIPS = {
    "": "Show every queue, topic and rule",
    "problems": "Show only the ones with a warning",
    "queue": "Show only the queues that aren't dead-letter queues",
    "dlq": "Show only the dead-letter queues",
    "topic": "Show only the SNS topics",
    "rule": "Show only the EventBridge rules",
}

_ASCENDING_FIRST = ("name",)

_DEFAULT_SORT = "problems"

_NO_VALUE = -1

_TONE_RANK = {"bad": 2, "warn": 1, "ok": 0}

Finding = tuple[str, str]


@dataclass
class _Item:
    """
    A line of the Everything tab, and what the window opens: kind ('queue', 'dlq', 'topic' or 'rule'), its name as
    reports name it, its ARN, a few words about it, its numbers and its findings (warnings counted as problems).
    """

    kind: str
    name: str
    arn: str
    note: str
    traffic: int | None
    failed: int | None
    waiting: int | None
    findings: list[Finding]

    @property
    def key(self) -> str:
        return f"{self.opens}:{self.arn}"

    @property
    def opens(self) -> str:
        """
        What it opens as: a dead-letter queue is a queue.
        """
        return "queue" if self.kind == "dlq" else self.kind

    @property
    def problems(self) -> int:
        return sum(1 for level, _ in self.findings if level == "warn")

    @property
    def tone(self) -> str:
        if self.kind == "dlq" and self.waiting:
            return "bad"
        return "warn" if self.problems else "ok"


def _queue_item(overview: Overview, queue: Queue, prices: dict[str, float]) -> _Item:
    numbers = overview.queue_numbers.get(queue.arn)
    readers = ", ".join(c.function for c in queue.consumers)
    note = f"read by {readers}" if readers else ("catches failures from " + ", ".join(queue.sources)
                                                if queue.is_dlq else "nothing reads it")
    oldest = numbers.oldest_now_s if numbers else None
    if oldest:
        note += f" · oldest {seconds_words(oldest)}"
    findings = queue_findings(queue_report_from(overview, queue), prices)
    return _Item("dlq" if queue.is_dlq else "queue", queue.name, queue.arn, note,
                 numbers.sent if numbers else None, None, queue.messages if queue.is_dlq else queue.visible, findings)


def _topic_item(overview: Overview, topic: Topic, prices: dict[str, float]) -> _Item:
    numbers = overview.topic_numbers.get(topic.arn)
    targets = ", ".join(s.target for s in topic.subscriptions[:3]) or "no subscriptions"
    more = f" and {len(topic.subscriptions) - 3} more" if len(topic.subscriptions) > 3 else ""
    findings = topic_findings(topic_report_from(overview, topic), prices)
    return _Item("topic", topic.name, topic.arn, f"to {targets}{more}", numbers.published if numbers else None,
                 numbers.failed if numbers else None, None, findings)


def _rule_item(overview: Overview, rule: Rule) -> _Item:
    numbers = overview.rule_numbers.get(rule.arn)
    matches = f"schedule {rule.schedule}" if rule.schedule else describe_pattern(rule.pattern)
    state = "" if rule.is_enabled else "off · "
    findings = rule_findings(rule_report_from(overview, rule))
    return _Item("rule", rule.label, rule.arn, f"{state}{matches}", numbers.matched if numbers else None,
                 numbers.failed if numbers else None, None, findings)


def region_items(overview: Overview, prices: dict[str, float]) -> list[_Item]:
    """
    Every queue, topic and rule of the overview as a line, with its findings (rules AWS services manage for
    themselves left out, as the reports do).
    """
    items = [_queue_item(overview, queue, prices) for queue in overview.queues]
    items += [_topic_item(overview, topic, prices) for topic in overview.topics]
    items += [_rule_item(overview, rule) for rule in overview.rules if not rule.managed_by]
    return items


def _in_chip(item: _Item, chip: str) -> bool:
    if chip == "problems":
        return item.problems > 0
    if chip:
        return item.kind == chip
    return True


def _column_value(item: _Item, column: str) -> tuple[Any, ...]:
    """
    What a column sorts a line by; a number a line hasn't got sorts below every one that has it.
    """
    if column == "name":
        return (item.name.lower(),)
    if column == "problems":
        return (_TONE_RANK[item.tone], item.problems)
    value = getattr(item, column)
    return (_NO_VALUE if value is None else value,)


def _region_head(blocks: list[Any]) -> list[Any]:
    """
    overview()'s blocks the tab draws above its list: the title, cards, findings and notes. Its tables are the list,
    its next steps are what to click, and the commands under 'Let them in' are in the Overview tab of each one they
    fix.
    """
    return [b for b in blocks if not isinstance(b, (_Table, _Next, _Text))]


class _EverythingTab:
    """
    The Everything tab, a part of MessagingExplorer: what it keeps is `item_query` (the search box's text),
    `item_chip`, `item_sort` and `item_descending`, `items` (every line) and `items_shown`, the lines in the order
    drawn.
    """

    def _everything_page(self: MessagingExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.item_query, self.item_chip = "", ""
        self.item_sort, self.item_descending = _DEFAULT_SORT, True
        self.items: list[_Item] = []
        self.items_shown: list[_Item] = []
        self._item_rows: list[_Line] = []
        self.everything_view = w.HTML(layout=layout(width="100%"))
        self.item_find = w.Text(placeholder="Search: a queue, topic or rule, a function that reads one…",
                                continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.item_find.add_class("mgx-find")
        self.item_find.observe(self._safely(self._item_typed), names="value")
        clear = w.Button(description="✕", tooltip="Clear the search", layout=layout(flex="0 0 auto"))
        clear.add_class("mgx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.item_find, "value", "")))
        finder = w.HBox([self.item_find, clear], layout=layout(width="100%", align_items="center"))
        finder.add_class("mgx-filters")
        self.item_chips = w.HBox(layout=layout(width="100%"))
        self.item_chips.add_class("mgx-chips")
        self.item_head = w.HBox([w.HTML(), *self._item_column_buttons()], layout=layout(width="100%"))
        self.item_head.add_class("mgx-lhead")
        self.items_box = w.VBox([self.item_head], layout=layout(width="100%"))
        for name in ("mgx-machines", "mgx-items"):
            self.items_box.add_class(name)
        self.item_count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.item_count], layout=layout(width="100%"))
        count.add_class("mgx-pager")
        return w.VBox([self.everything_view, finder, self.item_chips, self.items_box, count])

    def _item_column_buttons(self: MessagingExplorer) -> list[Any]:
        w, layout = self._w, self._w.Layout
        self.column_buttons: dict[str, Any] = {}
        for key, label, tip in _ITEM_COLUMNS:
            button = w.Button(description=label, tooltip=tip, layout=layout(width="auto"))
            for name in ("mgx-col", f"mgx-c-{key}"):
                button.add_class(name)
            button.on_click(self._safely(lambda _button, key=key: self._sort_items_by(key)))
            self.column_buttons[key] = button
        return list(self.column_buttons.values())

    def _item_typed(self: MessagingExplorer, change: dict[str, Any]) -> None:
        self.item_query = str(change["new"] or "").strip()
        self._draw_item_rows()

    def _pick_item_chip(self: MessagingExplorer, chip: str) -> None:
        self.item_chip = "" if chip == self.item_chip else chip
        self._draw_item_chips()
        self._draw_item_rows()

    def _sort_items_by(self: MessagingExplorer, column: str) -> None:
        if column == self.item_sort:
            self.item_descending = not self.item_descending
        else:
            self.item_sort, self.item_descending = column, column not in _ASCENDING_FIRST
        self._draw_item_rows()

    def _draw_everything(self: MessagingExplorer) -> None:
        """
        The Everything tab: the region's cards, findings and notes, then the chips and the lines, searched, filtered
        and sorted.
        """
        overview = self.overview
        if overview is None:
            return
        self.items = region_items(overview, self.core.prices)
        blocks = self.ui._overview_blocks(overview)
        head = _region_head(blocks)
        self.shown["everything"] = head
        self._set(self.everything_view, self._html(head))
        self._draw_item_chips()
        self._draw_item_rows()

    def _draw_item_chips(self: MessagingExplorer) -> None:
        w, layout = self._w, self._w.Layout
        chips = []
        for key, label, tone in _ITEM_CHIPS if self.items else ():
            count = sum(1 for item in self.items if _in_chip(item, key))
            if key and not count:
                continue
            chip = w.Button(description=f"{label} {count:,}", tooltip=_CHIP_TIPS[key]
                            + (" (click again for all)" if key else ""), layout=layout(width="auto"))
            for name in ("mgx-chip", f"mgx-t-{tone}"):
                chip.add_class(name)
            _class_if(chip, "mgx-on", key == self.item_chip)
            chip.on_click(self._safely(lambda _button, key=key: self._pick_item_chip(key)))
            chips.append(chip)
        self.item_chips.children = chips

    def _shown_items(self: MessagingExplorer) -> list[_Item]:
        """
        The lines the search and the chip leave, in the column's order (by name first, so equal values stay A to Z).
        """
        items = [item for item in self.items if _in_chip(item, self.item_chip)]
        if self.item_query:
            items = [item for item in items if search_rank(self.item_query, (item.name, item.note)) is not None]
        items.sort(key=lambda item: item.name.lower())
        items.sort(key=lambda item: _column_value(item, self.item_sort), reverse=self.item_descending)
        return items

    def _draw_item_rows(self: MessagingExplorer) -> None:
        items = self._shown_items()
        self.items_shown = items
        self._draw_item_heads()
        while len(self._item_rows) < len(items):
            self._item_rows.append(_Line(self, self._clicked_item))
        words = self.item_query.split()
        for row, item in zip(self._item_rows, items):
            row.key = item.key
            self._set(row.face, _item_face(kind=item.kind, name=item.name, note=item.note, tone=item.tone,
                                           traffic=item.traffic, failed=item.failed, waiting=item.waiting,
                                           problems=item.problems, words=words))
            _class_if(row.box, "mgx-on", item.key == self.item_key)
        rows = [row.box for row in self._item_rows[:len(items)]]
        self.items_box.children = [self.item_head, *rows, *self._nothing_listed(items)]
        total = len(self.items)
        text = f"{total:,} queues, topics and rules" if len(items) == total else f"{len(items):,} of {total:,}"
        self._set(self.item_count, _esc(text) if total else "")

    def _draw_item_heads(self: MessagingExplorer) -> None:
        for key, button in self.column_buttons.items():
            _class_if(button, "mgx-on", key == self.item_sort)
            label = next(label for column, label, _ in _ITEM_COLUMNS if column == key)
            arrow = (" ▾" if self.item_descending else " ▴") if key == self.item_sort else ""
            if button.description != label + arrow:
                button.description = label + arrow

    def _nothing_listed(self: MessagingExplorer, items: list[_Item]) -> list[Any]:
        if items:
            return []
        if not self.items:
            return [self._w.HTML(self._html([_Note(
                f"No queues, topics or rules in {self.core.region}. They're regional: explore(region='us-west-2') "
                "looks in another region.")]))]
        what = f"matches {self.item_query!r}" if self.item_query else "is under this chip"
        return [self._w.HTML(f'<div class="mgx-empty">Nothing {_esc(what)}.</div>')]

    def _clicked_item(self: MessagingExplorer, line: _Line) -> None:
        if line.key:
            self._open_item(line.key, tab="overview")
