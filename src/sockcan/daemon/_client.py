"""
Client-side code for the socketcan Daemon.
Sends an HTTP request to get a socket.

@date: 23.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import json
import logging
import socket
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from can.typechecking import CanFilter

    from sockcan import SocketcanFd

_logger = logging.getLogger(__name__)

HTTP_DELIMITER = "\r\n"


def ping_daemon(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> bool:
    """
    Tries pinging the daemon and return whether it's currently running.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
    except ConnectionRefusedError:
        _logger.info("No service running at %s:%d", host, port)
        return False

    ping_request = (
        HTTP_DELIMITER.join(
            [
                "GET /ping HTTP/1.1",
                f"Host: {host}:{port}",
                "Connection: close",
            ],
        )
        + 2 * HTTP_DELIMITER
    )
    sock.sendall(ping_request.encode("utf-8"))
    response = sock.recv(4096)
    _logger.info("Ping response %s", response)
    return b"200 OK" in response


def connect_socketcan_client(
    host: str = "127.0.0.1",
    port: int = 8000,
    channel: str = "PCAN_USBBUS1",
    filters: list[CanFilter] | None = None,
) -> SocketcanFd:
    """
    Sends a subscription request to socketcan daemon running on `host`:`port`
    for the CAN channel `channel`.
    If filters is provided, only messages matching those filters will be received.
    Returns the a socketcan socket if successful.
    Raises ValueError if daemon returns a 400.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 2. Connect to the server
    sock.connect((host, port))
    _logger.info("Connected to %s:%d", host, port)

    # 3. Construct the HTTP Upgrade Request
    query_params = f"channel={channel}"
    if filters:
        # Serialize filters as JSON and URL-encode
        filters_json = json.dumps(filters)
        filters_encoded = quote(filters_json)
        query_params += f"&filters={filters_encoded}"

    upgrade_request = (
        HTTP_DELIMITER.join(
            [
                f"GET /subscribe?{query_params} HTTP/1.1{HTTP_DELIMITER}",
                f"Host: {host}:{port}",
                "Connection: Upgrade",
                "Upgrade: socketcan",
            ],
        )
        + HTTP_DELIMITER
    )

    sock.sendall(upgrade_request.encode("utf-8"))

    # 4. Read the Server Response (the 101 Switching Protocols)
    # We read a chunk to clear the HTTP headers from the buffer
    response = sock.recv(4096)
    _logger.debug("HTTP response: %s", response.decode())

    if b"101 Switching Protocols" in response:
        _logger.info("Upgrade successful. Switching to socketcan communications")
        return cast("SocketcanFd", sock)
    raise ValueError(response.decode())
