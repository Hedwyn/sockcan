"""
Interoperability features with python-can.

@date: 20.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import atexit
import platform
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self

import can
from can.interfaces import BACKENDS
from can.typechecking import CanFilter

from sockcan import connect_to_socketcan
from sockcan._protocol import SocketcanConfig, SocketcanFd, build_recv_func, build_send_func
from sockcan.daemon import SocketcanServer, connect_socketcan_client, ping_daemon
from sockcan.daemon._server import BusParameters, SocketcanDaemon, ensure_socketcan_daemon_running

if TYPE_CHECKING:
    from collections.abc import Generator

    from can.typechecking import CanFilter, CanFilters, Channel


class FastSocketcanBus:
    """
    A socketcan bus pseudo-implementation that performs much faster
    than the one used by default by python-can.
    Use `hijack_python_can` to forcefully inject this class as the main
    socketcan implementation in python-can.
    """

    def __init__(
        self,
        channel: Channel,
        can_filters: CanFilters | None = None,
        **kwargs: object,
    ) -> None:
        _ = kwargs
        self._filters: list[CanFilter] = list(can_filters or [])
        # Note: actual socket is kept under .socket in SocketcanBus
        self._config = SocketcanConfig(channel=str(channel))
        self.socket = connect_to_socketcan(self._config)
        self.send = build_send_func(self.socket, expects_msg_cls=True)
        self.recv = build_recv_func(self.socket)

    def __enter__(self) -> Self:
        """
        Enables context manager protocol on this object as this is required
        by python-can. Returns oneself without side effects.
        """
        return self

    def __exit__(self, *_: object) -> None:
        """
        Closes the internal socket on exiting the scope.
        """
        self.socket.close()


@dataclass
class SocketcanDaemonConfig:
    """
    Used to define parameters whenever overriding python-can,
    as this is inherently global.
    If mode is `local`, userspace socketcan buses
    will use the a local thread.
    """

    mode: ServerMode = "local"
    allow_run_daemon_locally: bool = True
    host: str = "localhost"
    port: int = 8000
    local_server: SocketcanServer | None = None
    local_daemon: SocketcanDaemon | None = None
    linux_too: bool = False
    use_native_timestamps: bool = False


# --- Globals- required to inject this implementation into python-can --- #
_global_config = SocketcanDaemonConfig()
_local_servers: dict[str, SocketcanServer] = {}


class UserspaceSocketcanBus:
    """
    Allows talking to a virtual socketcan server running in userspace.
    Provides a socketcan interface on non-Unix platforms.
    """

    def __init__(
        self,
        channel: Channel,
        can_filters: CanFilters | None = None,
        socket: SocketcanFd | None = None,
        **kwargs: object,
    ) -> None:
        _ = kwargs
        self._filters: list[CanFilter] = list(can_filters or [])
        # Note: actual socket is kept under .socket in SocketcanBus
        self._config = SocketcanConfig(channel=str(channel))
        if socket is None:
            assert isinstance(channel, str)
            socket = self._get_socket(channel)
        self.socket = socket
        self.send = build_send_func(self.socket, expects_msg_cls=True)
        self.recv = build_recv_func(self.socket, use_native_timestamps=False)

    def fileno(self) -> int:
        return self.socket.fileno()

    def shutdown(self) -> None:
        self.socket.close()

    @classmethod
    def _get_socket(cls, channel: str) -> SocketcanFd:
        if _global_config.mode == "local":
            target_server = _local_servers.get(channel)
            if target_server is None:
                raise RuntimeError(f"Socketcanserver is not started on channel {channel}")
            return target_server.subscribe()

        # daemon mode
        if not ping_daemon(_global_config.host, _global_config.port):
            raise RuntimeError(
                "Daemon mode is used, but no daemon is running."
                "If you were intending to run daemon locally, "
                "use `ensure_socketcan_daemon_running()`",
            )
        return connect_socketcan_client(_global_config.host, _global_config.port, channel)

    def __enter__(self) -> Self:
        """
        Enables context manager protocol on this object as this is required
        by python-can. Returns oneself without side effects.
        """
        return self

    def __exit__(self, *_: object) -> None:
        """
        Closes the internal socket on exiting the scope.
        """
        self.socket.close()


def _hijack_python_can(
    bus_cls: type[FastSocketcanBus | UserspaceSocketcanBus] = FastSocketcanBus,
    system: str | None = None,
) -> tuple[str, str] | None:
    """
    Swaps python-can socketcan's implementation by ours,
    and returns the overriden values so that they can be easily restored.
    """
    system = system or platform.system()
    if system == "Windows":
        raise ValueError("Cannot use socketcan on Windows")
    # Registration format used by python-can is (import path, class name)
    former_factory = BACKENDS.get("socketcan", None)
    BACKENDS["socketcan"] = ("sockcan.interop", bus_cls.__name__)
    return former_factory


def _init_global_server(bus_parameters: BusParameters | None = None) -> SocketcanServer:
    bus_parameters = bus_parameters or BusParameters()
    bus = can.Bus(
        interface=bus_parameters.interface,
        channel=bus_parameters.channel,
        bitrate=bus_parameters.bitrate,
    )
    # making
    atexit.register(bus.shutdown)
    server = SocketcanServer(bus)
    server.start()
    atexit.register(server.stop)
    return server


def activate_userspace_socketcan(
    parameters: BusParameters | list[BusParameters] | None,
    config: SocketcanDaemonConfig | None = None,
    *,
    system: str | None = None,
) -> None:
    """
    WARNING: mutating global shared state here.
    Allows using virtual socketcan on Windows.
    Starts a socketcanserver running on the real CAN interface
    defined by `bus_parameters`.
    Then injects a compatible socketcan implementation
    in python-can's backend.
    Resources will be released on interpreter exit.
    """
    global _global_config
    config = config or _global_config
    _global_config = config
    system = system or platform.system()

    if system == "Linux" and not config.linux_too:
        raise RuntimeError(
            "Tried to enable userspace on Linux which supports socketcan natively."
            "If that's deliberate, enable `linux_too` in your config",
        )

    if system == "Windows" and config.use_native_timestamps:
        raise ValueError(
            "Native timestamps are not supported on windows."
            "Disable `use_native_timestamps` in your config",
        )
    if parameters is None:
        parameters = [BusParameters()]

    elif isinstance(parameters, BusParameters):
        parameters = [parameters]

    if config.mode == "local":
        for params in parameters:
            channel = params.channel
            if channel in _local_servers:
                raise RuntimeError(f"A server is already started for channel {channel}")
            server = _init_global_server(params)
            _local_servers[channel] = server

    elif config.mode == "daemon" and not ping_daemon(config.host, config.port):
        if not config.allow_run_daemon_locally:
            raise RuntimeError(
                "Daemon did not reply, and running daemon locally is disabled in config. "
                "If you want to run daemon locally, enabel `allow_run_daemon_locally`"
            )

        daemon = SocketcanDaemon(config.host, config.port)
        for params in parameters:
            daemon.register_bus(
                channel=params.channel,
                interface=params.interface,
                bitrate=params.bitrate,
                use_native_timestamps=config.use_native_timestamps,
            )
        daemon.start()
        atexit.register(daemon.stop)
    _hijack_python_can(UserspaceSocketcanBus, system=system)


@contextmanager
def override_python_can(
    bus_cls: type[FastSocketcanBus | UserspaceSocketcanBus] = FastSocketcanBus,
    system: str | None = None,
) -> Generator[None, None, None]:
    """
    Overrides python-can's implementation with `FastSocketcanBus` as part of this
    context manager scope only.
    Use `hijack_python_can` to do it permanently
    """
    former_factory = _hijack_python_can(bus_cls, system=system)
    try:
        yield
    finally:
        if former_factory:
            BACKENDS["socketcan"] = former_factory


def hijack_python_can(
    system: str | None = None,
) -> None:
    """
    WARNING: mutating global shared state here.

    Overrides python-can's implementation with `FastSocketcanBus`
    Can be used as a way to use ot test this implementation in projects python-can
    based projects with a one-liner - or as a way to optimize the Linux implementation
    in a multi-platform project, while keeping the convenience / abstraction layer of python-can.
    """
    _ = _hijack_python_can(bus_cls=FastSocketcanBus, system=system)


type ServerMode = Literal["local", "daemon"]
