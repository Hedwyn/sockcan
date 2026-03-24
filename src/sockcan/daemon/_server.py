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
import os
import socket
import struct
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum, auto
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from selectors import EVENT_READ, DefaultSelector
from socketserver import ThreadingMixIn
from threading import Event, Thread
from typing import TYPE_CHECKING, NamedTuple, Self, cast
from urllib.parse import parse_qs, urlparse

import can
from can import BusState, Message

from sockcan import SendFn, SocketcanFd, build_recv_func, build_send_func

if TYPE_CHECKING:
    from _typeshed import FileDescriptorLike
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
    fd: FileDescriptorLike
    filters: set[int] | None


class SocketcanServer:
    """
    Listens to the CAN bus and dispatches the messages to all
    consumers that subscribed to the bus.

    Note: returned sockets should not be set to non-blocking mode as it might
    mess up with the internal selection logic.
    """

    def __init__(self, bus: BusABC | None = None) -> None:
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

    @property
    def bus(self) -> BusABC | None:
        return self._bus

    @property
    def is_virtual(self) -> bool:
        """
        Whether this bus is simulated.
        """
        return self._bus is None

    @property
    def running(self) -> bool:
        """
        Whether message forwarding is currently running.
        """
        return self._running

    def listen_to(self, fd: SocketcanFd, filters: set[int] | None = None) -> None:
        send_fn = build_send_func(fd, expects_msg_cls=False)
        recv_fn = build_recv_func(fd, use_native_timestamps=False)
        self._consumers.append(_Consumer(send_fn, fd, filters))
        self._selector.register(fd, events=EVENT_READ, data=recv_fn)
        if self.running:
            # interrupting selection to refresh selection targets
            self._kill_switch_tx.send(b"0")

    def subscribe(self, filters: set[int] | None = None) -> SocketcanFd:
        """
        Subscribes to the bus; returns a socketcan-like socket that can be used
        with socketcan protocol.
        If filters is passed, only messages with requested filters will be forwarded.
        """
        _ours, _theirs = socket.socketpair()
        ours = cast("SocketcanFd", _ours)
        theirs = cast("SocketcanFd", _theirs)
        self.listen_to(ours, filters=filters)
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
        _logger.info("Starting socketcanserver on %s", self._bus)
        if self._running:
            raise RuntimeError("Already started")
        self._threads.clear()
        self._running = True
        if direction != ServerDirection.TX_ONLY and not self.is_virtual:
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
            if self._bus is not None and self._bus.state == BusState.ERROR:
                _logger.error("CAN bus server ended on can operation error: %s", exc)  # noqa: TRY400

    def _run_rx(self) -> None:
        """
        Listens forever to the bus and forwards messages to all consumers.
        """
        assert self._bus is not None, "RX thread can only real in non-virtual mode"
        recv = self._bus.recv
        consumers = self._consumers
        while True:
            next_message = recv()
            assert next_message is not None
            can_id = next_message.arbitration_id
            data = next_message.data

            for sender, _, filters in consumers:
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

        except (can.CanOperationError, struct.error) as exc:
            # this path can be taken when closing the bus handle
            # on user side.
            # It should only be considered an error if the bus is in an error state.
            if self._bus and self._bus.state == BusState.ERROR:
                _logger.error("CAN bus server ended on can operation error: %s", exc)  # noqa: TRY400

    def _run_tx(self) -> None:
        """
        Runs a selection loop over all consumers and forward
        their messages to the bus.
        """
        selector = self._selector
        bus_send = self._bus.send if self._bus else None
        kill_switch = self._kill_switch_rx
        consumers = self._consumers
        while self._running:
            selector_events = selector.select(timeout=0.1)
            for key, _ in selector_events:
                fileobj = key.fileobj
                recv_fn = key.data
                if recv_fn is None:
                    assert fileobj is kill_switch, (
                        "Registered an object with None data that's not the kill switch"
                    )
                    assert kill_switch.recv(1) == b"0"
                    if not self._running:
                        break
                    _logger.info("Selection interrupted, resuming")
                    # otherwise, kill_switch was used to reset selection, when consumers
                    # are registered or unregistered
                    continue

                msg = recv_fn()
                # short-circuiting messages between our consumers
                for send_fn, fd, filters in consumers:
                    _ = filters
                    if fd is fileobj:
                        # skipping, not sending to ourselves
                        continue
                    send_fn(msg.arbitration_id, msg.data, msg.is_extended_id)

                if bus_send is not None:
                    py_can_msg = Message(
                        arbitration_id=msg.arbitration_id,
                        is_extended_id=msg.is_extended_id,
                        data=msg.data,
                    )
                    bus_send(py_can_msg)
        _logger.info("Stopping sender thread, we've got terminated")


class _UserError(Exception):
    pass


class SubscriptionHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        on_subscribe: Callable[[str], None],
        on_unsubscribe: Callable[[str], None],
    ) -> None:
        self.on_subscribe = on_subscribe
        self.on_unsubscribe = on_unsubscribe


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class SocketcanDaemon(BaseHTTPRequestHandler):
    """
    Proviedes the Socketcan service over an HTTP server.
    Allows registering buses that should be managed by this daemon.
    Forwards the CAN traffic using socketcan protocol to subscribers.
    Use /subscribe endpoint (with channel?=channel_name) to get a socket.
    """

    def __init__(self, url: str = "localhost", port: int = 8000) -> None:
        self._port = port
        self._url = url
        self._httpd = ThreadedHTTPServer((url, port), partial(self._RequestHandler, daemon=self))

        self._httpd_thread: Thread | None = None
        self._servers: dict[str, SocketcanServer] = {}

    class _RequestHandler(BaseHTTPRequestHandler):
        """
        The handler object that should be spawned on each request,
        as expected by HTTPServer.
        Keeps track of the parent daemon so that it can call the internal subscription logic.
        """

        def __init__(
            self,
            request: socket.socket | tuple[bytes, socket.socket],
            client_address: tuple[str, int],
            server: HTTPServer,
            daemon: SocketcanDaemon,
        ) -> None:
            self.daemon = daemon
            super().__init__(request, client_address, server)

        def do_GET(self) -> None:
            """
            Handler for GET requests.
            """
            # Parse the URL to get the path and the query parameters
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            params = parse_qs(parsed_url.query)

            if path == "/ping":
                print("Received ping")
                self.send_response_and_content(str(os.getpid()))
                return

            # Extract 'channel' parameter
            channel = params.get("channel", [None])[0]
            _logger.info("Received subscribe request for channel %s: %s", channel, path)
            if channel is None:
                self.send_response_and_content("Channel must be specified", code=400)
                return
            if (server := self.daemon._servers.get(channel)) is None:
                _logger.info("No server")
                self.send_response_and_content(
                    "Requested channel is not managed by this daemon",
                    code=400,
                )
                return

            if path == "/subscribe":
                self.send_response(101)
                self.send_header("Upgrade", "socketcan")
                self.send_header("Connection", "Upgrade")
                self.end_headers()
                self.wfile.flush()  # Ensure headers are actually on the wire
                sock = self.request
                assert server.running, "Server should have been started already"
                _logger.info("Listening to socket")
                server.listen_to(sock)
                _logger.info("Upgrading connection to socketcan")
                # TODO: wait for socket to close
                time.sleep(1000)

            elif path == "/unsubscribe":
                raise NotImplementedError

            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"404 Not Found: Provide a 'channel' parameter.")

        def send_response_and_content(self, message: str, code: int = 200) -> None:
            """
            Small helper to send a response with required headers as standalone.
            """
            self.send_response(code)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(message.encode())

    def register_virtual_bus(self, channel: str = "vcan0") -> None:
        """
        Registers a new virtual bus that should be available from this daemon.
        Communications within the bus are simulated and will only be seen by the consumers
        of that bus.
        """
        virtual_server = SocketcanServer()
        self._servers[channel] = SocketcanServer()
        if self.is_running:
            virtual_server.start()

    def register_bus(self, channel: str, interface: str, bitrate: int = 500_000) -> None:
        """
        Registers the parameters for a new bus that should be managed by this daemon.
        """
        bus = can.Bus(interface=interface, channel=channel, bitrate=bitrate)
        server = SocketcanServer(bus)
        self._servers[channel] = server

    def start_socketcan_servers(self) -> None:
        """
        Starts all the registered socketcan servers.
        """
        for channel, server in self._servers.items():
            if server.is_virtual:
                _logger.info("Starting virtual socketcan server on channel %s", channel)
            else:
                _logger.info("Starting socketcan server on channel %s", channel)
            server.start()

    @property
    def port(self) -> int:
        """
        The port uses by the HTTP server.
        """
        return self._port

    @property
    def url(self) -> str:
        """
        The URL to which the HTTP server is bound.
        """
        return self._url

    @property
    def is_running(self) -> bool:
        """
        Whether the daemon thread is currently running.
        """
        return self._httpd_thread is not None and self._httpd_thread.is_alive()

    def start(self) -> None:
        """
        Starts the daemon.
        """
        self.start_socketcan_servers()
        self._httpd_thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self._httpd_thread.start()

    def stop(self) -> None:
        """
        Stops the daemon thread.
        """
        for channel, server in self._servers.items():
            _logger.info("Stopping socketcanserver on channel %s", channel)
            server.stop()
            if bus := server.bus:
                bus.shutdown()

    def __enter__(self) -> Self:
        """
        Starts the daemon on entering the context manager.
        """
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        """
        Stops the daemon on exiting the context manager.
        """
        self.stop()
