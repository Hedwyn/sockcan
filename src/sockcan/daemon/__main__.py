"""
Entrypoints for both server and client-side utilities.

@date: 23.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import logging
from time import monotonic

import click

from sockcan import build_recv_func
from sockcan.daemon import SocketcanDaemon, connect_socketcan_client
from sockcan.daemon._client import ping_daemon


@click.group()
def daemon() -> None:
    """
    Main entrypoint for all daemon-related commands
    """


@daemon.command()
@click.argument("channel", type=str, default="PCAN_USBBUS1")
@click.option("-ip", "--host-ip", default="localhost", type=str)
@click.option("-p", "--port", default=8000, type=int)
@click.option("-v", "--virtual", is_flag=True)
@click.option("-i", "--interface", type=str, default="pcan")
@click.option("-b", "--bitrate", type=int, default=500_000)
def run_daemon(
    *,
    channel: str,
    host_ip: str,
    port: int,
    interface: str,
    bitrate: int,
    virtual: bool,
) -> None:
    logging.basicConfig(level=logging.INFO)
    daemon = SocketcanDaemon(host_ip, port)
    if virtual:
        daemon.register_virtual_bus(channel=channel)
    else:
        daemon.register_bus(channel=channel, interface=interface, bitrate=bitrate)

    daemon.start()
    input("Press enter to stop...")
    daemon.stop()


@daemon.command()
@click.option("-ip", "--host-ip", default="localhost", type=str)
@click.option("-p", "--port", default=8000, type=int)
@click.option("-c", "--channel", default="vcan0", type=str)
def client(*, host_ip: str, port: int, channel: str) -> None:
    """
    Connects the daemon and show all received CAN messages.
    """
    logging.basicConfig(level=logging.INFO)
    sock = connect_socketcan_client(host=host_ip, port=port, channel=channel)
    click.echo("> Connected >")

    recv_fn = build_recv_func(sock, use_native_timestamps=False)
    while True:
        next_msg = recv_fn()
        payload = [f"{i:02x}" for i in next_msg.data]
        payload_str = " ".join(payload)
        click.echo(f"{next_msg.arbitration_id:08x}: {payload_str}")


@daemon.command()
@click.option("-ip", "--host-ip", default="localhost", type=str)
@click.option("-p", "--port", default=8000, type=int)
@click.option("-c", "--channel", default="vcan0", type=str)
def candump(*, host_ip: str, port: int, channel: str) -> None:
    """
    Connects the daemon and dumps received CAN messages, candump-style.
    """
    logging.basicConfig(level=logging.INFO)
    sock = connect_socketcan_client(host=host_ip, port=port, channel=channel)
    click.echo("> Connected >")

    recv_fn = build_recv_func(sock, use_native_timestamps=False)
    while True:
        next_msg = recv_fn()
        id_width = 8 if next_msg.is_extended_id else 3
        payload = " ".join(f"{byte:02X}" for byte in next_msg.data)
        click.echo(
            f"({next_msg.timestamp:.6f})  {channel}  "
            f"{next_msg.arbitration_id:0{id_width}X}   [{len(next_msg.data)}]  {payload}",
        )


def _frame_bit_count(*, is_extended_id: bool, data_len: int) -> int:
    """
    Approximates the on-wire bit count of a classic CAN frame (excludes bit stuffing).
    """
    overhead = 67 if is_extended_id else 47
    return overhead + 8 * data_len


@daemon.command()
@click.option("-ip", "--host-ip", default="localhost", type=str)
@click.option("-p", "--port", default=8000, type=int)
@click.option("-c", "--channel", default="vcan0", type=str)
@click.option("-b", "--bitrate", type=int, default=500_000)
@click.option("-w", "--window", type=float, default=1.0, help="Averaging window, in seconds")
def busload(*, host_ip: str, port: int, channel: str, bitrate: int, window: float) -> None:
    """
    Connects the daemon and periodically displays the CAN bus load.
    """
    logging.basicConfig(level=logging.INFO)
    sock = connect_socketcan_client(host=host_ip, port=port, channel=channel)
    click.echo("> Connected >")

    recv_fn = build_recv_func(sock, use_native_timestamps=False)
    window_bits = 0
    window_msg_count = 0
    window_start = monotonic()
    while True:
        next_msg = recv_fn()
        window_bits += _frame_bit_count(
            is_extended_id=next_msg.is_extended_id,
            data_len=len(next_msg.data),
        )
        window_msg_count += 1

        elapsed = monotonic() - window_start
        if elapsed >= window:
            load_pct = 100.0 * window_bits / (bitrate * elapsed)
            msg_per_sec = window_msg_count / elapsed
            click.echo(f"Bus load: {load_pct:5.1f}%  |  {msg_per_sec:7.1f} msg/s")
            window_bits = 0
            window_msg_count = 0
            window_start = monotonic()


@daemon.command()
@click.option("-ip", "--host-ip", default="localhost", type=str)
@click.option("-p", "--port", default=8000, type=int)
def ping(*, host_ip: str, port: int) -> None:
    """
    Connects the daemon and show all received CAN messages.
    """
    logging.basicConfig(level=logging.INFO)
    if ping_daemon(host_ip, port):
        click.echo(f"Daemon is running on {host_ip}:{port}")
    else:
        click.echo("Daemon is not running")


if __name__ == "__main__":
    daemon()
