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
    skip_if_no_vcan,
    skip_if_windows,
    tx_can_bus,
)
from sockcan.interop import FastSocketcanBus, _matches_filters, override_python_can

if TYPE_CHECKING:
    from can import Message as PyCanMessage


_ = tx_can_bus
_ = rx_can_bus


def test_matches_filters_rejects_non_matching_extended_id() -> None:
    """
    A filter scoped to one extended CAN ID must not match an unrelated extended
    CAN ID just because both frames are extended.

    Regression test: operator precedence in the old implementation turned the
    check into `(id_matches and no_extended_key) or (is_extended == filter_extended)`,
    so any two extended frames matched any extended filter regardless of ID.
    """
    afe6_only_filter = [{"can_id": 0x060780A5, "can_mask": 0x1FFFFFFF, "extended": True}]

    assert _matches_filters(afe6_only_filter, 0x060780A5, is_extended=True) is True
    assert _matches_filters(afe6_only_filter, 0x020780A5, is_extended=True) is False


def test_matches_filters_without_extended_key_matches_either_kind() -> None:
    """A filter that doesn't specify `extended` matches on ID alone, either kind of frame."""
    id_only_filter = [{"can_id": 0x060780A5, "can_mask": 0x1FFFFFFF}]

    assert _matches_filters(id_only_filter, 0x060780A5, is_extended=True) is True
    assert _matches_filters(id_only_filter, 0x060780A5, is_extended=False) is True
    assert _matches_filters(id_only_filter, 0x020780A5, is_extended=True) is False


def test_matches_filters_empty_accepts_everything() -> None:
    assert _matches_filters([], 0x123, is_extended=True) is True


@skip_if_no_vcan()
@skip_if_windows()
def test_python_can_overriding() -> None:
    with override_python_can(), can.Bus(channel="vcan0", interface="socketcan") as bus:
        assert isinstance(bus, FastSocketcanBus)
    with can.Bus(channel="vcan0", interface="socketcan") as bus:
        assert isinstance(bus, SocketcanBus)


@given(can_messages=st.lists(can_messages(), min_size=10, max_size=100))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=10)
@skip_if_no_vcan()
@skip_if_windows()
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
@skip_if_no_vcan()
@skip_if_windows()
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
