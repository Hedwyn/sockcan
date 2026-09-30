"""
Centralizes imports.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from ._protocol import (
    RecvFn,
    SendFn,
    SocketcanConfig,
    SocketcanFd,
    build_recv_func,
    build_send_func,
    connect_to_socketcan,
    disable_nagle,
)

__all__ = [
    "RecvFn",
    "SendFn",
    "SocketcanConfig",
    "SocketcanFd",
    "build_recv_func",
    "build_send_func",
    "connect_to_socketcan",
    "disable_nagle",
]
