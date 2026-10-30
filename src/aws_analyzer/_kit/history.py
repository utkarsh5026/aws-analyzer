"""
The places you opened in the explorers, remembered between sessions: the S3 folders you went to and the Lambda
functions you picked, so a window can offer them again, the ones you use most first, and a snapshot of what each one
looked like when you were last there, so a window can say what changed since.

One SQLite file (stdlib sqlite3), history/places.sqlite in aws-analyzer's cache folder ($XDG_CACHE_HOME, else
~/.cache), kept by clean_up(). It holds where you went (an s3:// path, a function's ARN), its name and when, per
AWS account and per explorer, never what a file or a log said. Each explorer keeps its newest KEPT_VISITS visits.
A snapshot is what an explorer chose to note about a place (counts, sizes, a code hash), as JSON; looks within
VISIT_GAP_MINUTES of each other are one visit, so a snapshot_before() is from an earlier visit, never this one, and
each place keeps its two newest snapshots.
history=False on an explorer, or AWS_ANALYZER_HISTORY=off, turns it off; AWS_ANALYZER_HISTORY=<path> puts the file
elsewhere. Nothing here raises: a file that can't be read or written turns the history off and says why (problem).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .local_files import sandbox_cache

HISTORY_ENV = "AWS_ANALYZER_HISTORY"
HISTORY_FOLDER = "history"
HISTORY_FILE = "places.sqlite"
HALF_LIFE_DAYS = 7.0
KEPT_VISITS = 500
VISIT_GAP_MINUTES = 60
SNAPSHOTS_KEPT = 2

_OFF = ("off", "0", "false", "no", "none")
_SECONDS_PER_DAY = 86_400.0
_TABLES = (
    "CREATE TABLE IF NOT EXISTS visits (account TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, "
    "label TEXT NOT NULL, at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS visits_by_place ON visits (account, kind, at)",
    "CREATE TABLE IF NOT EXISTS snapshots (account TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, "
    "at REAL NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS snapshots_by_place ON snapshots (account, kind, key, at)",
)


@dataclass(frozen=True)
class Visit:
    """
    One time you opened a place: its key (an s3:// path, an ARN), the name to show, and when.
    """

    key: str
    label: str
    at: datetime


@dataclass(frozen=True)
class Place:
    """
    A place you've been, ranked: how often and how lately (score, see frecency), when you were last there and how
    many visits are remembered.
    """

    key: str
    label: str
    last: datetime
    visits: int
    score: float


@dataclass(frozen=True)
class Snapshot:
    """
    What a place looked like on one visit: its key, when, and what the explorer noted (data, from JSON).
    """

    key: str
    at: datetime
    data: dict[str, Any]


def frecency(moments: list[datetime], now: datetime, *, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """
    How much a place is yours: every visit counts, a visit `half_life_days` old half as much as one now. Five visits
    this week outrank thirty in March, and one click yesterday doesn't outrank the folder you open every day.
    """
    total = 0.0
    for moment in moments:
        age_days = max(0.0, (now - moment).total_seconds() / _SECONDS_PER_DAY)
        total += 0.5 ** (age_days / half_life_days)
    return total


def rank_places(visits: list[Visit], now: datetime, *, limit: int | None = None) -> list[Place]:
    """
    The places in `visits`, the most yours first (frecency), the latest first when tied; each shows the name it had
    on its latest visit.
    """
    by_key: dict[str, list[Visit]] = {}
    for visit in visits:
        by_key.setdefault(visit.key, []).append(visit)

    places = []
    for key, seen in by_key.items():
        latest = max(seen, key=lambda visit: visit.at)
        score = frecency([visit.at for visit in seen], now)
        places.append(Place(key, latest.label, latest.at, len(seen), score))

    places.sort(key=lambda place: (-place.score, -place.last.timestamp()))
    return places if limit is None else places[:limit]


def history_path(setting: bool | str | os.PathLike | None = True) -> str:
    """
    The history file an explorer's history= means: '' for False (or AWS_ANALYZER_HISTORY=off), the path given, else
    AWS_ANALYZER_HISTORY's path, else history/places.sqlite in the cache folder.
    """
    if setting is False or setting is None:
        return ""
    if setting is not True:
        return os.path.abspath(os.path.expanduser(os.fspath(setting)))

    from_env = os.environ.get(HISTORY_ENV, "").strip()
    if from_env.lower() in _OFF:
        return ""
    if from_env:
        return os.path.abspath(os.path.expanduser(from_env))
    return os.path.join(sandbox_cache(), HISTORY_FOLDER, HISTORY_FILE)


def _utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


class PlaceHistory:
    """
    One explorer's remembered places (kind: 's3', 'lambda'), in the history file at `path`. record() adds a visit,
    places() ranks them, last() is where you were most lately and forget() clears them, all per AWS account.
    """

    def __init__(self, path: str, kind: str):
        self.path = path
        self.kind = kind
        self.problem = ""

    @classmethod
    def from_setting(cls, setting: bool | str | os.PathLike | None, kind: str) -> PlaceHistory | None:
        """
        The history an explorer's history= asks for, or None when it's off.
        """
        path = history_path(setting)
        return cls(path, kind) if path else None

    @property
    def is_on(self) -> bool:
        return not self.problem

    def _connect(self) -> sqlite3.Connection:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        for statement in _TABLES:
            connection.execute(statement)
        return connection

    def _failed(self, exc: Exception) -> None:
        self.problem = (f"Recent places are off: {self.path} couldn't be used ({type(exc).__name__}: {exc}). "
                        f"Set {HISTORY_ENV} to another file, or to off.")

    def record(self, account: str, key: str, label: str = "", *, at: datetime | None = None) -> None:
        """
        Adds a visit to `key`, and forgets this explorer's visits beyond its newest KEPT_VISITS, with the snapshots of
        the places that leaves without a visit.
        """
        if not key or not self.is_on:
            return
        moment = at or datetime.now(timezone.utc)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("INSERT INTO visits VALUES (?, ?, ?, ?, ?)",
                                   (account, self.kind, key, label or key, moment.timestamp()))
                connection.execute(
                    "DELETE FROM visits WHERE account = ? AND kind = ? AND rowid NOT IN (SELECT rowid FROM visits "
                    "WHERE account = ? AND kind = ? ORDER BY at DESC LIMIT ?)",
                    (account, self.kind, account, self.kind, KEPT_VISITS))
                connection.execute(
                    "DELETE FROM snapshots WHERE account = ? AND kind = ? AND key NOT IN (SELECT key FROM visits "
                    "WHERE account = ? AND kind = ?)", (account, self.kind, account, self.kind))
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)

    def visits(self, account: str) -> list[Visit]:
        """
        Every remembered visit of this explorer in `account`, oldest first.
        """
        if not self.is_on or not os.path.exists(self.path):
            return []
        try:
            with closing(self._connect()) as connection, connection:
                rows = connection.execute("SELECT key, label, at FROM visits WHERE account = ? AND kind = ? "
                                          "ORDER BY at", (account, self.kind)).fetchall()
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)
            return []
        return [Visit(key, label, _utc(at)) for key, label, at in rows]

    def places(self, account: str, *, limit: int | None = None, now: datetime | None = None) -> list[Place]:
        """
        The places you've been in `account`, the most yours first (rank_places).
        """
        return rank_places(self.visits(account), now or datetime.now(timezone.utc), limit=limit)

    def last(self, account: str) -> Place | None:
        """
        Where you were most lately in `account`, or None.
        """
        places = self.places(account)
        return max(places, key=lambda place: place.last) if places else None

    def _newest_snapshot(self, connection: sqlite3.Connection, account: str, key: str) -> tuple[int, float, str] | None:
        return connection.execute("SELECT rowid, at, data FROM snapshots WHERE account = ? AND kind = ? AND key = ? "
                                  "ORDER BY at DESC LIMIT 1", (account, self.kind, key)).fetchone()

    def save_snapshot(self, account: str, key: str, data: dict[str, Any], *, at: datetime | None = None) -> None:
        """
        Notes what `key` looks like now. Within VISIT_GAP_MINUTES of the last snapshot it's the same visit, so data
        is merged into that one; otherwise it's a new one, and only the newest SNAPSHOTS_KEPT are kept.
        """
        if not key or not self.is_on:
            return
        moment = at or datetime.now(timezone.utc)
        gap = timedelta(minutes=VISIT_GAP_MINUTES).total_seconds()
        try:
            with closing(self._connect()) as connection, connection:
                newest = self._newest_snapshot(connection, account, key)
                is_same_visit = newest is not None and moment.timestamp() - newest[1] < gap
                if is_same_visit:
                    merged = {**json.loads(newest[2]), **data}
                    connection.execute("UPDATE snapshots SET at = ?, data = ? WHERE rowid = ?",
                                       (moment.timestamp(), json.dumps(merged), newest[0]))
                    return
                connection.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
                                   (account, self.kind, key, moment.timestamp(), json.dumps(data)))
                connection.execute(
                    "DELETE FROM snapshots WHERE account = ? AND kind = ? AND key = ? AND rowid NOT IN (SELECT rowid "
                    "FROM snapshots WHERE account = ? AND kind = ? AND key = ? ORDER BY at DESC LIMIT ?)",
                    (account, self.kind, key, account, self.kind, key, SNAPSHOTS_KEPT))
        except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
            self._failed(exc)

    def _snapshots(self, query: str, params: tuple[Any, ...]) -> dict[str, Snapshot]:
        """
        The snapshots a query finds (key, at, data; oldest first), the newest per key.
        """
        if not self.is_on or not os.path.exists(self.path):
            return {}
        try:
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(query, params).fetchall()
            found = {key: Snapshot(key, _utc(at), json.loads(data)) for key, at, data in rows}
        except (sqlite3.Error, OSError, ValueError) as exc:
            self._failed(exc)
            return {}
        return found

    def snapshots_before(self, account: str, *, now: datetime | None = None) -> dict[str, Snapshot]:
        """
        Each place's snapshot from your last visit before this one (at least VISIT_GAP_MINUTES ago), by key.
        """
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=VISIT_GAP_MINUTES)
        return self._snapshots("SELECT key, at, data FROM snapshots WHERE account = ? AND kind = ? AND at <= ? "
                               "ORDER BY at", (account, self.kind, cutoff.timestamp()))

    def snapshot_before(self, account: str, key: str, *, now: datetime | None = None) -> Snapshot | None:
        """
        What `key` looked like on your last visit before this one, or None (a first visit, or history off).
        """
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=VISIT_GAP_MINUTES)
        found = self._snapshots("SELECT key, at, data FROM snapshots WHERE account = ? AND kind = ? AND key = ? "
                                "AND at <= ? ORDER BY at", (account, self.kind, key, cutoff.timestamp()))
        return found.get(key)

    def forget(self, account: str, key: str = "") -> int:
        """
        Forgets the visits to `key` in `account` and its snapshots, or every place this explorer remembers there; how
        many visits went.
        """
        if not self.is_on or not os.path.exists(self.path):
            return 0
        where = "account = ? AND kind = ?"
        params: tuple[str, ...] = (account, self.kind)
        if key:
            where += " AND key = ?"
            params += (key,)
        try:
            with closing(self._connect()) as connection, connection:
                removed = connection.execute(f"DELETE FROM visits WHERE {where}", params).rowcount
                connection.execute(f"DELETE FROM snapshots WHERE {where}", params)
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)
            return 0
        return removed
