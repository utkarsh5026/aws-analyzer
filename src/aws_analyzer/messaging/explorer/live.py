"""
The explorer's Live tab: how many messages wait in a queue, looked at every few seconds while the tab is open, with
which way the backlog is going and when it would be empty, to watch one drain after a fix. It watches the queue open,
else the fullest few, and stops by itself after a while. Each look is one GetQueueAttributes request per queue.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..._kit.fmt import human_money
from ..._kit.text import _esc
from ..._kit.window import _class_if, _running_loop

from ..analysis import backlog_trend
from ..helpers import queue_url, seconds_words
from ..models import Queue, QueueSample

if TYPE_CHECKING:
    from .window import MessagingExplorer


_WATCH_SECONDS = 5

_WATCH_MINUTES = 10

_WATCH_MOST = 6

_KEPT_LOOKS = 60

_SECONDS_PER_HOUR = 3_600


_LOWEST_BAR = 15


def _bar_height(visible: int, low: int, high: int) -> int:
    """
    A look's bar, in percent: between the lowest and the highest look watched, so a change of a few messages in
    thousands still shows; the same height for all while nothing changes.
    """
    if high == low:
        return 100 if high else _LOWEST_BAR
    return round(_LOWEST_BAR + (100 - _LOWEST_BAR) * (visible - low) / (high - low))


def _spark(samples: list[QueueSample]) -> str:
    """
    The visible messages of each look as bars, from the lowest look to the highest.
    """
    values = [s.visible for s in samples]
    low, high = min(values, default=0), max(values, default=0)
    return "".join(f'<i style="height:{_bar_height(s.visible, low, high)}%" title="{s.visible:,} at '
                   f'{s.at:%H:%M:%S} UTC"></i>' for s in samples)


def _since_first(samples: list[QueueSample]) -> str:
    """
    Where the queue started: '(119 at the first look, 30 seconds ago)', or '' after one look.
    """
    if len(samples) < 2:
        return ""
    ago = seconds_words((samples[-1].at - samples[0].at).total_seconds())
    return f" ({samples[0].visible:,} at the first look, {ago} ago)"


def _queue_card(queue: Queue, samples: list[QueueSample]) -> str:
    """
    A queue as Live shows it: its name, which way it's going, the looks as bars and the counts of the last one.
    """
    trend = backlog_trend(samples)
    tone = "ok" if trend.per_minute is not None and trend.per_minute < 0 else ""
    tone = "warn" if trend.per_minute is not None and trend.per_minute > 0 else tone
    last = samples[-1] if samples else QueueSample(datetime.now(timezone.utc), queue.visible, queue.in_flight,
                                                     queue.delayed)
    return (f'<div class="lv"><div class="lh"><b>{_esc(queue.name)}</b><span class="lt {tone}">'
            f'{_esc(trend.words)}</span></div><div class="spark">{_spark(samples)}</div>'
            f'<div class="ln">now {last.visible:,} waiting{_esc(_since_first(samples))} · {last.in_flight:,} in '
            f'flight · {last.delayed:,} delayed · {len(samples):,} looks</div></div>')


class _LiveTab:
    """
    The Live tab, a part of MessagingExplorer: `looks` holds each watched queue's looks (by ARN), `watched` the
    queues watched, and `watching` whether it's looking.
    """

    def _live_page(self: MessagingExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.looks: dict[str, list[QueueSample]] = {}
        self.watched: list[Queue] = []
        self.watching = False
        self._watch_token = 0
        self._watch_started = 0.0
        self.watch_button = w.Button(description="● Watch", tooltip=f"Look every {_WATCH_SECONDS} seconds (stops "
                                     f"after {_WATCH_MINUTES} minutes, or when you leave the tab)",
                                     layout=layout(width="auto"))
        self.watch_button.add_class("mgx-small")
        self.watch_button.on_click(self._safely(lambda _button: self._toggle_watch()))
        self.live_note = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        bar = w.HBox([self.watch_button, self.live_note], layout=layout(width="100%"))
        bar.add_class("mgx-bar")
        self.live_view = w.HTML(layout=layout(width="100%"))
        return w.VBox([bar, self.live_view])

    def _queues_to_watch(self: MessagingExplorer) -> list[Queue]:
        """
        The queue open, else the fullest few that hold messages (dead-letter queues too).
        """
        overview = self.overview
        if overview is None:
            return []
        kind, _, arn = self.item_key.partition(":")
        opened = overview.queue(arn) if kind == "queue" else None
        if opened is not None:
            return [opened]
        holding = sorted((q for q in overview.queues if q.messages), key=lambda q: -q.messages)
        return holding[:_WATCH_MOST]

    def _toggle_watch(self: MessagingExplorer) -> None:
        if self.watching:
            self._stop_watch("Live stopped: click Watch to look again.")
        else:
            self._start_watch()

    def _start_watch(self: MessagingExplorer) -> None:
        """
        Watches: a look now, then one every _WATCH_SECONDS on the notebook's loop (just the one look without a loop: a
        script, the tests).
        """
        queues = self._queues_to_watch()
        if [q.arn for q in queues] != [q.arn for q in self.watched]:
            self.looks = {}
        self.watched = queues
        if not queues:
            self._draw_live()
            self._status("No queue holds messages to watch: open one in the Everything tab, then come back.")
            return
        self.watching, self._watch_started = True, time.monotonic()
        self._watch_token += 1
        self._mark_watching(True)
        self._draw_live()
        loop = _running_loop()
        if loop is None:
            self._got_looks(self._look())
            return
        self._tasks["watch"] = loop.create_task(self._watch_loop(self._watch_token))

    def _stop_watch(self: MessagingExplorer, text: str = "") -> None:
        was = self.watching
        self.watching = False
        self._watch_token += 1
        self._mark_watching(False)
        self._draw_live()
        if was and text:
            self._status(text)

    def _mark_watching(self: MessagingExplorer, on: bool) -> None:
        _class_if(self.watch_button, "mgx-on", on)
        _class_if(self.tab_buttons["live"], "mgx-onair", on)
        label = "■ Stop" if on else "● Watch"
        if self.watch_button.description != label:
            self.watch_button.description = label

    def _look(self) -> list[Queue]:
        """
        One look at every queue watched, read on a worker thread: their counts now (GetQueueAttributes).
        """
        return [self.core.read_queue(queue.url or queue_url(queue.arn)) for queue in self.watched]

    def _got_looks(self: MessagingExplorer, queues: list[Queue]) -> None:
        now = datetime.now(timezone.utc)
        for queue in queues:
            kept = self.looks.setdefault(queue.arn, [])
            kept.append(QueueSample(now, queue.visible, queue.in_flight, queue.delayed))
            del kept[:-_KEPT_LOOKS]
        self._draw_live()

    async def _watch_loop(self: MessagingExplorer, token: int) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self.watching and self._watch_token == token:
                try:
                    queues = await loop.run_in_executor(self._workers(), self._look)
                except Exception as exc:  # noqa: BLE001
                    if self._watch_token == token:
                        self._stop_watch(f"Live stopped: {self._error_text(exc)}")
                    return
                if self._watch_token != token:
                    return
                self._got_looks(queues)
                if time.monotonic() - self._watch_started > _WATCH_MINUTES * 60:
                    self._stop_watch(f"Live stopped after {_WATCH_MINUTES} minutes: click Watch to look again.")
                    return
                await asyncio.sleep(_WATCH_SECONDS)
        finally:
            if self._tasks.get("watch") is asyncio.current_task():
                del self._tasks["watch"]

    def _draw_live(self: MessagingExplorer) -> None:
        queues = self.watched
        price = self.core.prices["sqs_request"]
        hourly = len(queues) * _SECONDS_PER_HOUR / _WATCH_SECONDS * price
        state = f"looking every {_WATCH_SECONDS} seconds" if self.watching else "stopped"
        what = ", ".join(q.name for q in queues) or "nothing yet"
        self._set(self.live_note, f'<span class="mgx-label">{_esc(state)}</span> {_esc(what)} · each look is one '
                                  f'request per queue (about {_esc(human_money(hourly))} an hour)')
        cards = "".join(_queue_card(q, self.looks.get(q.arn, [])) for q in queues)
        empty = ('<div class="mgx-empty">Live watches the queue open, or the fullest queues that hold messages, '
                 "while this tab is open.</div>")
        self._set(self.live_view, cards or empty)
