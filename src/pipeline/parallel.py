"""Thread-pool helper for network-bound stages (fetch)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def pmap(fn: Callable[[T], R], items: Iterable[T], workers: int = 1) -> list[R]:
    """`[fn(x) for x in items]` on up to `workers` threads; results keep the input order.

    For network-bound work. HttpClient's per-host throttle is shared by all threads, so extra
    workers only overlap waiting for servers, they never exceed the polite request rate.
    Every item is attempted even if another fails, so one bad file does not cost the others;
    the first error is raised once all are done. workers <= 1 runs in the calling thread.
    """
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        results, errors = [], []
        for x in items:
            try:
                results.append(fn(x))
            except Exception as e:
                errors.append(e)
        if errors:
            raise errors[0]
        return results
    ex = ThreadPoolExecutor(max_workers=min(workers, len(items)))
    try:
        futures = [ex.submit(fn, x) for x in items]
        errors = [e for e in (f.exception() for f in futures) if e is not None]
    finally:
        ex.shutdown(wait=True, cancel_futures=True)  # Ctrl-C: drop what has not started yet
    if errors:
        raise errors[0]
    return [f.result() for f in futures]
