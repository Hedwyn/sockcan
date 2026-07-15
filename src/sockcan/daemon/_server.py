"""
Creates a socketcan server over an arbitrary CAN interface.
Provides a producer/consumer interface, allowing to create Socketcan-like
connections over any CAN interface.
For interfaces like pcan on Windows, this also allows concurrent connection on the CAN bus.

@date: 20.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import atexit
import contextlib
import errno
import json
import logging
import os
import platform
import socket
import struct
import time
import warnings
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum, auto
from functools import cache, partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from selectors import EVENT_READ, DefaultSelector
from socketserver import ThreadingMixIn
from threading import Event, Thread
from typing import TYPE_CHECKING, NamedTuple, Self, cast
from urllib.parse import parse_qs, unquote, urlparse

import can
from can import BusState, Message

from sockcan import SendFn, SocketcanFd, build_recv_func, build_send_func

from ._client import ping_daemon

if TYPE_CHECKING:
    from _typeshed import FileDescriptorLike
    from can import BusABC
    from can.typechecking import CanFilter

_logger = logging.getLogger(__name__)


def _normalize_filters(
    filters: set[int] | list[CanFilter] | None,
) -> list[CanFilter] | None:
    """
    Normalizes filters to list[CanFilter] format.
    Converts set[int] to list of CanFilter dicts with full masks.
    """
    if filters is None:
        return None

    # If it's already a list of CanFilter dicts, return as is
    if isinstance(filters, list):
        return filters

    # If it's a set of ints, convert to list of CanFilter dicts
    if isinstance(filters, set):
        return [{"can_id": can_id, "can_mask": 0x1FFFFFFF} for can_id in filters]
    raise TypeError(f"Unsupported filter type: {type(filters)}")


def _frame_matches(
    filters: list[CanFilter] | None,
    can_id: int,
    *,
    is_extended: bool,
) -> bool:
    """
    Returns whether a frame passes a consumer's filter set.

    A consumer with no filters accepts every frame. Otherwise the frame is
    accepted as soon as it matches any single filter, using python-can's
    mask-based formula ``(can_id & can_mask) == (filter_can_id & can_mask)``.
    When a filter carries an ``extended`` flag, the frame's extended bit must
    match it too; a filter without that flag matches frames of either kind.
    """
    if not filters:
        return True
    for can_filter in filters:
        filter_can_mask = can_filter["can_mask"]
        if (
            (can_id & filter_can_mask) == (can_filter["can_id"] & filter_can_mask)
            and "extended" not in can_filter
        ) or is_extended == can_filter.get("extended", False):
            return True
    return False


def _inet_socket_pair(
    sock_type: socket.SocketKind = socket.SOCK_DGRAM,
) -> tuple[socket.socket, socket.socket]:
    """
    Emulate socket.socketpair() for AF_INET, for SOCK_DGRAM (and SOCK_STREAM),
    since socket.socketpair() only supports AF_INET natively as a Windows
    fallback for SOCK_STREAM; AF_UNIX-backed platforms (e.g. Linux) reject
    AF_INET outright.

    Returns a tuple of two connected sockets (a, b) on 127.0.0.1.
    """
    if sock_type == socket.SOCK_STREAM:
        return _inet_stream_socket_pair()

    conn1 = socket.socket(socket.AF_INET, sock_type)
    conn2 = socket.socket(socket.AF_INET, sock_type)

    try:
        conn1.bind(("127.0.0.1", 0))
        conn2.bind(("127.0.0.1", 0))

        conn1.connect(conn2.getsockname())
        conn2.connect(conn1.getsockname())
    except OSError:
        conn1.close()
        conn2.close()
        raise
    return conn1, conn2


def _inet_stream_socket_pair() -> tuple[socket.socket, socket.socket]:
    """
    Emulate socket.socketpair() for AF_INET/SOCK_STREAM.

    SOCK_STREAM has no symmetric bind+connect trick like SOCK_DGRAM: a
    merely-bound socket isn't listening, so connecting to it is refused.
    Use a short-lived listener to accept one connection instead.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        conn1.connect(listener.getsockname())
        conn2, _ = listener.accept()
    except OSError:
        conn1.close()
        raise
    finally:
        listener.close()
    return conn1, conn2


ENDPOINT_NOT_CONNECTED_ERRNO = errno.ENOTCONN
CONNECTION_REFUSED_ERRRNO = errno.ECONNREFUSED


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
    virtual: bool = False


type RecvFn = Callable[[], Message]


class _Consumer(NamedTuple):
    """
    Stores the information required about a consumer in a minimal format.
    """

    sender: SendFn
    fd: FileDescriptorLike
    filters: list[CanFilter] | None


class SocketcanServer:
    """
    Listens to the CAN bus and dispatches the messages to all
    consumers that subscribed to the bus.

    Note: returned sockets should not be set to non-blocking mode as it might
    mess up with the internal selection logic.
    """

    def __init__(
        self,
        bus: BusABC | None = None,
        *,
        use_native_timestamps: bool = False,
        use_stream: bool = False,
        contention_time: float | None = None,
    ) -> None:
        """
        Wraps the passed `bus`. For interfaces that do not support concurrency
        (e.g. pcan windows), this object should be the only one accessing the real bus.

        If `use_stream`  is enabled, uses `SOCK_STREAM` instead of `SOCK_DGRAM`
        when creating sockets.

        If `contention_time` is passed, the configured delay (in seconds)
        will be applied between messages.
        """
        self._consumers: list[_Consumer] = []
        self._kill_switch = Event
        self._bus = bus
        self._selector = DefaultSelector()
        self._kill_switch_rx, self._kill_switch_tx = socket.socketpair()
        self._running: bool = False
        self._threads: list[Thread] = []
        self.use_native_timestamps = use_native_timestamps
        self._selector.register(self._kill_switch_rx, events=EVENT_READ, data=None)
        self._use_stream = use_stream
        self.contention_time = contention_time

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

    def listen_to(
        self,
        fd: SocketcanFd,
        filters: set[int] | list[CanFilter] | None = None,
        *,
        force_stream: bool = False,
    ) -> None:
        # Normalize filters to list[CanFilter] format
        normalized_filters = _normalize_filters(filters)
        use_stream = self._use_stream or force_stream

        # sanity checks
        if not use_stream and fd.type != socket.SOCK_DGRAM:
            raise RuntimeError(
                "Server is configured to DGRAM mode, cannot listen to SOCK_STREAM socket",
            )

        send_fn = build_send_func(fd, expects_msg_cls=False)
        recv_fn = build_recv_func(
            fd,
            use_native_timestamps=self.use_native_timestamps,
            is_stream=use_stream,
        )
        _logger.info("Registering new consumer with filters: %s", normalized_filters)
        self._consumers.append(_Consumer(send_fn, fd, normalized_filters))
        # KeyError is expected if Socket is already registered (e.g., HTTP upgrade socket)
        # This is fine, we'll handle it in the HTTP handler thread
        with contextlib.suppress(KeyError):
            self._selector.register(fd, events=EVENT_READ, data=recv_fn)
        if self.running:
            # interrupting selection to refresh selection targets
            self._kill_switch_tx.send(b"0")

    def subscribe(self, filters: set[int] | list[CanFilter] | None = None) -> SocketcanFd:
        """
        Subscribes to the bus; returns a socketcan-like socket that can be used
        with socketcan protocol.
        If filters is passed, only messages with requested filters will be forwarded.
        Accepts either set[int] for exact matching or list[CanFilter] for mask-based filtering.
        """
        _logger.info("Subscribing with filters: %s", filters)
        # Windows sanity checks:
        # Modern windows *do* have AF_UNIX socket types.
        # However, 1) this not true for older versions and 2)
        # a lot of python distributions do not include it at build time
        # making this unusuable from Python even if the system support it
        # (starting with the Python exe shipped by uv itself)
        force_stream = False
        if self._use_stream:
            _ours, _theirs = _inet_socket_pair(socket.SOCK_STREAM)
        elif platform.system() == "Windows" and not hasattr(socket, "AF_UNIX"):
            msg = (
                "No support for AF_UNIX sockets: your Windows system might be too old, or "
                "your python distribution might have been compiled without support for it. "
                "Using AF_INET instead."
            )
            warnings.warn(msg, stacklevel=2)
            _ours, _theirs = _inet_socket_pair()
            force_stream = True
        else:
            _ours, _theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        ours = cast("SocketcanFd", _ours)
        theirs = cast("SocketcanFd", _theirs)
        self.listen_to(ours, filters=filters, force_stream=force_stream)
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
        _logger.info("Stopping socketcanserver on %s", self._bus)
        self._kill_switch_tx.send(b"0")
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
        except (can.CanOperationError, OSError, ValueError) as exc:
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
        closed_connections: list[_Consumer] = []
        is_stream = self._use_stream
        while True:
            if closed_connections:
                for consumer in closed_connections:
                    consumers.remove(consumer)
                closed_connections.clear()

            next_message = recv()
            assert next_message is not None
            can_id = next_message.arbitration_id
            data = next_message.data
            is_extended = next_message.is_extended_id

            for consumer in consumers:
                sender, _, filters = consumer
                if not _frame_matches(filters, can_id, is_extended=is_extended):
                    continue

                try:
                    sender(can_id, data, next_message.is_extended_id, None)
                except BrokenPipeError:
                    _logger.info("Client closed connection")
                    closed_connections.append(consumer)
                except OSError as exc:
                    if not is_stream and exc.errno not in [
                        ENDPOINT_NOT_CONNECTED_ERRNO,
                        CONNECTION_REFUSED_ERRRNO,
                    ]:
                        raise
                    # for DGRAM sockets, just ignoring,
                    # this errno means the other side is not listening

    def run_tx(self) -> None:
        """
        Main thread function.
        Catches bus errors and reports them if they are not coming
        from closing the wrapped bus handle.
        """
        try:
            self._run_tx()

        except (can.CanOperationError, struct.error, ValueError) as exc:
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
        contention_time = self.contention_time
        kill_switch = self._kill_switch_rx
        consumers = self._consumers
        is_stream = self._use_stream
        sleep = time.sleep
        while self._running:
            selector_events = selector.select()
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

                try:
                    msg = recv_fn()
                except (struct.error, OSError):
                    # A consumer disconnecting is normal: the recv helper wraps the
                    # underlying socket error (e.g. ConnectionResetError on an abrupt
                    # client close) into a plain OSError, so catch the whole OSError
                    # family here. Unregister that consumer and keep serving the rest
                    # instead of letting the TX thread die.
                    _logger.info("Bus closed")
                    selector.unregister(key.fileobj)
                    continue

                # short-circuiting messages between our consumers
                for send_fn, fd, filters in consumers:
                    if fd is fileobj:
                        # skipping, not sending to ourselves
                        continue
                    if not _frame_matches(
                        filters,
                        msg.arbitration_id,
                        is_extended=msg.is_extended_id,
                    ):
                        # Honour the destination consumer's filters on the
                        # consumer-to-consumer loopback path too, just like the
                        # real-bus RX path does. Without this, a consumer that
                        # subscribed with filters still receives every frame any
                        # other consumer sends.
                        continue
                    try:
                        send_fn(msg.arbitration_id, msg.data, msg.is_extended_id, None)
                    except (BrokenPipeError, ConnectionResetError):
                        continue
                    except OSError as exc:
                        if not is_stream and exc.errno not in [
                            ENDPOINT_NOT_CONNECTED_ERRNO,
                            CONNECTION_REFUSED_ERRRNO,
                        ]:
                            raise

                if bus_send is not None:
                    py_can_msg = Message(
                        arbitration_id=msg.arbitration_id,
                        is_extended_id=msg.is_extended_id,
                        data=msg.data,
                    )
                    if contention_time:
                        sleep(contention_time)
                    bus_send(py_can_msg)
        _logger.info("Stopping sender thread, we've got terminated")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


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
        self.kill_switch = Event()
        super().__init__(request, client_address, server)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        statement = "%s - - " + format
        _logger.info(statement, self.client_address[0], *args)

    def do_GET(self) -> None:
        """
        Handler for GET requests.
        """
        # Parse the URL to get the path and the query parameters
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        params = parse_qs(parsed_url.query)

        if path == "/ping":
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
            filters = None
            if filter_str := params.get("filters", [None])[0]:
                try:
                    # Filters are URL-encoded JSON
                    filters_json = unquote(filter_str)
                    filters = json.loads(filters_json)
                    # Validate that it's a list of filter dicts
                    if not isinstance(filters, list):
                        raise TypeError("Filters should be a list")
                except (json.JSONDecodeError, ValueError) as e:
                    self.send_response_and_content(f"Invalid filters format: {e}", code=400)
                    return

            self.send_response(101)
            self.send_header("Upgrade", "socketcan")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()  # Ensure headers are actually on the wire
            sock = self.request
            assert server.running, "Server should have been started already"
            _logger.info("Listening to socket with filters: %s", filters)
            server.listen_to(sock, filters=filters)
            _logger.info("Upgrading connection to socketcan")
            # Keep the connection open for socketcan communication
            # The SocketcanServer's TX/RX threads will handle the actual communication
            # We need to keep the handler from returning to prevent the socket from being closed.
            # Use a long timeout to support long-running connections (e.g., benchmarks)
            # TODO: Properly implement HTTP upgrade by detaching the socket
            self.kill_switch.wait()

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


class SocketcanDaemon(BaseHTTPRequestHandler):
    """
    Proviedes the Socketcan service over an HTTP server.
    Allows registering buses that should be managed by this daemon.
    Forwards the CAN traffic using socketcan protocol to subscribers.
    Use /subscribe endpoint (with channel?=channel_name) to get a socket.
    """

    def __init__(
        self, host: str = "localhost", port: int = 0, contention_time: float | None = None
    ) -> None:
        self._host = host
        self._httpd = ThreadedHTTPServer((host, port), partial(_RequestHandler, daemon=self))
        self._port = self._httpd.socket.getsockname()[1]
        _logger.info("Socketcan daemon bound to %s:%d", host, self._port)
        self.contention_time = contention_time

        self._httpd_thread: Thread | None = None
        self._servers: dict[str, SocketcanServer] = {}

    def register_virtual_bus(self, channel: str = "vcan0") -> None:
        """
        Registers a new virtual bus that should be available from this daemon.
        Communications within the bus are simulated and will only be seen by the consumers
        of that bus.
        """
        _logger.info("Registering virtual bus on channel %s", channel)
        virtual_server = SocketcanServer(use_stream=True, contention_time=self.contention_time)
        self._servers[channel] = virtual_server
        if self.is_running:
            virtual_server.start()

    def register_bus(
        self,
        channel: str,
        interface: str,
        bitrate: int = 500_000,
        *,
        use_native_timestamps: bool = False,
    ) -> None:
        """
        Registers the parameters for a new bus that should be managed by this daemon.
        """
        _logger.info(
            "Registering bus on channel %s (interface=%s, bitrate=%d)",
            channel,
            interface,
            bitrate,
        )
        bus = can.Bus(interface=interface, channel=channel, bitrate=bitrate)
        server = SocketcanServer(
            bus,
            use_native_timestamps=use_native_timestamps,
            use_stream=True,
            contention_time=self.contention_time,
        )
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
        return self._host

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
        _logger.info("Starting socketcan daemon on %s:%d", self._host, self._port)
        self.start_socketcan_servers()
        self._httpd_thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self._httpd_thread.start()

    def stop(self) -> None:
        """
        Stops the daemon thread.
        """
        self._httpd.shutdown()
        if self._httpd_thread is not None:
            self._httpd_thread.join()
        self._httpd.server_close()
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


@cache
def start_daemon_globally(host: str = "localhost", port: int = 0) -> SocketcanDaemon:
    daemon = SocketcanDaemon(host=host, port=port)
    daemon.start()
    atexit.register(daemon.stop)
    return daemon


def ensure_socketcan_daemon_running(
    host: str = "localhost",
    port: int = 0,
) -> SocketcanDaemon | None:
    if port and ping_daemon(host, port):
        _logger.info("Daemon is already run by another process, no need to start it here")
        return None
    return start_daemon_globally(host, port)
