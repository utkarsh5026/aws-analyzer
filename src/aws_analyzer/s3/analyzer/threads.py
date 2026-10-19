"""
Work spread over threads, with progress reported from the calling thread only.
"""

from __future__ import annotations

import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable


def _run_in_threads(
    work: Callable[[Any], Any],
    items: list[Any],
    workers: int,
    tick: Callable[[], None],
    stop: threading.Event,
) -> list[tuple[Any, Any, Exception | None]]:
    """
    Run work(item) for every item on `workers` threads -> [(item, result, error)], in the order they finish.
    tick() runs about four times a second on the calling thread, so progress bars are only touched from it.
    An interrupt (the notebook's stop button) sets `stop` for the workers to see and cancels what hasn't started.
    """
    results: list[tuple[Any, Any, Exception | None]] = []
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, item): item for item in items}
        pending = set(futures)
        try:
            while pending:
                finished, pending = wait(
                    pending, timeout=0.25, return_when=FIRST_COMPLETED
                )
                for future in finished:
                    try:
                        results.append((futures[future], future.result(), None))
                    except Exception as exc:
                        results.append((futures[future], None, exc))
                tick()
        except BaseException:
            stop.set()
            for future in pending:
                future.cancel()
            raise
    return results


def _pool_size(client: Any) -> int:
    """
    How many connections the client keeps open: more threads than that just wait for one.
    """
    return (
        getattr(
            getattr(getattr(client, "meta", None), "config", None),
            "max_pool_connections",
            None,
        )
        or 10
    )
