"""
Runs benchmarks against python-can.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import click

from sockcan._protocol import (
    SocketcanConfig,
    build_recv_func,
    build_send_func,
    connect_to_socketcan,
)
from sockcan.fixtures import vcan_bus

from ._bench import bench, rx_batch_gen, tx_batch_gen
from ._bench_daemon import DAEMON_BATCH_SIZE, bench_e2e


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.option("-r", "--rounds", default=200, type=int)
@click.option("-b", "--batch-size", default=100, type=int)
def kernel(*, rounds: int, batch_size: int) -> None:
    """Benchmarks python-can vs sockcan with direct kernel communications."""
    with vcan_bus() as tx_bus, vcan_bus() as rx_bus:
        _, pycan_rx = bench(
            tx_batch_gen(tx_bus.send, batch_size=batch_size),
            rx_batch_gen(rx_bus.recv, batch_size=batch_size),
            rounds,
        )

        sockcan_sock = connect_to_socketcan(SocketcanConfig(channel="vcan0"))
        _, sockcan_rx = bench(
            tx_batch_gen(tx_bus.send, batch_size=batch_size),
            rx_batch_gen(build_recv_func(sockcan_sock), batch_size=batch_size),
            rounds,
        )

    click.echo(f"RX: sockcan {pycan_rx / sockcan_rx:.2f}x faster than python-can")


@cli.command()
@click.option("-r", "--rounds", default=100, type=int)
@click.option("-b", "--batch-size", default=DAEMON_BATCH_SIZE, type=int)
@click.option("-p", "--port", default=18765, type=int)
def daemon(*, rounds: int, batch_size: int, port: int) -> None:
    """
    Benchmarks sockcan daemon overhead vs direct kernel python-can.
    Daemon server, TX client, and RX client all run in the same process.
    Reports end-to-end throughput (TX → relay → RX).
    Linux only: requires vcan0.
    """
    from sockcan.daemon import SocketcanDaemon, connect_socketcan_client  # noqa: PLC0415

    # --- kernel baselines ---
    with vcan_bus() as tx_bus, vcan_bus() as rx_bus:
        _, pycan_k = bench(
            tx_batch_gen(tx_bus.send, batch_size=batch_size),
            rx_batch_gen(rx_bus.recv, batch_size=batch_size),
            rounds,
        )
    # rx_bus closed before sockcan bench: its open socket would accumulate unread
    # frames and fill its buffer, which slows down vcan delivery to all sockets.
    with vcan_bus() as tx_bus:
        sockcan_sock = connect_to_socketcan(SocketcanConfig(channel="vcan0"))
        _, sockcan_k = bench(
            tx_batch_gen(tx_bus.send, batch_size=batch_size),
            rx_batch_gen(build_recv_func(sockcan_sock), batch_size=batch_size),
            rounds,
        )

    # --- daemon: end-to-end TX → relay → RX (all in-process) ---
    daemon_inst = SocketcanDaemon(port=port)
    daemon_inst.register_virtual_bus("vcan0")
    with daemon_inst:
        tx = connect_socketcan_client(channel="vcan0", port=port)
        rx = connect_socketcan_client(channel="vcan0", port=port)
        daemon_t = bench_e2e(
            build_send_func(tx, expects_msg_cls=True),
            build_recv_func(rx, use_native_timestamps=False, is_stream=True),
            batch_size,
            rounds,
        )
        tx.close()
        rx.close()

    total_frames = rounds * batch_size
    click.echo(f"[kernel]  python-can:  {pycan_k:.3f}s  ({total_frames / pycan_k:.0f} frames/s)")
    click.echo(
        f"[kernel]  sockcan:     {sockcan_k:.3f}s  ({total_frames / sockcan_k:.0f} frames/s)"
        f"  ({pycan_k / sockcan_k:.2f}x vs python-can)"
    )
    click.echo(
        f"[daemon]  sockcan:     {daemon_t:.3f}s  ({total_frames / daemon_t:.0f} frames/s)"
        f"  ({daemon_t / sockcan_k:.2f}x overhead vs kernel)"
    )


if __name__ == "__main__":
    cli()
