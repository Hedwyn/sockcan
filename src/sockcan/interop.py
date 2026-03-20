"""
Interoperability features with python-can.

@date: 20.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import atexit
import platform
from contextlib import contextmanager
from typing import TYPE_CHECKING, Self

import can
from can.interfaces import BACKENDS
from can.typechecking import CanFilter

from sockcan import connect_to_socketcan
from sockcan._protocol import SocketcanConfig, SocketcanFd, build_recv_func, build_send_func
from sockcan.daemon import SocketcanServer
from sockcan.daemon._server import BusParameters

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


_global_socketcan_server: SocketcanServer | None = None


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
            if _global_socketcan_server is None:
                raise RuntimeError(
                    "You must pass a socket as there isn't one in the current context",
                )
            # TODO: filters
            socket = _global_socketcan_server.subscribe()
        self.socket = socket
        self.send = build_send_func(self.socket, expects_msg_cls=True)
        self.recv = build_recv_func(self.socket, use_native_timestamps=False)

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


def _hijack_python_can(*, system: str | None = None) -> tuple[str, str]:
    """
    Swaps python-can socketcan's implementation by ours,
    and returns the overriden values so that they can be easily restored.
    """
    system = system or platform.system()
    if system == "Windows":
        raise ValueError("Cannot use socketcan on Windows")
    # Registration format used by python-can is (import path, class name)
    former_factory = BACKENDS["socketcan"]
    BACKENDS["socketcan"] = ("sockcan.interop", "FastSocketcanBus")
    return former_factory


@contextmanager
def override_python_can(*, system: str | None = None) -> Generator[None, None, None]:
    """
    Overrides python-can's implementation with `FastSocketcanBus` as part of this
    context manager scope only.
    Use `hijack_python_can` to do it permanently
    """
    former_factory = _hijack_python_can(system=system)
    try:
        yield
    finally:
        BACKENDS["socketcan"] = former_factory


def hijack_python_can(*, system: str | None = None) -> None:
    """
    WARNING: mutating global shared state here.

    Overrides python-can's implementation with `FastSocketcanBus`
    Can be used as a way to use ot test this implementation in projects python-can
    based projects with a one-liner - or as a way to optimize the Linux implementation
    in a multi-platform project, while keeping the convenience / abstraction layer of python-can.
    """
    _ = _hijack_python_can(system=system)


def use_virtual_socketcan(
    bus_parameters: BusParameters | None = None,
    system: str | None = None,
    *,
    windows_only: bool = True,
) -> None:
    global _global_socketcan_server
    """
    WARNING: mutating global shared state here.  
    Allows using virtual socketcan on Windows.
    Starts a socketcanserver running on the real CAN interface
    defined by `bus_parameters`.
    Then injects a compatible socketcan implementation 
    in python-can's backend.
    Resources will be released on interpreter exit.
    """
    if _global_socketcan_server is not None and _global_socketcan_server.running:
        raise RuntimeError("Socketcanserver is already up and running")

    system = system or platform.system()
    if system != "Windows" and not windows_only:
        raise RuntimeError(
            "Not running on Windows, native socketcan should be used instead of virtual."
            "Disable `windows_only` if that's deliberate",
        )

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
    _global_socketcan_server = server
