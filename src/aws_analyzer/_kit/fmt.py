"""
Numbers, sizes, money, times and counts: parsing what users type in, and formatting what reports show; the time
zone reports show times in (set_timezone); file names' extensions; and Stat, a running count and its total bytes.
"""

from __future__ import annotations

import difflib
import os
import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

HOURS_PER_MONTH = 730  # how AWS turns an hourly price into a monthly one

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)i?b?\s*$", re.IGNORECASE)
_RELATIVE_TIME_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}
_COUNT_RE = re.compile(r"^\s*(\d[\d,_]*(?:\.\d+)?)\s*([km]?)\s*$", re.IGNORECASE)


def human_size(num_bytes: float | None) -> str:
    """
    1536 -> '1.5 KB' (binary units, like the AWS console).
    """
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def parse_size(value: int | float | str | None) -> int | None:
    """
    '10MB', '1.5 GiB', '512k', 1024 -> bytes. Units are binary (1 KB = 1024 B).
    """
    if value is None or isinstance(value, (int, float)):
        return None if value is None else int(value)
    match = _SIZE_RE.match(value)
    if not match:
        raise ValueError(f"Can't parse size {value!r}; try 1024, '10MB' or '1.5GB'")
    number, unit = match.groups()
    return int(float(number) * 1024 ** " kmgtp".index(unit.lower() or " "))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------------- the time zone reports show

TIMEZONE_ENV = "AWS_ANALYZER_TZ"  # names the zone for every notebook, before any of them calls set_timezone()
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)
_NUMERIC_ZONE_RE = re.compile(r"^(?:UTC|GMT)?[+-][\d:]+$")
_DEFAULT = object()  # no set_timezone() yet: AWS_ANALYZER_TZ's zone, else UTC
_chosen: Any = _DEFAULT  # what set_timezone() chose: a tzinfo, or None for this machine's own zone
_from_env: dict[str, tzinfo | None] = {}  # AWS_ANALYZER_TZ's value -> its zone, worked out once per value


def set_timezone(zone: str | tzinfo | None) -> str:
    """
    Show times in your own time zone: set_timezone('Asia/Kolkata'). Every report and window from then on shows
    its times there and says which zone they're in ('Last modified (IST)', '... since 2024-05-01 14:02 IST'), and
    dates you type in ('2024-05-01', '2024-05-01 10:00') are read there too. Returns the zone's short name, the way
    reports show it ('IST', 'PST/PDT', 'UTC').

    zone is a tz database name ('Europe/London', 'America/New_York'; its city alone works too: 'kolkata',
    'new york'), 'local' for this machine's own zone, 'UTC', an offset ('+05:30', 'UTC-4') or a tzinfo. None goes
    back to the default: the zone the AWS_ANALYZER_TZ environment variable names, else UTC. SageMaker's machines
    keep their clocks in UTC, so 'local' is UTC there: name your zone instead.

    A few things stay in UTC whatever the zone, and say so: CloudWatch's daily numbers (Lambda's "Day (UTC)"), which
    AWS adds up by UTC day.
    """
    global _chosen
    _chosen = _DEFAULT if zone is None else _zone_from(zone)
    return _tz_label()


def _zone_from(value: str | tzinfo) -> tzinfo | None:
    """
    What set_timezone() takes -> a tzinfo, or None for this machine's own zone. A name it can't find raises a
    ValueError that says what it takes, with the closest names.
    """
    if isinstance(value, tzinfo):
        return value
    if not isinstance(value, str):
        raise TypeError(f"set_timezone() takes a zone's name, like 'Asia/Kolkata', or a tzinfo; got {value!r}")
    text = value.strip()
    if text.lower() == "local":
        return None
    if text.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    offset = _OFFSET_RE.match(text)
    if offset:
        sign, hours, minutes = offset.groups()
        delta = timedelta(hours=int(hours), minutes=int(minutes or 0))
        if delta > timedelta(hours=14) or int(minutes or 0) >= 60:
            raise ValueError(f"{value!r} isn't an offset from UTC: they run from -12:00 to +14:00")
        return timezone(-delta if sign == "-" else delta)
    from zoneinfo import ZoneInfo, available_timezones

    names = available_timezones()
    if not names:  # Windows has no tz database of its own
        raise ValueError(f"Can't look up {value!r}: this Python has no time zone database. pip install tzdata, or "
                         "give an offset, like '+05:30'")
    places: dict[str, str] = {}  # 'asia/kolkata', 'kolkata', 'america/new york', 'new york' -> its name
    for name in sorted(names, key=lambda n: (n.count("/"), n)):  # 'louisville': America/, not America/Kentucky/
        full = name.lower().replace("_", " ")
        places.setdefault(full, name)
        places.setdefault(full.rsplit("/", 1)[-1], name)
    asked = re.sub(r"[\s_]+", " ", text.lower())
    if asked in places:
        return ZoneInfo(places[asked])
    close = dict.fromkeys(places[c] for c in difflib.get_close_matches(asked, list(places), n=3, cutoff=0.7))
    hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
    if re.fullmatch(r"[A-Z]{2,5}", text):
        hint += f" A short name like {text} can mean more than one zone (IST is India's, Ireland's and Israel's)."
    raise ValueError(f"{value!r} isn't a time zone set_timezone() knows.{hint} Name it the way the tz database does "
                     "('Asia/Kolkata', 'America/New_York'), by its city ('kolkata'), or by its offset ('+05:30')")


def _display_zone() -> tzinfo | None:
    """
    The zone reports show times in: set_timezone()'s, else the one AWS_ANALYZER_TZ names, else UTC. None is this
    machine's own zone.
    """
    if _chosen is not _DEFAULT:
        return _chosen
    name = os.environ.get(TIMEZONE_ENV, "").strip()
    if not name:
        return timezone.utc
    if name not in _from_env:
        try:
            _from_env[name] = _zone_from(name)
        except ValueError as exc:
            warnings.warn(f"{TIMEZONE_ENV}={name!r}: {exc}. Times show in UTC.", stacklevel=2)
            _from_env[name] = timezone.utc
    return _from_env[name]


def _in_zone(moment: datetime) -> datetime:
    """
    The moment in the zone reports show times in (UTC unless set_timezone() chose another).
    """
    return moment.astimezone(_display_zone())


def _zone_name(moment: datetime) -> str:
    """
    The zone of a moment _in_zone() gave, as reports name it: 'UTC', 'IST', 'PDT'; 'UTC+04' or 'UTC+05:45' for a
    zone that has no name in letters.
    """
    name = moment.tzname() or ""
    if name and not _NUMERIC_ZONE_RE.match(name):
        return name
    minutes = round((moment.utcoffset() or timedelta(0)).total_seconds() / 60)
    hours, rest = divmod(abs(minutes), 60)
    return "UTC" if not minutes else f"UTC{'-' if minutes < 0 else '+'}{hours:02d}" + (f":{rest:02d}" if rest else "")


def _tz_label() -> str:
    """
    The name of the zone reports show times in, for a column of times ('Last modified (IST)'): 'UTC', 'IST', or
    both of a zone's names when it changes its clocks, standard time first ('PST/PDT', 'GMT/BST').
    """
    zone, year = _display_zone(), _utcnow().year
    moments = [datetime(year, month, 1, 12, tzinfo=timezone.utc).astimezone(zone) for month in (1, 7)]
    named = sorted((m.utcoffset() or timedelta(0), _zone_name(m)) for m in moments)
    return "/".join(dict.fromkeys(name for _, name in named))


def parse_time(
    value: datetime | date | timedelta | str | None, now: datetime | None = None
) -> datetime | None:
    """
    datetime/date, ISO string ('2024-05-01', '2024-05-01T10:00Z'), or a relative
    age like '7d', '12h', '30m', '2w' meaning "that long ago". Naive values are in the zone reports show times in:
    UTC, unless set_timezone() chose another.
    """
    if value is None:
        return None
    if isinstance(value, timedelta):
        return (now or _utcnow()) - value
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    else:
        relative = _RELATIVE_TIME_RE.match(str(value))
        if relative:
            seconds = float(relative.group(1)) * _UNIT_SECONDS[relative.group(2).lower()]
            return (now or _utcnow()) - timedelta(seconds=seconds)
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if moment.tzinfo:
        return moment
    zone = _display_zone()
    return moment.astimezone() if zone is None else moment.replace(tzinfo=zone)  # astimezone(): this machine's


def human_age(when: datetime | None, now: datetime | None = None) -> str:
    """
    datetime -> '3d ago' / '5mo ago' / 'just now'.
    """
    if when is None:
        return "-"
    seconds = ((now or _utcnow()) - when).total_seconds()
    for unit, size in (
        ("y", 365 * 86400),
        ("mo", 30 * 86400),
        ("d", 86400),
        ("h", 3600),
        ("m", 60),
    ):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def human_money(usd: float | None) -> str:
    """
    12.345 -> '$12.35', 0.004 -> '<$0.01', 12345.6 -> '$12,346', -3 -> '-$3.00'.
    """
    if usd is None:
        return "-"
    sign, usd = ("-" if usd < 0 else ""), abs(usd)
    if 0 < usd < 0.01:
        return f"{sign}<$0.01"
    return f"{sign}${usd:,.0f}" if usd >= 1000 else f"{sign}${usd:,.2f}"


def _fmt_dt(moment: datetime | None, *, seconds: bool = False, zone: bool = False) -> str:
    """
    A moment as reports show it, in the zone set_timezone() chose (UTC unless): '2024-05-01 14:02', to the second
    with seconds=True. zone=True adds the zone's name, for a time in a sentence: '2024-05-01 14:02 UTC'.
    """
    if moment is None:
        return "-"
    moment = _in_zone(moment)
    text = moment.strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")
    return f"{text} {_zone_name(moment)}" if zone else text


def _duration(seconds: float) -> str:
    """
    0.42 -> '0.4s', 42.4 -> '42s', 125 -> '2m 05s', 7500 -> '2h 05m'.
    """
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60):02d}m"


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _count(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _share(part: float, whole: float) -> float:
    return part / whole if whole else 0.0


def _band(value: float, bands: list[tuple[str, int | None]]) -> str:
    for label, upper in bands:
        if upper is None or value < upper:
            return label
    return bands[-1][0]


def _total(cost: dict[str, float | None]) -> float | None:
    """
    The sum of a cost breakdown, or None when no part of it is known.
    """
    known = [v for v in cost.values() if v is not None]
    return sum(known) if known else None


def _as_int(value: Any, name: str, *, hint: str = "") -> int:
    """
    A number-of-items argument: 1000, '10,000', '10k' or '2m' -> int.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer():
        return int(value)
    match = _COUNT_RE.match(value) if isinstance(value, str) else None
    if match:
        number = (
            float(match.group(1).replace(",", "").replace("_", ""))
            * {"": 1, "k": 1000, "m": 10**6}[match.group(2).lower()]
        )
        if number.is_integer():
            return int(number)
    raise ValueError(f"{name} takes a number of items, like 1000 or '10k'{hint}; got {value!r}")


def _as_count(value: Any, name: str) -> int | None:
    """
    Like _as_int, for limits where None means no limit.
    """
    return None if value is None else _as_int(value, name, hint=", or None for no limit")


_COMPRESSION_EXTS = {
    "gz",
    "gzip",
    "bz2",
    "xz",
    "zst",
    "zstd",
    "snappy",
    "lz4",
    "zip",
    "z",
}


def file_extension(key: str) -> str:
    """
    'a/data.csv.gz' -> 'csv.gz', 'a/part-0.snappy.parquet' -> 'parquet', 'a/README' -> '(none)'.
    """
    if key.endswith("/"):
        return "(folder marker)"
    parts = key.rsplit("/", 1)[-1].lower().split(".")
    if len(parts) < 2 or parts[-1] == "" or (len(parts) == 2 and parts[0] == ""):
        return "(none)"  # 'README', 'name.', '.env'
    ext = parts[-1]
    if (
        ext in _COMPRESSION_EXTS
        and len(parts) > 2
        and parts[-2].isalpha()
        and len(parts[-2]) <= 8
    ):
        return f"{parts[-2]}.{ext}"
    return ext


@dataclass
class Stat:
    """
    Running count (objects, items) + total bytes.
    """

    count: int = 0
    size: int = 0

    def add(self, size: int) -> None:
        self.count += 1
        self.size += size
