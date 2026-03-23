"""
Entrypoints for both server and client-side utilities.

@date: 23.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import logging

import click

from sockcan import build_recv_func
from sockcan.daemon import SocketcanDaemon, connect_socketcan_client


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
def client(*, host_ip: str, port: int) -> None:
    """
    Connects the daemon and show all received CAN messages.
    """
    logging.basicConfig(level=logging.INFO)
    sock = connect_socketcan_client(host=host_ip, port=port, channel="vcan0")
    click.echo("> Connected >")

    recv_fn = build_recv_func(sock, use_native_timestamps=False)
    while True:
        next_msg = recv_fn()
        payload = [f"{i:02x}" for i in next_msg.data]
        payload_str = " ".join(payload)
        click.echo(f"{next_msg.arbitration_id:08x}: {payload_str}")


if __name__ == "__main__":
    daemon()
