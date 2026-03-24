"""
Test suite for the socketcan server

@date: 20.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
from can import Message
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
        server = SocketcanServer(bus)
        yield server
        server.stop()


@pytest.fixture
def virtual_socketcan_server() -> Generator[SocketcanServer, None, None]:
    server = SocketcanServer()
    yield server
    server.stop()
    server.join()


@given(can_messages=st.lists(can_messages(), min_size=10, max_size=100))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=10)
def test_single_consumer_rx(
    can_messages: list[PyCanMessage],
    tx_can_bus: SocketcanBus,
) -> None:
    with vcan_bus() as rx_bus:
        socketcan_server = SocketcanServer(rx_bus)
        consumer = socketcan_server.subscribe()
        socketcan_server.start(direction=ServerDirection.RX_ONLY)
        recv_fn = build_recv_func(consumer, use_native_timestamps=False, is_stream=True)

        for msg in can_messages:
            tx_can_bus.send(msg)
            obtained = recv_fn()
            assert obtained.data == msg.data
            assert obtained.arbitration_id == msg.arbitration_id
            assert obtained.is_extended_id is msg.is_extended_id
    socketcan_server.stop()


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


@given(can_messages=st.lists(can_messages(), min_size=10, max_size=100))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=1)
def test_socketcan_bus_bidir(
    can_messages: list[PyCanMessage],
) -> None:
    with vcan_bus() as bus:
        socketcan_server = SocketcanServer(bus)
        conn_1 = socketcan_server.subscribe()
        socketcan_server.start()

        recv_fn_1 = build_recv_func(conn_1, use_native_timestamps=False)
        send_fn_1 = build_send_func(conn_1, expects_msg_cls=True)

        test_msg = can_messages[0]
        send_fn_1(test_msg)
        # expecting here to NOT receive our own message
        conn_1.setblocking(False)  # noqa: FBT003
        with pytest.raises(BlockingIOError):
            conn_1.recv(1)
        conn_1.setblocking(True)  # noqa: FBT003

        conn_2 = socketcan_server.subscribe()
        # conn_2.setblocking(False)  # noqa: FBT003
        send_fn_2 = build_send_func(conn_2, expects_msg_cls=True)
        recv_fn_2 = build_recv_func(conn_2, use_native_timestamps=False)

        for msg in can_messages:
            send_fn_1(msg)
            obtained = recv_fn_2()
            assert obtained.arbitration_id == msg.arbitration_id
            assert obtained.data == msg.data
            assert obtained.is_extended_id == msg.is_extended_id

        for msg in can_messages:
            send_fn_2(msg)
            obtained = recv_fn_1()
            assert obtained.arbitration_id == msg.arbitration_id
            assert obtained.data == msg.data
            assert obtained.is_extended_id == msg.is_extended_id
    socketcan_server.stop()


@given(can_messages=st.lists(can_messages(), min_size=2, max_size=2))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=1)
def test_socketcan_bus_buffering(
    can_messages: list[PyCanMessage],
) -> None:
    with vcan_bus() as bus:
        socketcan_server = SocketcanServer(bus)
        conn_1 = socketcan_server.subscribe()

        socketcan_server.start()
        recv_fn_1 = build_recv_func(conn_1, use_native_timestamps=False)
        send_fn_1 = build_send_func(conn_1, expects_msg_cls=True)

        test_msg = can_messages[0]
        send_fn_1(test_msg)
        # expecting here to NOT receive our own message
        conn_1.setblocking(False)  # noqa: FBT003
        with pytest.raises(BlockingIOError):
            conn_1.recv(1)
        conn_1.setblocking(True)  # noqa: FBT003

        conn_2 = socketcan_server.subscribe()
        send_fn_2 = build_send_func(conn_2, expects_msg_cls=True)
        recv_fn_2 = build_recv_func(conn_2, use_native_timestamps=False, is_stream=True)

        for msg in can_messages:
            send_fn_1(msg)
        for msg in can_messages:
            obtained = recv_fn_2()
            assert obtained.arbitration_id == msg.arbitration_id
            assert obtained.data == msg.data
            assert obtained.is_extended_id == msg.is_extended_id

        for msg in can_messages:
            send_fn_2(msg)
        for msg in can_messages:
            obtained = recv_fn_1()
            assert obtained.arbitration_id == msg.arbitration_id
            assert obtained.data == msg.data
            assert obtained.is_extended_id == msg.is_extended_id
        socketcan_server.stop()


@given(can_messages=st.lists(can_messages(), min_size=10, max_size=100))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=1)
def test_virtual_socketcan_bus(
    virtual_socketcan_server: SocketcanServer,
    can_messages: list[PyCanMessage],
) -> None:
    virtual_socketcan_server = SocketcanServer()

    conn_1 = virtual_socketcan_server.subscribe()

    virtual_socketcan_server.start()
    recv_fn_1 = build_recv_func(conn_1, use_native_timestamps=False)
    send_fn_1 = build_send_func(conn_1, expects_msg_cls=True)

    test_msg = can_messages[0]
    send_fn_1(test_msg)
    # expecting here to NOT receive our own message
    conn_1.setblocking(False)  # noqa: FBT003
    with pytest.raises(BlockingIOError):
        conn_1.recv(1)
    conn_1.setblocking(True)  # noqa: FBT003

    conn_2 = virtual_socketcan_server.subscribe()
    # conn_2.setblocking(False)  # noqa: FBT003
    send_fn_2 = build_send_func(conn_2, expects_msg_cls=True)
    recv_fn_2 = build_recv_func(conn_2, use_native_timestamps=False)

    for msg in can_messages:
        send_fn_1(msg)
        obtained = recv_fn_2()
        assert obtained.arbitration_id == msg.arbitration_id
        assert obtained.data == msg.data
        assert obtained.is_extended_id == msg.is_extended_id

    for msg in can_messages:
        send_fn_2(msg)
        obtained = recv_fn_1()
        assert obtained.arbitration_id == msg.arbitration_id
        assert obtained.data == msg.data
        assert obtained.is_extended_id == msg.is_extended_id
