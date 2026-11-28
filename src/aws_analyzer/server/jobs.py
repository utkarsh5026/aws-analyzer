"""
Commands run as jobs: the page starts one (POST /api/jobs), gets its ID at once, and follows what it does as events
(GET /api/jobs/<id>/events, Server-Sent Events) while the rest of the page keeps working: its progress (the View's
_progress ticks: what it's reading, how many, of how many), each report it shows, notes (stopping), and done. Stop
(DELETE /api/jobs/<id>) raises JobStopped from the job's next progress tick, the way the notebook's stop button
raises KeyboardInterrupt there, so a long scan ends where it is and says what it read.

A job's events are kept, numbered from 1, so a page that opens the stream late, or reconnects (an EventSource sends
Last-Event-ID), gets every event it missed. Finished jobs are kept for JOBS_KEPT seconds, at most JOBS_HELD of them.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from collections.abc import Callable, Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .._kit.fmt import _utcnow

JOB_WORKERS = 8
JOBS_KEPT = 15 * 60
JOBS_HELD = 50
PROGRESS_EVERY = 0.25
RUNNING, DONE, FAILED, STOPPED = "running", "done", "failed", "stopped"


class JobStopped(BaseException):
    """
    The page's Stop, raised from the job's next progress tick. A BaseException, like KeyboardInterrupt, so a
    command's guard (@_friendly_errors catches Exception) lets it through and a threaded scan cancels its workers.
    """


@dataclass(frozen=True)
class JobEvent:
    """
    One thing a job did: its number (from 1), its kind ('progress', 'report', 'note' or 'done') and what it says.
    """

    number: int
    kind: str
    data: dict[str, Any]


@dataclass
class _Listener:
    loop: asyncio.AbstractEventLoop
    woken: asyncio.Event


@dataclass
class Job:
    """
    A command running for the page: what it runs (service, command, the call as written in a notebook, its
    arguments as form texts), when it started, its state (running, done, failed or stopped), its events and the
    Event that Stop sets.
    """

    service: str
    command: str
    call: str
    arguments: dict[str, str]
    id: str = field(default_factory=lambda: secrets.token_urlsafe(9))
    started_at: str = field(default_factory=lambda: _utcnow().isoformat().replace("+00:00", "Z"))
    state: str = RUNNING
    seconds: float = 0.0
    stop: threading.Event = field(default_factory=threading.Event)
    _started: float = field(default_factory=time.monotonic)
    _ended: float | None = None
    _events: list[JobEvent] = field(default_factory=list)
    _progress: dict[str, Any] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _listeners: list[_Listener] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.state == RUNNING

    def add(self, kind: str, data: dict[str, Any]) -> JobEvent:
        """
        Records an event and wakes every stream following the job (on its own event loop).
        """
        with self._lock:
            event = JobEvent(len(self._events) + 1, kind, data)
            self._events.append(event)
            if kind == "progress":
                self._progress = data
            listeners = list(self._listeners)
        for listener in listeners:
            listener.loop.call_soon_threadsafe(listener.woken.set)
        return event

    def events_after(self, number: int) -> list[JobEvent]:
        with self._lock:
            return self._events[max(0, number):]

    def finish(self, state: str) -> None:
        """
        Ends the job in `state` and says so as its last event, 'done'.
        """
        self.state = state
        self._ended = time.monotonic()
        self.seconds = round(self._ended - self._started, 2)
        self.add("done", {"state": state, "seconds": self.seconds})

    def ended_before(self, moment: float) -> bool:
        return self._ended is not None and self._ended < moment

    @contextmanager
    def listening(self) -> Iterator[asyncio.Event]:
        """
        An asyncio.Event on the running loop that's set whenever the job adds an event, for as long as the stream
        follows it.
        """
        listener = _Listener(asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._listeners.append(listener)
        try:
            yield listener.woken
        finally:
            with self._lock:
                self._listeners.remove(listener)

    def summary(self) -> dict[str, Any]:
        """
        The job as the page lists it: what it runs, its state, how long it took (so far), its newest progress and how
        many events it has.
        """
        seconds = self.seconds if not self.is_running else round(time.monotonic() - self._started, 2)
        with self._lock:
            progress, count = self._progress, len(self._events)
        return {"id": self.id, "service": self.service, "command": self.command, "call": self.call,
                "arguments": self.arguments, "started_at": self.started_at, "state": self.state,
                "seconds": seconds, "progress": progress, "events": count, "stopping": self.stop.is_set()}


class _ProgressSender:
    """
    What a job's View._progress yields as tick while it runs: each tick checks Stop first (raising JobStopped), then
    sends the count as a progress event, at most every PROGRESS_EVERY seconds unless the total changed.
    """

    def __init__(self, job: Job, label: str, unit: str):
        self.job, self.label, self.unit = job, label, unit
        self.started = time.monotonic()
        self.sent_at = 0.0
        self.total: int | None = None

    def tick(self, count: int, total: int | None = None) -> None:
        if self.job.stop.is_set():
            raise JobStopped
        now = time.monotonic()
        is_new_total = total != self.total
        if is_new_total:
            self.started, self.total = now, total
        if not is_new_total and now - self.sent_at < PROGRESS_EVERY:
            return
        self.sent_at = now
        self.job.add("progress", {"label": self.label, "unit": self.unit, "count": count, "total": total,
                                  "seconds": round(now - self.started, 2)})


def job_progress(job: Job, default: tuple[str, str]) -> Callable[..., Any]:
    """
    What a View's _progress becomes while it runs a job: progress(label, unit) gives a tick that sends the job's
    progress and stops it when Stop was pressed. `default` is the View's _PROGRESS, said when no label is given.
    """

    @contextmanager
    def progress(label: str | None = None, unit: str | None = None) -> Generator[Callable[..., None], None, None]:
        sender = _ProgressSender(job, default[0] if label is None else label, default[1] if unit is None else unit)
        yield sender.tick

    return progress


class JobBoard:
    """
    The jobs this server runs: each on one of JOB_WORKERS threads, kept until JOBS_KEPT seconds after it ended (at
    most JOBS_HELD finished ones).
    """

    def __init__(self, workers: int = JOB_WORKERS):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aws-analyzer-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, job: Job, work: Callable[[Job], None]) -> Job:
        """
        Keeps the job and runs work(job) on a worker thread; work must end it (job.finish).
        """
        with self._lock:
            self._forget_old()
            self._jobs[job.id] = job
        self._pool.submit(work, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        """
        Every job kept, newest first.
        """
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.started_at, reverse=True)

    def stop(self, job_id: str) -> Job | None:
        """
        Asks a running job to stop at its next step, and says so as a note; None for a job there isn't.
        """
        job = self.get(job_id)
        if job is None or not job.is_running or job.stop.is_set():
            return job
        job.stop.set()
        job.add("note", {"text": "Stopping: it stops at its next step, and shows what it read so far.",
                         "level": "info"})
        return job

    def shutdown(self) -> None:
        for job in self.all():
            job.stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _forget_old(self) -> None:
        cutoff = time.monotonic() - JOBS_KEPT
        finished = [job for job in self._jobs.values() if not job.is_running]
        for job in finished:
            if job.ended_before(cutoff):
                del self._jobs[job.id]
        finished = sorted((job for job in self._jobs.values() if not job.is_running), key=lambda job: job.started_at)
        for job in finished[:max(0, len(finished) - JOBS_HELD)]:
            del self._jobs[job.id]
