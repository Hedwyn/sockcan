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
from enum import Enum, auto
from selectors import EVENT_READ, DefaultSelector
from threading import Event, Thread
from typing import TYPE_CHECKING, NamedTuple, Self, cast

import can
from can import BusState, Message

from sockcan import SendFn, SocketcanFd, build_send_func
from sockcan._protocol import build_recv_func

if TYPE_CHECKING:
    from can import BusABC

_logger = logging.getLogger(__name__)


class ServerDirection(Enum):
    """
    In which direction(s) the socketcan server should work.
    """

    RX_ONLY = auto()
    TX_ONLY = auto()
    BIDIRECTIONAL = auto()


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


class SocketcanServer:
    """
    Listens to the CAN bus and dispatches the messages to all
    consumers that subscribed to the bus.
    """

    def __init__(self, bus: BusABC) -> None:
        """
        Wraps the passed `bus`. For interfaces that do not support concurrency
        (e.g. pcan windows), this object should be the only one accessing the real bus.
        """
        self._consumers: list[_Consumer] = []
        self._kill_switch = Event
        self._bus = bus
        self._selector = DefaultSelector()
        self._kill_switch_rx, self._kill_switch_tx = socket.socketpair()
        self._running: bool = False
        self._threads: list[Thread] = []
        self._selector.register(self._kill_switch_rx, events=EVENT_READ, data=None)

    def subscribe(self, filters: set[int] | None = None) -> SocketcanFd:
        """
        Subscibes to the bus; returns a socketcan-like socket that can be used
        with socketcan protocol.
        If filters is passed, only messages with requested filters will be forwarded.
        """
        _ours, _theirs = socket.socketpair()
        ours = cast("SocketcanFd", _ours)
        theirs = cast("SocketcanFd", _theirs)
        send_fn = build_send_func(ours, expects_msg_cls=False)
        recv_fn = build_recv_func(ours, use_native_timestamps=False)
        self._consumers.append(_Consumer(send_fn, filters))
        self._selector.register(ours, events=EVENT_READ, data=recv_fn)
        return theirs

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

    def start(self, direction: ServerDirection = ServerDirection.BIDIRECTIONAL) -> None:
        """
        Starts both TX and RX thread.
        """
        if self._running:
            raise RuntimeError("Already started")
        self._threads.clear()
        self._running = True
        if direction != ServerDirection.TX_ONLY:
            rx_thread = Thread(target=self.run_rx, daemon=True)
            rx_thread.start()
            self._threads.append(rx_thread)
        if direction != ServerDirection.RX_ONLY:
            tx_thread = Thread(target=self.run_tx, daemon=True)
            tx_thread.start()
            self._threads.append(tx_thread)

    def stop(self) -> None:
        """
        Stops the sender thread.
        """
        if not self._running:
            return
        self._kill_switch_rx.send(b"0")
        self._running = False

    def join(self) -> None:
        """
        Waits for RX and TX thread to terminate.
        """
        for thread in self._threads:
            thread.join()

    def run_rx(self) -> None:
        """
        Main thread function.
        Catches bus errors and reports them if they are not coming
        from closing the wrapped bus handle.
        """
        try:
            self._run_rx()
        except can.CanOperationError as exc:
            # this path can be taken when closing the bus handle
            # on user side.
            # It should only be considered an error if the bus is in an error state.
            if self._bus.state == BusState.ERROR:
                _logger.error("CAN bus server ended on can operation error: %s", exc)  # noqa: TRY400

    def _run_rx(self) -> None:
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

    def run_tx(self) -> None:
        """
        Main thread function.
        Catches bus errors and reports them if they are not coming
        from closing the wrapped bus handle.
        """
        try:
            self._run_tx()
        except can.CanOperationError as exc:
            # this path can be taken when closing the bus handle
            # on user side.
            # It should only be considered an error if the bus is in an error state.
            if self._bus.state == BusState.ERROR:
                _logger.error("CAN bus server ended on can operation error: %s", exc)  # noqa: TRY400

    def _run_tx(self) -> None:
        """
        Runs a selection loop over all consumers and forward
        their messages to the bus.
        """
        selector = self._selector
        bus_send = self._bus.send
        kill_switch = self._kill_switch_rx
        while self._running:
            selector_events = selector.select(timeout=0.1)
            for key, _ in selector_events:
                recv_fn = key.data
                if recv_fn is None:
                    assert key.fileobj is kill_switch, (
                        "Registered an object with None data that's not the kill switch"
                    )
                    assert kill_switch.recv(1) == b"0"
                    if not self._running:
                        break
                    # otherwise, kill_switch was used to reset selection, when consumers
                    # are registered or unregistered
                    continue

                msg = recv_fn()
                py_can_msg = Message(
                    arbitration_id=msg.arbitration_id,
                    is_extended_id=msg.is_extended_id,
                    data=msg.data,
                )
                bus_send(py_can_msg)
        _logger.info("Stopping sender thread, we've got terminated")
