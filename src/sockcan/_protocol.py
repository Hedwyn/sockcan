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
from functools import lru_cache, partial
from time import time_ns
from typing import Any, Literal, NamedTuple, NewType, Protocol, cast, overload

SocketcanFd = NewType("SocketcanFd", socket.socket)

RECEIVED_TIMESTAMP_STRUCT = struct.Struct("@ll")


class CanMessageProtocol(Protocol):
    arbitration_id: int
    data: bytes | bytearray
    is_extended_id: bool
    timestamp: float


@dataclass(slots=True)
class CanMessage:
    """
    Container for CAN message data that matches field naming
    use by python-can's Message.
    """

    arbitration_id: int
    data: bytes
    is_extended_id: bool
    timestamp: float

    def __str__(self) -> str:
        payload = " ".join([f"{b:02x}" for b in self.data])
        return f"{self.arbitration_id:08x}:{payload}"


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
SOCKT_CAN_STRUCT_SIZE = 16
SO_TIMESTAMPNS = 35
CAN_EFF_FLAG = 0x80000000
CAN_RAW_LOOPBACK = 3
CAN_FRAME_HEADER_STRUCT = struct.Struct("=IBB2x")
CAN_EXTENSION_MASK = 0x07FFF800


@dataclass(slots=True, frozen=True)
class SocketcanConfig:
    """
    Options to configure the socketcan connection.
    """

    channel: str = "can0"
    loopback: LoopbackMode = LoopbackMode.FOR_OTHER_SOCKS


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

type HeaderPack = Callable[[int, int, int], bytes]


def _socketcan_recv(
    recv_fn: RecvMsgFn,
    timeout: float | None = None,
    exc_class: type[Exception] = OSError,
    # Note: all parameters below are injected as default arguments so they are accessed faster
    # they are not meants to be overriden, hence the prefix '__'
    _ancillary_data_size: int = get_received_ancillary_buf_size(),
    # Warning: these defaulted parameters are mainly there
    # to inject the constants in local scope and speed up their access.
    _header_unpack: HeaderUnpack = CAN_FRAME_HEADER_STRUCT.unpack_from,
    _time_fn: Callable[[], int] = time_ns,
    _timestamp_unpack: TimestampUnpack = RECEIVED_TIMESTAMP_STRUCT.unpack_from,
    _canfd_mtu: int = CANFD_MTU,
    _can_eff_flag: int = CAN_EFF_FLAG,
) -> CanMessage:
    """
    Captures a message from the CAN bus and runs partial decoding.
    Unpacks the data, arbitration ID and timestamp andf leaves all the other metadata undecoded.
    Metadata will only be decoded on access.
    """
    if timeout is not None and timeout > 0:
        raise NotImplementedError
    # Fetching the Arb ID, DLC and Data
    try:
        cf, ancillary_data, *_ = recv_fn(_canfd_mtu, _ancillary_data_size)
    except OSError as error:
        msg = f"Error receiving: {error.strerror}"
        raise exc_class(msg) from error

    can_id, can_dlc, _ = _header_unpack(cf)
    # is_extended = bool(can_id & _can_eff_flag)
    # Note: `'not not' is faster than bool
    is_extended = not not (can_id & _can_eff_flag)  # noqa: SIM208
    can_id = can_id & 0x1FFFFFFF

    data = cf[8 : 8 + can_dlc]

    if _ancillary_data_size > 0:
        assert ancillary_data, "ancillary data was not enabled on the socket"
        cmsg_data = ancillary_data[0][2]

        seconds, nanoseconds = _timestamp_unpack(cmsg_data)
        timestamp = seconds + nanoseconds * 1e-9
    else:
        timestamp = _time_fn() * 1e-9

    # updating data
    return CanMessage(can_id, data, is_extended, timestamp)


type _RecvFn = Callable[[int], bytes]


def _socketcan_recv_stream(
    recv_fn: _RecvFn,
    timeout: float | None = None,
    exc_class: type[Exception] = OSError,
    # Note: all parameters below are injected as default arguments so they are accessed faster
    # they are not meants to be overriden, hence the prefix '__'
    # Warning: these defaulted parameters are mainly there
    # to inject the constants in local scope and speed up their access.
    _header_unpack: HeaderUnpack = CAN_FRAME_HEADER_STRUCT.unpack_from,
    _time_fn: Callable[[], int] = time_ns,
    _msg_size: int = 16,
    _can_eff_flag: int = CAN_EFF_FLAG,
) -> CanMessage:
    """
    Captures a message from the CAN bus and runs partial decoding.
    Unpacks the data, arbitration ID and timestamp andf leaves all the other metadata undecoded.
    Metadata will only be decoded on access.
    """
    if timeout is not None and timeout > 0:
        raise NotImplementedError
    # Fetching the Arb ID, DLC and Data
    try:
        cf = recv_fn(_msg_size)
    except OSError as error:
        msg = f"Error receiving: {error.strerror}"
        raise exc_class(msg) from error
    can_id, can_dlc, _ = _header_unpack(cf)

    # Note: `'not not' is faster than bool
    is_extended = not not (can_id & _can_eff_flag)  # noqa: SIM208
    can_id = can_id & 0x1FFFFFFF

    data = cf[8 : 8 + can_dlc]

    timestamp = _time_fn() * 1e-9

    # updating data
    return CanMessage(can_id, data, is_extended, timestamp)


type RecvFn = Callable[[], CanMessage]


def build_recv_func(
    fd: SocketcanFd,
    *,
    use_native_timestamps: bool = True,
    is_stream: bool = False,
) -> RecvFn:
    """
    Builds the receive function for socketcan socket `fd`.
    """
    ancillary_data_size = get_received_ancillary_buf_size() if use_native_timestamps else 0
    if is_stream:
        return partial(_socketcan_recv_stream, fd.recv)

    return partial(_socketcan_recv, fd.recvmsg, _ancillary_data_size=ancillary_data_size)


@lru_cache(maxsize=1024)
def build_tx_header(
    can_id: int,
    dlc: int,
    *,
    is_extended_id: bool = False,
    _header_pack: HeaderPack = CAN_FRAME_HEADER_STRUCT.pack,
    _can_eff_flag: int = CAN_EFF_FLAG,
    _can_extension_mask: int = CAN_EXTENSION_MASK,
) -> bytes:
    """
    Encodes the CAN header bytes for a given ID and DLC
    """
    if is_extended_id or (can_id & _can_extension_mask) > 0:
        can_id |= _can_eff_flag

    return _header_pack(can_id, dlc, 0)


class SendMsgFn(Protocol):
    def __call__(self, data: bytes, flags: int = 0, /) -> int: ...


def _socketcan_send(
    send_fn: SendMsgFn,
    arbitration_id: int,
    data: bytes | bytearray,
    is_extended: bool = False,  # noqa: FBT001, FBT002
) -> None:
    """
    Sends a can message specified with `data` and `arbitration_id`
    using the socket send function `send_fn`
    """
    header = build_tx_header(arbitration_id, data.__len__(), is_extended_id=is_extended)
    payload = header + data.ljust(8, b"\0")
    send_fn(payload)


def _socketcan_send_msg(
    send_fn: SendMsgFn,
    message: CanMessageProtocol,
) -> None:
    """
    Sends a can message specified with `data` and `arbitration_id`
    using the socket send function `send_fn`
    """
    header = build_tx_header(
        message.arbitration_id,
        message.data.__len__(),
        is_extended_id=message.is_extended_id,
    )
    send_fn(header + message.data.ljust(8, b"\0"))


# SendFn -> to pass directly arbitration_id, data and extended flag as args
# MessageSendFn -> when passing a container implementing CanMessageProtocol to the sender
type SendFn = Callable[[int, bytes, bool], None]
type MessageSendFn = Callable[[CanMessageProtocol], None]


@overload
def build_send_func(fd: SocketcanFd, *, expects_msg_cls: Literal[True]) -> MessageSendFn: ...


@overload
def build_send_func(fd: SocketcanFd, *, expects_msg_cls: Literal[False]) -> SendFn: ...


def build_send_func(fd: SocketcanFd, *, expects_msg_cls: bool = False) -> SendFn | MessageSendFn:
    """
    Builds the send function for socketcan socket `fd`.
    """
    if expects_msg_cls:
        return partial(_socketcan_send_msg, fd.send)
    else:
        return partial(_socketcan_send, fd.send)
