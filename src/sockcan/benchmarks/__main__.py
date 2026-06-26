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
from ._bench_daemon import DAEMON_BATCH_SIZE, build_pycan_recv_stream


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
    Benchmarks all 4 scenarios: direct kernel vs userspace daemon, python-can vs sockcan.
    Linux only: requires vcan0.
    """
    from sockcan.daemon import SocketcanDaemon, connect_socketcan_client  # noqa: PLC0415

    # --- scenarios 1 & 2: direct kernel ---
    with vcan_bus() as tx_bus, vcan_bus() as rx_bus:
        _, pycan_k = bench(
            tx_batch_gen(tx_bus.send, batch_size=batch_size),
            rx_batch_gen(rx_bus.recv, batch_size=batch_size),
            rounds,
        )

        sockcan_sock = connect_to_socketcan(SocketcanConfig(channel="vcan0"))
        _, sockcan_k = bench(
            tx_batch_gen(tx_bus.send, batch_size=batch_size),
            rx_batch_gen(build_recv_func(sockcan_sock), batch_size=batch_size),
            rounds,
        )

    # --- scenarios 3 & 4: userspace daemon (virtual bus, no hardware needed) ---
    daemon = SocketcanDaemon(port=port)
    daemon.register_virtual_bus("vcan0")
    with daemon:
        # scenario 3: sockcan recv via daemon
        tx1 = connect_socketcan_client(channel="vcan0", port=port)
        rx1 = connect_socketcan_client(channel="vcan0", port=port)
        _, sockcan_d = bench(
            tx_batch_gen(build_send_func(tx1, expects_msg_cls=True), batch_size=batch_size),
            rx_batch_gen(
                build_recv_func(rx1, use_native_timestamps=False, is_stream=True),
                batch_size=batch_size,
            ),
            rounds,
        )
        tx1.close()
        rx1.close()

        # scenario 4: python-can recv via daemon
        tx2 = connect_socketcan_client(channel="vcan0", port=port)
        rx2 = connect_socketcan_client(channel="vcan0", port=port)
        _, pycan_d = bench(
            tx_batch_gen(build_send_func(tx2, expects_msg_cls=True), batch_size=batch_size),
            rx_batch_gen(build_pycan_recv_stream(rx2), batch_size=batch_size),
            rounds,
        )
        tx2.close()
        rx2.close()

    click.echo(f"[kernel]  python-can:            {pycan_k:.3f}s")
    click.echo(
        f"[kernel]  sockcan:               {sockcan_k:.3f}s  ({pycan_k / sockcan_k:.2f}x faster than python-can)"
    )
    click.echo(
        f"[daemon]  sockcan:               {sockcan_d:.3f}s  ({sockcan_d / sockcan_k:.2f}x kernel overhead)"
    )
    click.echo(
        f"[daemon]  python-can:            {pycan_d:.3f}s  ({pycan_d / pycan_k:.2f}x kernel overhead)"
    )
    click.echo(f"[daemon]  sockcan vs python-can: {pycan_d / sockcan_d:.2f}x faster")


if __name__ == "__main__":
    cli()
