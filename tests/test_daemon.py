"""
Test suite for the socketcan server

@date: 20.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from socket import socket
import threading
from typing import TYPE_CHECKING, Literal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sockcan import build_recv_func
from sockcan._protocol import build_send_func
from sockcan.daemon import SocketcanServer
from sockcan.daemon._server import ServerDirection
from sockcan.fixtures import can_messages, rx_can_bus, tx_can_bus, vcan_bus

if TYPE_CHECKING:
    from collections.abc import Generator

    from can import Message as PyCanMessage
    from can.interfaces.socketcan import SocketcanBus


_ = tx_can_bus
_ = rx_can_bus

# Whether the socketcan socket should be created using python-can
# or our own implementation
SOCKET_PROVIDERS = ["python-can", "sockcan"]
type SocketProvider = Literal["python-can", "sockcan"]


@pytest.fixture
def socketcan_server() -> Generator[SocketcanServer, None, None]:
    with vcan_bus() as bus:
        yield SocketcanServer(bus)


@given(can_messages=st.lists(can_messages(), min_size=10, max_size=100))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=10)
def test_single_consumer_rx(
    can_messages: list[PyCanMessage],
    tx_can_bus: SocketcanBus,
    socketcan_server: SocketcanServer,
) -> None:
    with vcan_bus() as rx_bus:
        socketcan_server = SocketcanServer(rx_bus)
        consumer = socketcan_server.subscribe()
        socketcan_server.start(direction=ServerDirection.RX_ONLY)
        recv_fn = build_recv_func(consumer, use_native_timestamps=False)

        for msg in can_messages:
            tx_can_bus.send(msg)
            obtained = recv_fn()
            assert obtained.data == msg.data
            assert obtained.arbitration_id == msg.arbitration_id
            assert obtained.is_extended_id is msg.is_extended_id


@given(can_messages=st.lists(can_messages(), min_size=10, max_size=100))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=10)
def test_single_consumer_tx(
    can_messages: list[PyCanMessage],
    rx_can_bus: SocketcanBus,
    socketcan_server: SocketcanServer,
) -> None:
    with vcan_bus() as tx_bus:
        socketcan_server = SocketcanServer(tx_bus)
        consumer = socketcan_server.subscribe()
        socketcan_server.start(direction=ServerDirection.TX_ONLY)
        send_fn = build_send_func(consumer)

        for msg in can_messages:
            send_fn(msg.arbitration_id, bytes(msg.data), msg.is_extended_id)
            obtained = rx_can_bus.recv()
            assert obtained is not None
            assert obtained.data == msg.data
            assert obtained.arbitration_id == msg.arbitration_id
            assert obtained.is_extended_id is msg.is_extended_id
    socketcan_server.stop()
    socketcan_server.join()
