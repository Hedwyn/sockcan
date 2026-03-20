"""
Checks that the interoperability layer with python-can works properly.

@date: 20.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import can
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sockcan.fixtures import (
    SocketcanBus,
    can_messages,
    rx_can_bus,
    tx_can_bus,
)
from sockcan.interop import FastSocketcanBus, override_python_can

if TYPE_CHECKING:
    from can import Message as PyCanMessage


_ = tx_can_bus
_ = rx_can_bus


def test_python_can_overriding() -> None:
    with override_python_can(), can.Bus(channel="vcan0", interface="socketcan") as bus:
        assert isinstance(bus, FastSocketcanBus)
    with can.Bus(channel="vcan0", interface="socketcan") as bus:
        assert isinstance(bus, SocketcanBus)


@given(can_messages=st.lists(can_messages(), min_size=10, max_size=100))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=10)
def test_compatiblity_rx(
    can_messages: list[PyCanMessage],
    tx_can_bus: SocketcanBus,
    rx_can_bus: SocketcanBus,
) -> None:
    with override_python_can(), can.Bus(interface="socketcan", channel="vcan0") as test_bus:
        for msg in can_messages:
            tx_can_bus.send(msg)
            reference = rx_can_bus.recv()
            assert reference is not None
            obtained = test_bus.recv()
            assert obtained is not None

            assert reference.arbitration_id == obtained.arbitration_id
            assert reference.is_extended_id is obtained.is_extended_id
            assert reference.data == obtained.data
            assert reference.timestamp == obtained.timestamp


@given(can_messages=st.lists(can_messages(), min_size=10, max_size=100))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=10)
def test_compatiblity_tx(
    can_messages: list[PyCanMessage],
    tx_can_bus: SocketcanBus,
    rx_can_bus: SocketcanBus,
) -> None:
    with override_python_can(), can.Bus(interface="socketcan", channel="vcan0") as test_bus:
        for msg in can_messages:
            tx_can_bus.send(msg)
            reference = rx_can_bus.recv()
            test_bus.send(msg)
            obtained = rx_can_bus.recv()

            assert reference is not None
            obtained = test_bus.recv()
            assert obtained is not None

            assert reference.arbitration_id == obtained.arbitration_id
            assert reference.is_extended_id is obtained.is_extended_id
            assert reference.data == obtained.data
            assert reference.timestamp == obtained.timestamp
