"""
Implements end-to-end benchmark for the userspace socket daemon.

@date: 26.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from threading import Event, Thread
from time import perf_counter
from typing import TYPE_CHECKING

from ._bench import TEST_MSG

if TYPE_CHECKING:
    from collections.abc import Callable

    from can import Message

# Larger batch to reduce GIL / context-switch noise relative to per-frame cost.
DAEMON_BATCH_SIZE = 500


def bench_e2e(
    send_fn: Callable[[Message], None],
    recv_fn: Callable[[], object],
    batch_size: int,
    rounds: int,
) -> float:
    """
    Measures total wall-clock seconds for rounds * batch_size frames flowing
    end-to-end (TX → daemon relay → RX) with TX and RX concurrent.
    Timing starts when TX begins sending and ends when RX has consumed all frames,
    capturing the full pipeline latency including the relay overhead.
    """
    total = 0.0
    for _ in range(rounds):
        done = Event()

        def _rx(n: int = batch_size, ev: Event = done) -> None:
            for _ in range(n):
                recv_fn()
            ev.set()

        Thread(target=_rx, daemon=True).start()
        t0 = perf_counter()
        for _ in range(batch_size):
            send_fn(TEST_MSG)
        done.wait()
        total += perf_counter() - t0

    return total
