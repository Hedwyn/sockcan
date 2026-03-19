"""
Sanity checks for the tests fixtures.

@date: 18.03.2026
@author; Baptiste Pestourie
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import HealthCheck, given, settings

from sockcan.fixtures import SocketcanBus, can_messages, rx_can_bus, skip_if_no_vcan, tx_can_bus

if TYPE_CHECKING:
    from can import Message


# fixtures
_ = rx_can_bus
_ = tx_can_bus


@given(can_message=can_messages())
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@skip_if_no_vcan()
def test_random_message_sanity(
    can_message: Message,
    rx_can_bus: SocketcanBus,
    tx_can_bus: SocketcanBus,
) -> None:
    """
    Verifies that the auto-generated messages are received on the other side
    as expected.
    """
    tx_can_bus.send(can_message)
    obtained = rx_can_bus.recv()
    assert obtained is not None
    assert obtained.arbitration_id == can_message.arbitration_id
    assert obtained.data == can_message.data
    assert obtained.dlc == can_message.dlc
    assert obtained.is_extended_id is can_message.is_extended_id
