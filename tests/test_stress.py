"""
Stress tests for the daemon: several clients, high load, end-to-end integrity.

Every frame carries its own sequence number and a checksum derived from it, so a
run can tell apart the four ways a relay can go wrong: losing a frame, duplicating
one, reordering two, and corrupting one. The daemon has two independent relay paths
and both are covered here:

* consumer to consumer, short-circuited by the TX thread (`_run_tx`)
* bus to consumer, forwarded by the RX thread (`_run_rx`)

@date: 30.09.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, NamedTuple

import can
import pytest

from sockcan import build_recv_func, build_send_func
from sockcan.daemon import SocketcanDaemon, connect_socketcan_client

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from sockcan import SocketcanFd
    from sockcan.daemon._server import SocketcanServer

# A frame carries a 4-byte sequence number and 4 bytes derived from it, so both
# loss and corruption are detectable from the payload alone.
KNUTH_HASH = 2654435761
FRAME_ID = 0x200
# Socket buffers are squeezed on both ends of a consumer's connection so that a
# burst overflows it within a few thousand frames instead of the ~160k the default
# 2.5 MB send buffer would swallow. They are restored before the consumer resumes
# reading, so the backlog drains at normal speed.
SQUEEZED_BUFFER = 2048
ROOMY_BUFFER = 1 << 20
# Generous: a run only ever waits this long if something is genuinely stuck.
COMPLETION_TIMEOUT = 120.0
# How long without a single new frame counts as "nothing more is coming".
QUIET_PERIOD = 2.0
# How long to give the daemon to register a consumer after its socket is connected.
REGISTRATION_TIMEOUT = 10.0


def payload(seq: int) -> bytes:
    """
    Builds the payload for sequence number `seq`.
    """
    return seq.to_bytes(4, "big") + (seq * KNUTH_HASH & 0xFFFFFFFF).to_bytes(4, "big")


def decode(data: bytes) -> int:
    """
    Returns the sequence number carried by `data`.

    Raises
    ------
    AssertionError
        If the payload does not match what `payload` builds for that sequence
        number, i.e. the frame was corrupted on the way.
    """
    seq = int.from_bytes(data[:4], "big")
    assert data == payload(seq), f"corrupted payload for seq {seq}: {data.hex()}"
    return seq


class Integrity(NamedTuple):
    """
    What a consumer actually received, compared against what was sent.

    `pending` is how many frames the daemon still holds queued for that consumer,
    which is what separates the two ways a count can come up short: frames the relay
    lost, and frames that were simply still on their way.
    """

    expected: int
    received: list[int]
    pending: int = 0

    @property
    def missing(self) -> list[int]:
        return sorted(set(range(self.expected)) - set(self.received))

    @property
    def duplicated(self) -> int:
        return len(self.received) - len(set(self.received))

    @property
    def reordered(self) -> bool:
        return self.received != sorted(self.received)

    def describe(self) -> str:
        missing = self.missing
        return (
            f"expected {self.expected}, got {len(self.received)} "
            f"({len(missing)} missing, {self.duplicated} duplicated, "
            f"reordered={self.reordered}, {self.pending} still queued by the daemon)"
            + (f"; first gaps: {missing[:10]}" if missing else "")
        )

    def assert_intact(self) -> None:
        assert not self.missing, (
            "frames were still queued, the wait gave up too early: "
            if self.pending
            else "frames were dropped: "
        ) + self.describe()
        assert not self.duplicated, f"frames were duplicated: {self.describe()}"
        assert not self.reordered, f"frames were reordered: {self.describe()}"


def daemon_side_socket(server: SocketcanServer, client: SocketcanFd) -> socket.socket:
    """
    Returns the daemon's end of `client`'s connection, matched on the peer address.

    Squeezing that end is what makes a burst overflow quickly: the consumer's own
    receive buffer is only half of what a frame has to fit through.

    The daemon answers the HTTP upgrade before it registers the consumer, so a client
    is connected for a short while before it is actually subscribed - and frames
    published in that window never reach it. Tests have to wait that window out, or
    they measure that race instead of what they meant to measure.
    """
    wanted = client.getsockname()
    deadline = time.monotonic() + REGISTRATION_TIMEOUT
    while time.monotonic() < deadline:
        for consumer in server._consumers:
            with contextlib.suppress(OSError):
                if consumer.fd.getpeername() == wanted:
                    return consumer.fd
        time.sleep(0.01)
    raise AssertionError(f"consumer {wanted} was never registered by the daemon")


class Consumer:
    """
    A client of the daemon, draining its socket in a thread of its own.

    `hold` keeps it from reading, which is how a consumer that cannot keep up is
    simulated: the sockets towards it fill up and the daemon's sends start failing.
    """

    def __init__(self, sock: SocketcanFd, server: SocketcanServer, *, squeezable: bool) -> None:
        self.socket = sock
        peer = daemon_side_socket(server, sock)
        self._peer = peer if squeezable else None
        self._io = server._consumer_io[peer]
        self._send = build_send_func(sock, expects_msg_cls=True, is_stream=True)
        self._recv = build_recv_func(sock, is_stream=True)
        self.received: list[int] = []
        self._reading = threading.Event()
        self._reading.set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def publish(self, seq: int, can_id: int = FRAME_ID) -> None:
        """
        Puts one frame carrying sequence number `seq` on the bus.
        """
        self._send(can.Message(arbitration_id=can_id, data=payload(seq)), None)

    def _resize(self, size: int) -> None:
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, size)
        if self._peer is not None:
            self._peer.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, size)

    def start(self) -> None:
        self.socket.settimeout(COMPLETION_TIMEOUT)
        self._thread.start()

    def _drain(self) -> None:
        while not self._stop.is_set():
            self._reading.wait()
            try:
                self.received.append(decode(bytes(self._recv().data)))
            except OSError:
                return

    @contextmanager
    def hold(self) -> Iterator[None]:
        """
        Stops draining for the duration of the block, with both ends of the
        connection squeezed so the daemon runs out of room quickly.

        Normal buffer sizes are restored before reading resumes, so what follows
        measures whether the frames survived, not how narrow the pipe was.
        """
        self._resize(SQUEEZED_BUFFER)
        self._reading.clear()
        try:
            yield
        finally:
            self._resize(ROOMY_BUFFER)
            self._reading.set()

    @property
    def pending(self) -> int:
        """
        How many frames the daemon is still holding queued for this consumer.
        """
        return len(self._io.outbound)

    def wait_for(self, expected: int) -> None:
        """
        Blocks until `expected` frames have arrived.

        Gives up early only once the consumer has gone quiet *and* the daemon has
        nothing left queued for it: as long as either is true more frames are still
        coming, and stopping there would report them as lost. Under load the daemon
        can go quiet for longer than a fixed grace period, so waiting on the backlog
        rather than on a timer is what keeps this a test of the relay and not of the
        machine it runs on.
        """
        deadline = time.monotonic() + COMPLETION_TIMEOUT
        last_count = -1
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            count = len(self.received)
            if count >= expected:
                return
            if count != last_count:
                last_count = count
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= QUIET_PERIOD and not self.pending:
                return
            time.sleep(0.05)

    def stop(self) -> None:
        self._stop.set()
        self._reading.set()
        self.socket.close()
        self._thread.join(timeout=2.0)

    def integrity(self, expected: int) -> Integrity:
        return Integrity(expected, list(self.received), self.pending)


@contextmanager
def running_daemon(channel: str, interface: str | None = None) -> Generator[SocketcanDaemon]:
    """
    Starts a daemon on an ephemeral port.

    With no `interface`, the bus is virtual and only the TX thread runs, so frames
    are short-circuited between consumers. With one, a real python-can bus is
    wrapped and the RX thread forwards from the bus to the consumers.
    """
    daemon = SocketcanDaemon("127.0.0.1", 0)
    if interface is None:
        daemon.register_virtual_bus(channel)
    else:
        daemon.register_bus(channel=channel, interface=interface)
    daemon.start()
    try:
        yield daemon
    finally:
        daemon.stop()


def connect(daemon: SocketcanDaemon, channel: str, *, squeezable: bool = False) -> Consumer:
    """
    Connects one consumer to `daemon` and starts draining it.
    """
    sock = connect_socketcan_client("127.0.0.1", daemon.port, channel)
    server = daemon._servers[channel]
    # Block until the daemon has actually subscribed it, otherwise the frames
    # published in the meantime are missed and look like a relay bug.
    daemon_side_socket(server, sock)
    consumer = Consumer(sock, server, squeezable=squeezable)
    consumer.start()
    return consumer


def test_consumer_to_consumer_under_load() -> None:
    """
    Several clients hammering the daemon at once must all see every frame, once,
    in order and intact.
    """
    count = 5_000
    with running_daemon("vstress") as daemon:
        sender = connect(daemon, "vstress")
        listeners = [connect(daemon, "vstress") for _ in range(3)]
        try:
            for seq in range(count):
                sender.publish(seq)
            for listener in listeners:
                listener.wait_for(count)
                listener.integrity(count).assert_intact()
        finally:
            for consumer in (sender, *listeners):
                consumer.stop()


def test_bidirectional_under_load() -> None:
    """
    Every client sending while every client receives: each one must see the full
    stream of each of the others, with its own frames never echoed back to it.
    """
    count = 2_000
    client_count = 3
    with running_daemon("vstress") as daemon:
        clients = [connect(daemon, "vstress") for _ in range(client_count)]
        try:

            def blast(client: Consumer) -> None:
                for seq in range(count):
                    client.publish(seq, 0x300)

            senders = [threading.Thread(target=blast, args=(c,)) for c in clients]
            for thread in senders:
                thread.start()
            for thread in senders:
                thread.join()

            expected = count * (client_count - 1)
            for client in clients:
                client.wait_for(expected)
                # every other client's full stream, hence each sequence number
                # exactly `client_count - 1` times
                assert len(client.received) == expected, (
                    f"expected {expected} frames, got {len(client.received)}"
                )
                for seq in range(count):
                    assert client.received.count(seq) == client_count - 1, (
                        f"seq {seq} seen {client.received.count(seq)} times, "
                        f"expected {client_count - 1}"
                    )
        finally:
            for client in clients:
                client.stop()


def test_slow_consumer_does_not_lose_frames_on_tx_path() -> None:
    """
    A consumer that stops reading long enough for the daemon to run out of room must
    not lose frames: they belong in that consumer's backlog, to be delivered once it
    catches up.

    Regression test: `_run_tx`'s consumer-to-consumer short-circuit had no branch for
    a send that could not go through right away. `SO_SNDTIMEO` expiring raises
    `BlockingIOError` (EAGAIN) on Linux, which landed in the catch-all `except
    OSError` and was merely logged, so the frame was dropped and neither the sender
    nor the consumer was told. The healthy consumer staying intact throughout is what
    tells a relay bug apart from the load simply being too high.
    """
    count = 30_000
    with running_daemon("vstress") as daemon:
        sender = connect(daemon, "vstress")
        healthy = connect(daemon, "vstress")
        slow = connect(daemon, "vstress", squeezable=True)
        try:
            with slow.hold():
                for seq in range(count):
                    sender.publish(seq)
                time.sleep(1.0)
            slow.wait_for(count)
            healthy.wait_for(count)

            healthy.integrity(count).assert_intact()
            slow.integrity(count).assert_intact()
        finally:
            for consumer in (sender, healthy, slow):
                consumer.stop()


def test_slow_consumer_does_not_lose_frames_on_rx_path() -> None:
    """
    Same requirement on the bus-to-consumer path, which is the one a real CAN
    interface uses.

    Regression test: `_run_rx` did have a branch meant to queue the frame and hand it
    to the TX thread, but it caught `TimeoutError`, which `SO_SNDTIMEO` never raises
    on Linux. The frame therefore fell through to the catch-all, where a stream
    consumer was not just skipped but evicted outright, silently killing the
    subscription.
    """
    count = 30_000
    channel = "vstress_rx"
    with running_daemon(channel, interface="virtual") as daemon:
        healthy = connect(daemon, channel)
        slow = connect(daemon, channel, squeezable=True)
        bus = can.Bus(interface="virtual", channel=channel)
        try:
            with slow.hold():
                for seq in range(count):
                    bus.send(can.Message(arbitration_id=0x200, data=payload(seq)))
                time.sleep(1.0)
            slow.wait_for(count)
            healthy.wait_for(count)

            server = daemon._servers[channel]
            assert len(server._consumers) == 2, (
                f"a consumer was evicted: {len(server._consumers)} left out of 2"
            )
            healthy.integrity(count).assert_intact()
            slow.integrity(count).assert_intact()
        finally:
            bus.shutdown()
            for consumer in (healthy, slow):
                consumer.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
