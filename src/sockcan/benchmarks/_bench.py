"""
Implements benchmarks against python-can.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import cProfile
from collections.abc import Callable, Generator
from cProfile import Profile
from threading import Event, Thread
from time import perf_counter
from typing import TYPE_CHECKING

import can
from can import Message

if TYPE_CHECKING:
    from sockcan import RecvFn
    from sockcan.fixtures import SocketcanBus


TEST_MSG = can.Message(arbitration_id=0x200, data=b"\x00\x01\x02\x03\x04\x05\x06\x07")

# Each tick yields elapsed seconds for that batch.
type BatchGen = Generator[float, None, None]


def bench_rx(
    recv_fn: RecvFn | Callable[..., object],
    tx_bus: SocketcanBus,
    test_msg: Message = TEST_MSG,
    batch_size: int = 100,
    total_rounds: int = 100,
) -> Profile:
    """
    Profiles the receiver from this project against python-can.
    `batch_size` should be small enough to fit in the receiver buffer,
    as they will be sent at onBuffer, ce without consuming them.
    Total messages sent is equal to batch_size * total_rounds.
    """
    profile = cProfile.Profile()
    for _ in range(total_rounds):
        for _ in range(batch_size):
            tx_bus.send(test_msg)

        profile.enable()
        for _ in range(batch_size):
            recv_fn()
        profile.disable()
    return profile


class _BatchThread:
    """
    Wraps a BatchGen in a background daemon thread exposing the generator protocol.
    Each .send(None) triggers one batch in the thread and blocks until done,
    returning the elapsed seconds for that batch.
    """

    def __init__(self, gen: BatchGen) -> None:
        self._gen = gen
        self._go = Event()
        self._done = Event()
        self._elapsed: float = 0.0
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            self._go.wait()
            self._go.clear()
            try:
                self._elapsed = next(self._gen)
            except StopIteration:
                self._done.set()
                return
            self._done.set()

    def signal(self) -> None:
        """Trigger the next batch without blocking."""
        self._go.set()

    def wait(self) -> float:
        """Block until the current batch completes, return elapsed seconds."""
        self._done.wait()
        self._done.clear()
        return self._elapsed

    def send(self, value: None = None) -> float:
        """Trigger the next batch and block until done (generator protocol)."""
        self.signal()
        return self.wait()

    def close(self) -> None:
        """Close the underlying generator and unblock the thread so it can exit."""
        self._gen.close()
        self._go.set()


def tx_batch_gen(
    send_fn: Callable[..., None],
    test_msg: Message = TEST_MSG,
    batch_size: int = 100,
) -> BatchGen:
    """Generator: each tick sends batch_size messages and yields elapsed seconds."""
    while True:
        t0 = perf_counter()
        for _ in range(batch_size):
            send_fn(test_msg)
        yield perf_counter() - t0


def rx_batch_gen(
    recv_fn: Callable[[], object],
    batch_size: int = 100,
) -> BatchGen:
    """Generator: each tick receives batch_size messages and yields elapsed seconds."""
    while True:
        t0 = perf_counter()
        for _ in range(batch_size):
            recv_fn()
        yield perf_counter() - t0


def bench(
    tx_gen: BatchGen,
    rx_gen: BatchGen,
    total_rounds: int = 100,
) -> tuple[float, float]:
    """
    Runs total_rounds ticks of tx_gen and rx_gen concurrently in separate threads.
    Returns (total_tx_seconds, total_rx_seconds).
    """
    tx_thread = _BatchThread(tx_gen)
    rx_thread = _BatchThread(rx_gen)

    tx_total = rx_total = 0.0

    for _ in range(total_rounds):
        tx_thread.signal()
        rx_thread.signal()
        tx_total += tx_thread.wait()
        rx_total += rx_thread.wait()

    tx_thread.close()
    rx_thread.close()

    return tx_total, rx_total
