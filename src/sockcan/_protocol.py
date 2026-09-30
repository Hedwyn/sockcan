"""
Implements the binary protoco defined by socketcan.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import logging
import socket
import struct
from collections.abc import Buffer, Callable
from dataclasses import dataclass
from enum import Enum, auto
from functools import lru_cache, partial
from time import time_ns
from typing import Any, Literal, NamedTuple, NewType, Protocol, cast, overload

_logger = logging.getLogger(__name__)

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


def disable_nagle(sock: socket.socket) -> None:
    """
    Turns off Nagle's algorithm on `sock`, when it applies to it.

    Frames are 16 bytes, so Nagle would hold every one of them back until the
    previous one is acknowledged, then release the lot as a burst. That trades the
    pacing the caller asked for against a stall of up to a round-trip - fatal for
    a protocol whose consumers time their frames. Only TCP sockets are concerned;
    anything else (AF_UNIX, datagram) is left untouched.
    """
    if sock.family not in (socket.AF_INET, socket.AF_INET6) or sock.type != socket.SOCK_STREAM:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError as error:
        # Not worth failing a working connection over: Nagle only costs latency.
        _logger.warning("Could not disable Nagle's algorithm: %s", error)


def get_received_ancillary_buf_size() -> int:
    """
    Ancillary data size is platform dependant
    """
    if (cmsg_space := getattr(socket, "CMSG_SPACE", None)) is None:
        return 0
    return cmsg_space(RECEIVED_TIMESTAMP_STRUCT.size)


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


def _complete_frame(
    recv_fn: _RecvFn,
    first_chunk: bytes,
    exc_class: type[Exception],
    msg_size: int,
) -> bytes:
    """
    Slow path of `_socketcan_recv_stream`, for a frame split across several reads.

    A stream socket may return less than the requested size. Decoding a short read as
    if it were a whole frame would both mis-decode it and leave the remaining bytes in
    the stream, shifting every subsequent frame on that connection - that desync is
    permanent, so the frame has to be completed here instead.
    """
    chunks = [first_chunk]
    received = len(first_chunk)
    while received < msg_size:
        try:
            chunk = recv_fn(msg_size - received)
        except OSError as error:
            raise exc_class(
                f"Error receiving: {error.strerror} "
                f"(mid-frame, got {received} bytes out of {msg_size})",
            ) from error
        if not chunk:
            raise exc_class(
                f"Connection closed by peer mid-frame: got {received} bytes out of {msg_size}",
            )
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


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
    # Fetching the Arb ID, DLC and Data
    try:
        cf = recv_fn(_msg_size)
    except OSError as error:
        msg = f"Error receiving: {error.strerror}"
        raise exc_class(msg) from error
    if not cf:
        raise exc_class("Connection closed by peer")
    if len(cf) < _msg_size:
        # Slow path: a stream socket is free to hand back less than we asked for.
        cf = _complete_frame(recv_fn, cf, exc_class, _msg_size)
    can_id, can_dlc, _ = _header_unpack(cf)

    # Note: `'not not' is faster than bool
    is_extended = not not (can_id & _can_eff_flag)  # noqa: SIM208
    can_id = can_id & 0x1FFFFFFF

    data = cf[8 : 8 + can_dlc]

    timestamp = _time_fn() * 1e-9

    # updating data
    return CanMessage(can_id, data, is_extended, timestamp)


class RecvFn(Protocol):
    def __call__(self, timeout: float | None = None) -> CanMessage: ...


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

    recvmsg = getattr(fd, "recvmsg", None)
    if recvmsg is None:
        raise SystemError("recvmsg not available on your system")

    return partial(_socketcan_recv, recvmsg, _ancillary_data_size=ancillary_data_size)


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
    # `data` is deliberately loose: the frame completion path hands over a memoryview
    # of the payload rather than slicing a fresh bytes object out of it.
    def __call__(self, data: Buffer, flags: int = 0, /) -> int: ...


# How many consecutive zero-progress attempts a half-written frame is given before the
# consumer is declared unreachable. Stream consumers carry SO_SNDTIMEO, so this bounds
# how long a stuck one may hold the sender: `send_timeout` * that many.
STREAM_SEND_ATTEMPTS = 5


def _complete_send(
    send_fn: SendMsgFn,
    payload: bytes,
    sent: int,
    msg_size: int,
    attempts: int,
) -> None:
    """
    Slow path of the stream senders, for a frame the socket only accepted part of.

    A stream socket may accept less than the whole payload. Those bytes are on the wire
    already and cannot be taken back, so the frame has to be finished: abandoning it here
    would shift every subsequent frame on that connection, and re-queueing it for a later
    retry - which is what the backlog path does on a timeout - would put its first bytes
    on the wire twice. Bounded by `attempts` consecutive attempts without progress, after
    which the connection is unusable and is reported as such, rather than left desynced.

    Note that a send refused outright (nothing written, hence a raised `TimeoutError`)
    never reaches here: that frame is not committed, so it can safely be queued and
    retried whole by the caller.
    """
    view = memoryview(payload)
    remaining_attempts = attempts
    while sent < msg_size:
        try:
            written = send_fn(view[sent:])
        except (TimeoutError, BlockingIOError):
            written = 0
        if written:
            sent += written
            remaining_attempts = attempts
            continue
        remaining_attempts -= 1
        if remaining_attempts <= 0:
            raise OSError(
                f"Consumer stopped accepting data mid-frame: "
                f"{sent} bytes out of {msg_size} written, connection is out of sync",
            )


def _socketcan_send(
    send_fn: SendMsgFn,
    arbitration_id: int,
    data: bytes | bytearray,
    is_extended: bool = False,  # noqa: FBT001, FBT002
    timeout: float | None = None,
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
    timeout: float | None = None,
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


def _socketcan_send_stream(
    send_fn: SendMsgFn,
    arbitration_id: int,
    data: bytes | bytearray,
    is_extended: bool = False,  # noqa: FBT001, FBT002
    timeout: float | None = None,
    # Note: all parameters below are injected as default arguments so they are accessed faster
    # Warning: these defaulted parameters are mainly there
    # to inject the constants in local scope and speed up their access.
    _msg_size: int = 16,
    _attempts: int = STREAM_SEND_ATTEMPTS,
) -> None:
    """
    Stream-socket variant of `_socketcan_send`: completes the frame if the socket
    only took part of it, since a stream gives no framing of its own.
    """
    header = build_tx_header(arbitration_id, data.__len__(), is_extended_id=is_extended)
    payload = header + data.ljust(8, b"\0")
    sent = send_fn(payload)
    if sent < _msg_size:
        _complete_send(send_fn, payload, sent, _msg_size, _attempts)


def _socketcan_send_msg_stream(
    send_fn: SendMsgFn,
    message: CanMessageProtocol,
    timeout: float | None = None,
    # Note: all parameters below are injected as default arguments so they are accessed faster
    # Warning: these defaulted parameters are mainly there
    # to inject the constants in local scope and speed up their access.
    _msg_size: int = 16,
    _attempts: int = STREAM_SEND_ATTEMPTS,
) -> None:
    """
    Stream-socket variant of `_socketcan_send_msg`: completes the frame if the socket
    only took part of it, since a stream gives no framing of its own.
    """
    header = build_tx_header(
        message.arbitration_id,
        message.data.__len__(),
        is_extended_id=message.is_extended_id,
    )
    payload = header + message.data.ljust(8, b"\0")
    sent = send_fn(payload)
    if sent < _msg_size:
        _complete_send(send_fn, payload, sent, _msg_size, _attempts)


# SendFn -> to pass directly arbitration_id, data and extended flag as args
# MessageSendFn -> when passing a container implementing CanMessageProtocol to the sender
type SendFn = Callable[[int, bytes | bytearray, bool, float | None], None]
type MessageSendFn = Callable[[CanMessageProtocol, float | None], None]


@overload
def build_send_func(
    fd: SocketcanFd,
    *,
    expects_msg_cls: Literal[True],
    is_stream: bool = False,
) -> MessageSendFn: ...


@overload
def build_send_func(
    fd: SocketcanFd,
    *,
    expects_msg_cls: Literal[False],
    is_stream: bool = False,
) -> SendFn: ...


def build_send_func(
    fd: SocketcanFd,
    *,
    expects_msg_cls: bool = False,
    is_stream: bool = False,
) -> SendFn | MessageSendFn:
    """
    Builds the send function for socketcan socket `fd`.

    `is_stream` must mirror what was passed to `build_recv_func` for the same socket:
    a datagram socket writes a frame or nothing, whereas a stream one may take only
    part of it and needs the frame completed before the next one is written.
    """
    if expects_msg_cls:
        if is_stream:
            return partial(_socketcan_send_msg_stream, fd.send)
        return partial(_socketcan_send_msg, fd.send)
    if is_stream:
        return partial(_socketcan_send_stream, fd.send)
    return partial(_socketcan_send, fd.send)
