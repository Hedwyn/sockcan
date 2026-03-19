"""
Runs benchmarks against python-can.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import pstats

import click

from sockcan._protocol import SocketcanConfig, build_recv_func, connect_to_socketcan
from sockcan.fixtures import vcan_bus

from ._bench import bench


@click.command()
@click.option("-r", "--rounds", default=200, type=int)
@click.option("-b", "--batch-size", default=100, type=int)
@click.option("-v", "--verbose", is_flag=True)
def cli(*, rounds: int, batch_size: int, verbose: bool) -> None:
    """
    Runs the benchmarks interactively
    """
    with vcan_bus() as tx_bus, vcan_bus() as rx_bus:
        python_can_profile = bench(rx_bus.recv, tx_bus, batch_size=batch_size, total_rounds=rounds)

        if verbose:
            python_can_profile.print_stats()

        sockcan_sock = connect_to_socketcan(SocketcanConfig(channel="vcan0"))
        recv_fn = build_recv_func(sockcan_sock)
        sockcan_profile = bench(recv_fn, tx_bus, batch_size=batch_size, total_rounds=rounds)
        if verbose:
            sockcan_profile.print_stats()

        python_can_stats = pstats.Stats(python_can_profile)
        sockcan_stats = pstats.Stats(sockcan_profile)
        ratio = python_can_stats.total_tt / sockcan_stats.total_tt  # type: ignore[attr-defined]
        click.echo(f"Performed {ratio:.02f} x faster than python-can")


if __name__ == "__main__":
    cli()
