"""
Verifies the implementation of socketcan protocol.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import contextlib
import socket
from typing import TYPE_CHECKING, Literal, cast

import pytest
from hypothesis import HealthCheck, given, settings

from sockcan import SocketcanConfig, SocketcanFd, build_recv_func, connect_to_socketcan
from sockcan._protocol import build_send_func
from sockcan.fixtures import can_messages, rx_can_bus, skip_if_no_vcan, tx_can_bus, vcan_bus

if TYPE_CHECKING:
    from collections.abc import Generator
    from socket import socket as Socket  # noqa: N812

    from can import Message as PyCanMessage
    from can.interfaces.socketcan import SocketcanBus
    from pytest import FixtureRequest  # noqa: PT013


_ = tx_can_bus
_ = rx_can_bus

# Whether the socketcan socket should be created using python-can
# or our own implementation
SOCKET_PROVIDERS = ["python-can", "sockcan"]
type SocketProvider = Literal["python-can", "sockcan"]


@contextlib.contextmanager
def get_socketcan_sock(
    provider: SocketProvider = "python-can",
) -> Generator[SocketcanFd, None, None]:
    if provider == "python-can":
        with vcan_bus() as bus:
            yield cast("SocketcanFd", bus.socket)
    else:  # sockcan
        sock = connect_to_socketcan(SocketcanConfig(channel="vcan0"))
        try:
            yield sock
        finally:
            sock.close()


@pytest.fixture
def rx_sock(request: FixtureRequest) -> Generator[SocketcanFd, None, None]:
    """
    The socket that should be used to test message reception.
    Built by default using python-can. If parametrizing provider as sockcan,
    builds it using our implementation instead.
    """
    provider = cast("SocketProvider", getattr(request, "param", "python-can"))
    with get_socketcan_sock(provider=provider) as sock:
        yield sock


@pytest.fixture
def tx_sock(request: FixtureRequest) -> Generator[SocketcanFd, None, None]:
    """
    The socket that should be used to test message reception.
    Built by default using python-can. If parametrizing provider as sockcan,
    builds it using our implementation instead.
    """
    provider = cast("SocketProvider", getattr(request, "param", "python-can"))
    with get_socketcan_sock(provider=provider) as sock:
        yield sock


@pytest.mark.parametrize("rx_sock", ["sockcan"], indirect=True)
def test_sock_sanity(rx_sock: Socket) -> None:
    assert isinstance(rx_sock, socket.socket)


@given(can_message=can_messages())
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.parametrize("rx_sock", SOCKET_PROVIDERS, indirect=True)
@skip_if_no_vcan()
def test_recv_message(
    can_message: PyCanMessage,
    tx_can_bus: SocketcanBus,
    rx_sock: SocketcanFd,
) -> None:
    """
    Sends a message using python-can and verifies that the implementation in this package
    receives it properly.
    """
    recv_fn = build_recv_func(rx_sock)
    tx_can_bus.send(can_message)
    obtained = recv_fn()
    assert obtained.arbitration_id == can_message.arbitration_id
    assert obtained.is_extended_id == can_message.is_extended_id
    assert obtained.data == can_message.data


@given(can_message=can_messages())
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.parametrize("tx_sock", SOCKET_PROVIDERS, indirect=True)
@skip_if_no_vcan()
def test_send_message(
    can_message: PyCanMessage,
    rx_can_bus: SocketcanBus,
    tx_sock: SocketcanFd,
) -> None:
    send_fn = build_send_func(tx_sock)
    send_fn(can_message.arbitration_id, can_message.data, can_message.is_extended_id)  # type: ignore[arg-type]
    obtained = rx_can_bus.recv(timeout=0)
    assert obtained is not None, "Message was not sent"

    assert obtained.arbitration_id == can_message.arbitration_id
    assert obtained.is_extended_id == can_message.is_extended_id
    assert obtained.data == can_message.data
