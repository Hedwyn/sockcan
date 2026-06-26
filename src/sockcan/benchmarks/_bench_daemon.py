"""
Implements benchmarks for the userspace socket daemon fixture.

@date: 26.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from functools import partial
from time import time
from typing import TYPE_CHECKING, Callable

from can import Message

from sockcan._protocol import CAN_EFF_FLAG, CAN_FRAME_HEADER_STRUCT

if TYPE_CHECKING:
    import socket


# 50 frames × 16 bytes = 800 bytes; fits within both the default AF_CAN kernel
# recv buffer (~860 frames at ~248 bytes/frame with sk_buff overhead) and the
# daemon TCP socket recv buffer.
DAEMON_BATCH_SIZE = 50


def _pycan_recv_stream(
    recv_fn: Callable[[int], bytes],
    _: float | None = None,
    _header_unpack: Callable[[bytes], tuple[int, int, int]] = CAN_FRAME_HEADER_STRUCT.unpack_from,
    _time_fn: Callable[[], float] = time,
    _msg_size: int = 16,
    _can_eff_flag: int = CAN_EFF_FLAG,
) -> Message:
    """
    Replicates python-can SocketcanBus recv parsing on a stream socket.
    Returns can.Message to benchmark python-can's object construction overhead
    on the daemon transport.
    """
    cf = recv_fn(_msg_size)
    can_id, can_dlc, _ = _header_unpack(cf)
    is_extended = bool(can_id & _can_eff_flag)
    can_id &= 0x1FFFFFFF
    data = cf[8 : 8 + can_dlc]
    return Message(
        arbitration_id=can_id,
        data=data,
        is_extended_id=is_extended,
        timestamp=_time_fn(),
    )


def build_pycan_recv_stream(sock: socket.socket) -> Callable[[], Message]:
    """Builds a python-can compatible recv for a stream socket from the daemon."""
    return partial(_pycan_recv_stream, sock.recv)
