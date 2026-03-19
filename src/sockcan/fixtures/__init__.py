"""
All fixtures from this package avaiable for testing.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from can.interfaces.socketcan import SocketcanBus

from ._bus import has_vcan, rx_can_bus, skip_if_no_vcan, tx_can_bus, vcan_bus
from ._messages import can_messages

__all__ = [
    "SocketcanBus",
    "can_messages",
    "has_vcan",
    "rx_can_bus",
    "skip_if_no_vcan",
    "tx_can_bus",
    "vcan_bus",
]
