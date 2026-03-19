"""
Implements the binary protoco defined by socketcan.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from functools import partial
from typing import Any, NamedTuple, NewType, Protocol, cast

SocketcanFd = NewType("SocketcanFd", socket.socket)

RECEIVED_TIMESTAMP_STRUCT = struct.Struct("@ll")


class CanMessage(NamedTuple):
    arbitration_id: int
    data: bytes
    is_extended_id: bool
    timestamp: float


def get_received_ancillary_buf_size() -> int:
    """
    Ancillary data size is platform dependant
    """
    try:
        from socket import CMSG_SPACE  # noqa: PLC0415

        return CMSG_SPACE(RECEIVED_TIMESTAMP_STRUCT.size)
    except ImportError:
        return 0


class LoopbackMode(Enum):
    """
    Whether we receive our own messages.

    If using .FOR_OTHER_SOCKS: others sockets on the same physical CAN device
    will receive our TX messages, but not us.
    If .ON: the socket will receive its own message.
    """

    OFF = auto()
    FOR_OTHER_SOCKS = auto()
    ON = auto()


class LazyCanMessage(NamedTuple):
    arbitration_id: int
    data: bytes
    raw_link_data: bytes


# --- Constants --- #
CANFD_MTU = 72
PF_CAN = 29
CAN_RAW = 1
SOL_CAN_BASE = 100
SOL_CAN_RAW = SOL_CAN_BASE + CAN_RAW
CAN_RAW_RECV_OWN_MSGS = 4

SO_TIMESTAMPNS = 35
CAN_EFF_FLAG = 0x80000000
CAN_RAW_LOOPBACK = 3
CAN_FRAME_HEADER_STRUCT = struct.Struct("=IBB2x")


@dataclass(slots=True, frozen=True)
class SocketcanConfig:
    """
    Options to configure the socketcan connection.
    """

    channel: str = "can0"
    loopback: LoopbackMode = LoopbackMode.OFF


def connect_to_socketcan(config: SocketcanConfig) -> SocketcanFd:
    """
    Creates a socketcan socket according to `config`.
    """
    sock = socket.socket(PF_CAN, socket.SOCK_RAW, CAN_RAW)
    sock.setsockopt(
        SOL_CAN_RAW,
        CAN_RAW_RECV_OWN_MSGS,
        1 if config.loopback == LoopbackMode.ON else 0,
    )
    sock.setsockopt(
        SOL_CAN_RAW,
        CAN_RAW_LOOPBACK,
        1 if config.loopback == LoopbackMode.FOR_OTHER_SOCKS else 0,
    )
    sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
    sock.bind((config.channel,))
    return cast("SocketcanFd", sock)


type _CMSG = tuple[int, int, bytes]


class RecvMsgFn(Protocol):
    """
    Any recv function which signatures complies with `recvmsg` method of sockets.
    """

    def __call__(
        self,
        bufsize: int,
        ancbufsize: int = 0,
        flags: int = 0,
        /,
    ) -> tuple[bytes, list[_CMSG], int, Any]: ...


type HeaderUnpack = Callable[[bytes], tuple[int, int, int]]
type TimestampUnpack = Callable[[bytes], tuple[int, int]]


def _socketcan_recv(
    recv_fn: RecvMsgFn,
    custom_mask: int = 0xFFFF_FFFF,
    exc_class: type[Exception] = OSError,
    # Note: all parameters below are injected as default arguments so they are accessed faster
    # they are not meants to be overriden, hence the prefix '__'
    _ancillary_data_size: int = get_received_ancillary_buf_size(),
    # Warning: these defaulted parameters are mainly there
    # to inject the constants in local scope and speed up their access.
    _header_unpack: HeaderUnpack = CAN_FRAME_HEADER_STRUCT.unpack_from,
    _timestamp_unpack: TimestampUnpack = RECEIVED_TIMESTAMP_STRUCT.unpack_from,
    _canfd_mtu: int = CANFD_MTU,
    _can_eff_flag: int = CAN_EFF_FLAG,
) -> CanMessage:
    """
    Captures a message from the CAN bus and runs partial decoding.
    Unpacks the data, arbitration ID and timestamp andf leaves all the other metadata undecoded.
    Metadata will only be decoded on access.
    """
    # Fetching the Arb ID, DLC and Data
    try:
        cf, ancillary_data, *_ = recv_fn(_canfd_mtu, _ancillary_data_size)
    except OSError as error:
        msg = f"Error receiving: {error.strerror}"
        raise exc_class(msg) from error

    can_id, can_dlc, _ = _header_unpack(cf)
    is_extended = bool(can_id & _can_eff_flag)
    can_id = can_id & 0x1FFFFFFF

    data = cf[8 : 8 + can_dlc]
    can_id = can_id & custom_mask

    assert ancillary_data, "ancillary data was not enabled on the socket"
    *_, cmsg_data = ancillary_data[0]

    seconds, nanoseconds = _timestamp_unpack(cmsg_data)
    timestamp = seconds + nanoseconds * 1e-9
    # updating data
    return CanMessage(can_id, data, is_extended, timestamp)


type RecvFn = Callable[[], CanMessage]


def build_recv_func(fd: SocketcanFd) -> RecvFn:
    """
    Builds the receive function for socketcan socket `fd`.
    """
    return partial(_socketcan_recv, fd.recvmsg)
