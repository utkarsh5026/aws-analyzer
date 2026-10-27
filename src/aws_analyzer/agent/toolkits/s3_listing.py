"""
How the S3 toolkit asks the user before a big read (interrupt_on): a listing that goes past the budget's ask_keys
(KeyListing, which find_files, folder_summary and save_s3_files read their keys through, and listing_read, what it
asks), a save that finds more files than the model asked for (count_read) and a save into the workspace past
ask_bytes (save_read). Each BigRead says what each choice reads and what its
requests cost, at the list price it's given (USD per 1,000 requests). The functions are pure; _AsksFirst is the part
of the S3 toolkit that lists through a KeyListing and asks before a save.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator

from ..._kit.fmt import _plural, human_money, human_size
from ..answers import BigRead
from ..budget import Budget
from .s3_saving import SavePlan, plan_saves

if TYPE_CHECKING:
    from ...s3 import ObjectInfo

_KEYS_PER_REQUEST = 1000
_REQUESTS_PER_PRICE = 1000


def listing_read(tool: str, uri: str, budget: Budget, *, held: int | None, price: float) -> BigRead:
    """
    What a listing asks once it has listed the budget's ask_keys keys under `uri` and there are more: Go lists the
    rest, up to its s3_keys (or about the `held` files CloudWatch counts in the bucket, when that's fewer), Smaller
    stops there. `price` is USD per 1,000 LIST requests.
    """
    listed, limit = budget.ask_keys, budget.s3_keys
    most = limit if held is None else max(listed, min(limit, held))
    requests = max(1, math.ceil((most - listed) / _KEYS_PER_REQUEST))
    cost = requests * price / _REQUESTS_PER_PRICE
    holds = f"; the bucket holds about {held:,} files" if held else ""
    return BigRead(tool, what=f"{tool} has listed {listed:,} keys under {uri} and there are more{holds}.",
                   go=f"list the rest, up to {limit:,} keys (about {_plural(requests, 'more request')}, "
                      f"{human_money(cost)})",
                   smaller=f"stop at the {listed:,} keys listed so far: the counts cover those only", cost=cost)


def save_read(uri: str, *, files: int, size: int, smaller_files: int, smaller_size: int,  # noqa: PLR0913
              limit: int, price: float) -> BigRead:
    """
    What save_s3_files asks before it copies `files` files (`size` bytes) from `uri` into the workspace, more than
    `limit` bytes: Go copies them all, Smaller the `smaller_files` that fit in `limit` (`smaller_size` bytes). `price`
    is USD per 1,000 GET requests.
    """
    cost = files * price / _REQUESTS_PER_PRICE
    if smaller_files:
        smaller = f"copy {_plural(smaller_files, 'file')} ({human_size(smaller_size)}), up to {human_size(limit)}"
    else:
        smaller = f"copy none: each is bigger than {human_size(limit)}"
    return BigRead("save_s3_files",
                   what=f"save_s3_files would copy {_plural(files, 'file')} ({human_size(size)}) from {uri} into "
                        f"the workspace, more than {human_size(limit)}.",
                   go=f"copy all {_plural(files, 'file')} ({human_size(size)}, {human_money(cost)} in requests)",
                   smaller=smaller, cost=cost)


def count_read(uri: str, *, asked: int, files: int, size: int, most: int, found: int,  # noqa: PLR0913
               price: float) -> BigRead:
    """
    What save_s3_files asks when more of the files under `uri` can be copied than the `asked` the model asked for:
    Go copies `files` (`size` bytes; fewer than `found` when the budget's save_files, `most`, or the workspace stops
    it), Smaller the first `asked` in key order. `price` is USD per 1,000 GET requests.
    """
    cost = files * price / _REQUESTS_PER_PRICE
    if files == found:
        go = f"copy all {_plural(files, 'file')} ({human_size(size)}, {human_money(cost)} in requests)"
    elif files == most:
        go = (f"copy {_plural(files, 'file')} ({human_size(size)}, {human_money(cost)} in requests), the most one "
              "save takes (the budget's save_files)")
    else:
        go = (f"copy the {_plural(files, 'file')} that fit in the workspace ({human_size(size)}, "
              f"{human_money(cost)} in requests)")
    return BigRead("save_s3_files",
                   what=f"save_s3_files found {_plural(found, 'file')} under {uri} to copy; it was asked for "
                        f"{asked:,}.",
                   go=go, smaller=f"copy the first {asked:,} in key order", cost=cost)


class KeyListing:
    """
    The keys under a location in key order (`keys`, the analyzer's iter_objects), for a tool that reads them all:
    once it has passed `ask_at` keys and there are more, it asks whether to stop there (`stop_here`, which asks the
    user and says whether they picked Smaller), unless `limit` is no more than that. cut_short says the user stopped it.
    """

    def __init__(self, keys: Iterable[ObjectInfo], *, ask_at: int, limit: int, stop_here: Callable[[], bool]):
        self._keys = keys
        self._ask_at = ask_at
        self._limit = limit
        self._stop_here = stop_here
        self.cut_short = False

    def __iter__(self) -> Iterator[ObjectInfo]:
        for seen, obj in enumerate(self._keys):
            if seen == self._ask_at and self._ask_at < self._limit and self._stop_here():
                self.cut_short = True
                return
            yield obj


class _AsksFirst:
    """
    The part of the S3 toolkit that asks before a big read: its listings go through a KeyListing, which asks past
    the budget's ask_keys (_stop_listing, the toolkit's, says how many files the bucket holds), and a save past
    ask_bytes asks before it copies anything.
    """

    analyzer: Any
    budget: Budget
    will_ask: Callable[[str], bool]
    ask_user: Callable[[BigRead], str]
    _stop_listing: Callable[[str, str], bool]

    def _matching(self, tool: str, location: str,
                  keep: Callable[[ObjectInfo], bool]) -> tuple[list[ObjectInfo], int, bool]:
        """
        (the files under a location that `keep` accepts, how many keys were read, whether there were more): it reads
        up to the budget's s3_keys, and one more to tell when there were more, asking the user past ask_keys.
        """
        cap = self.budget.s3_keys
        listing = self._listing(tool, location)
        scanned, found, more = 0, [], False
        for obj in listing:
            if scanned >= cap:
                more = True
                break
            scanned += 1
            if keep(obj):
                found.append(obj)
        return found, scanned, more or listing.cut_short

    def _listing(self, tool: str, location: str) -> KeyListing:
        """
        The keys under a location for one of `tool`'s listings, up to the budget's s3_keys and one more, asking the
        user once it passes ask_keys (when the agent asks about the tool).
        """
        budget = self.budget
        keys = self.analyzer.iter_objects(location, limit=budget.s3_keys + 1)
        ask_at = budget.ask_keys if self.will_ask(tool) else budget.s3_keys
        return KeyListing(keys, ask_at=ask_at, limit=budget.s3_keys,
                          stop_here=lambda: self._stop_listing(tool, location))

    def _confirm_save(self, files: list[ObjectInfo], uri: str, *, room: int, max_files: int,
                      price: float) -> SavePlan:
        """
        The save to make of `files`: the first `max_files` that fit in `room`, unless the user picks more of them
        (_confirm_count) or fewer bytes (_confirm_size). `price` is USD per 1,000 GET requests.
        """
        plan = self._confirm_count(files, uri, room=room, max_files=max_files, price=price)
        return self._confirm_size(plan, files, uri, room=room, price=price)

    def _confirm_count(self, files: list[ObjectInfo], uri: str, *, room: int, max_files: int,
                       price: float) -> SavePlan:
        """
        The first `max_files` of `files` that fit in `room`, or, when more could be copied and the user picks Go,
        as many as fit, up to the budget's save_files.
        """
        plan = plan_saves(files, room=room, max_files=max_files)
        most = self.budget.save_files
        if max_files >= most or not self.will_ask("save_s3_files"):
            return plan
        everything = plan_saves(files, room=room, max_files=most)
        if len(everything.chosen) <= len(plan.chosen):
            return plan
        copyable = len(plan_saves(files, room=sum(f.size for f in files), max_files=len(files)).chosen)
        read = count_read(uri, asked=max_files, files=len(everything.chosen), size=everything.size, most=most,
                          found=copyable, price=price)
        return everything if self.ask_user(read) == "go" else plan

    def _confirm_size(self, plan: SavePlan, files: list[ObjectInfo], uri: str, *, room: int,
                      price: float) -> SavePlan:
        """
        `plan` when it's no more than the budget's ask_bytes, else what the user picks: all of it, or the files that
        fit in ask_bytes.
        """
        limit = self.budget.ask_bytes
        if plan.size <= limit or not self.will_ask("save_s3_files"):
            return plan
        smaller = plan_saves(files, room=min(room, limit), max_files=len(plan.chosen),
                             past_room=f"past the {human_size(limit)} you picked")
        read = save_read(uri, files=len(plan.chosen), size=plan.size, smaller_files=len(smaller.chosen),
                         smaller_size=smaller.size, limit=limit, price=price)
        return smaller if self.ask_user(read) == "smaller" else plan
