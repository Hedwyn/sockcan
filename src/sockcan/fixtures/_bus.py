"""
Small fixtures around VCAN bus.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING

import can
import pytest
from can.interfaces.socketcan import SocketcanBus

if TYPE_CHECKING:
    from collections.abc import Generator


@contextlib.contextmanager
def vcan_bus(channel: str = "vcan0") -> Generator[SocketcanBus, None, None]:
    """
    A context manager around a virtual can bus.
    """
    with can.Bus(interface="socketcan", channel=channel) as bus:
        assert isinstance(bus, SocketcanBus)
        yield bus


def has_vcan(channel: str = "vcan0") -> bool:
    """
    Whether vcan0 is available on the system.
    """
    try:
        with vcan_bus(channel=channel):
            return True

    except OSError:
        return False


def skip_if_no_vcan[T: Callable[..., None]]() -> Callable[[T], T]:
    """
    A pytest mark that skips the test if no vcan is available.
    """
    return pytest.mark.skipif(
        has_vcan(),
        reason="VCAN channel (vcan0) needs to be available for testing",
    )


@pytest.fixture
def tx_can_bus() -> Generator[SocketcanBus, None, None]:
    """
    Bus fixture for testing
    """
    with vcan_bus() as bus:
        yield bus


@pytest.fixture
def rx_can_bus() -> Generator[SocketcanBus, None, None]:
    """
    Bus fixture for testing
    """
    with vcan_bus() as bus:
        yield bus
