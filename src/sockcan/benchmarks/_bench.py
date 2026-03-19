"""
Implements benchmarks against python-can.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import cProfile
from cProfile import Profile
from typing import TYPE_CHECKING

import can
from can import Message

if TYPE_CHECKING:
    from collections.abc import Callable

    from sockcan import RecvFn
    from sockcan.fixtures import SocketcanBus


TEST_MSG = can.Message(arbitration_id=0x200, data=b"\x00\x01\x02\x03\x04\x05\x06\x07")


def bench(
    recv_fn: RecvFn | Callable[..., object],
    tx_bus: SocketcanBus,
    test_msg: Message = TEST_MSG,
    batch_size: int = 100,
    total_rounds: int = 100,
) -> Profile:
    """
    Profiles the receiver from this project against python-can.
    `batch_size` should be small enough to fit in the receiver buffer,
    as they will be sent at once without consuming them.
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
