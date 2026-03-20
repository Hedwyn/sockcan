"""
Creates a socketcan server over an arbitrary CAN interface.
Provides a producer/consumer interface, allowing to create Socketcan-like
connections over any CAN interface.
For interfaces like pcan on Windows, this also allows concurrent connection on the CAN bus.

@date: 20.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Thread
from typing import TYPE_CHECKING, NamedTuple, Self, cast

import can
from can import BusState

from sockcan import SendFn, SocketcanFd, build_send_func

if TYPE_CHECKING:
    from can import BusABC, Message

_logger = logging.getLogger(__name__)


@dataclass
class BusParameters:
    """
    All configurable parameters for the CAN bus.
    """

    channel: str = "PCAN_USBBUS1"
    interface: str = "pcan"
    bitrate: int = 500_000


type RecvFn = Callable[[], Message]


class _Consumer(NamedTuple):
    """
    Stores the information required about a consumer in a minimal format.
    """

    sender: SendFn
    filters: set[int] | None


class SocketcanServer(Thread):
    """
    Listens to the CAN bus and dispatches the messages to all
    consumers that subscribed to the bus.
    """

    def __init__(self, bus: BusABC) -> None:
        """
        Wraps the passed `bus`. For interfaces that do not support concurrency
        (e.g. pcan windows), this object should be the only one accessing the real bus.
        """
        super().__init__(daemon=True)
        self._consumers: list[_Consumer] = []
        self._kill_switch = Event
        self._bus = bus

    def subscribe(self, filters: set[int] | None = None) -> SocketcanFd:
        """
        Subscibes to the bus; returns a socketcan-like socket that can be used
        with socketcan protocol.
        If filters is passed, only messages with requested filters will be forwarded.
        """
        parent, child = socket.socketpair()
        sender = build_send_func(cast("SocketcanFd", parent))
        self._consumers.append(_Consumer(sender, filters))
        return cast("SocketcanFd", child)

    @classmethod
    @contextmanager
    def factory(cls, bus_params: BusParameters | None = None) -> Generator[Self, None, None]:
        """
        A context-manager building a bus and wrapping it with
        this object. Bus will be closed on exiting the context.
        """
        bus_params = bus_params or BusParameters()
        with can.Bus(
            interface=bus_params.interface,
            channel=bus_params.channel,
            bitrate=bus_params.bitrate,
        ) as bus:
            server = cls(bus)
            yield server

    def run(self) -> None:
        """
        Main thread function.
        Catches bus errors and reports them if they are not coming
        from closing the wrapped bus handle.
        """
        try:
            self._run()
        except can.CanOperationError as exc:
            # this path can be taken when closing the bus handle
            # on user side.
            # It should only be considered an error if the bus is in an error state.
            if self._bus.state == BusState.ERROR:
                _logger.error("CAN bus server ended on can operation error: %s", exc)  # noqa: TRY400

    def _run(self) -> None:
        """
        Listens forever to the bus and forwards messages to all consumers.
        """
        recv = self._bus.recv
        consumers = self._consumers
        while True:
            next_message = recv()
            assert next_message is not None
            can_id = next_message.arbitration_id
            data = next_message.data

            for sender, filters in consumers:
                if filters and can_id not in filters:
                    continue
                sender(can_id, data, next_message.is_extended_id)  # type: ignore[arg-type]
