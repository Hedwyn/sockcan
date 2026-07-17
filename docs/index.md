# Sockcan

Sockcan is a fast and lightweight client-side socketcan implementation, as well as an optional server-side one in userspace that allows running socketcan
in systems that do not support Socketcan natively (Windows, vanilla Mac, Linux kernels compiled without CAN support).
It also implements fast decoder and encoders function that perform much faster than their cantools counterpart while providing a similar interface.
[SocketCAN](https://docs.kernel.org/networking/can.html)
is an implementation of CAN protocols within the Linux kernel that provides a unified CAN interface over a standard socket API.<br>

This library provides different sets of functionalities:

* core (`import sockcan`): a fast implementation of socketcan protocol. Should perform about 60x faster than `python-can`.
* daemon (`import sockcan.daemon`): a userspace implementation of the socketcan driver.
Runs a HTTP daemon over the real CAN bus, applications can subscribe to the daemon and get a socketcan-like socket to read and write to the bus.
This enables additional capabilities on top of what the underlying driver offers natively, such as concurrent access to the bus from multiple
processes and virtual CAN interfaces, even on drivers/platforms that don't support them natively (e.g. PCAN on Windows).
* transcoders (`import sockcan.transcoders`): fast CAN decoders/encoders that can be used as a drop-in replacement for the ones from `cantools`.
* interop: (`import sockcan.interop`): convenience (hacky) code to inject sockcan within existing `python-can`-based projects
without having to rewrite your code. Useful to get performance improvements in existing `python-can` applications with little effort,
or to quickly try out `sockcan` to see if it's worth switching to it.

## Getting started

### Installing

`sockcan` is available on [PyPI](https://pypi.org/project/sockcan/). Installing `sockcan` without extra will ship the core only (which has zero dependencies):

```
pip install sockcan
```

For the other features, you need the following extras:

* server-side (userspace socketcan daemon) and python-can interoperability: `pip install sockcan[daemon]`
* transcoders (fast encoders/decoders): `pip install sockcan[transcoders]`

>[!NOTE]
> On some shells (e.g., zsh) you might need to escape the brackets with `\` when installing extras (e.g., `pip install sockcan\[daemon\]`)

### Using the core API directly

If you don't use `python-can` at all, `sockcan` talks directly to the Linux kernel's SocketCAN
sockets — no daemon, no extra process. This assumes you already have a CAN interface (`can0`,
`vcan0`, ...) set up; see [Setting Up CAN](tutorials/setting_up_can.md) if you don't.

```python
from sockcan import SocketcanConfig, connect_to_socketcan, build_send_func, build_recv_func

config = SocketcanConfig(channel="can0")
sock = connect_to_socketcan(config)

send = build_send_func(sock, expects_msg_cls=False)
recv = build_recv_func(sock)

send(0x123, b'\x01\x02\x03\x04', False)
msg = recv()
print(f"ID: {msg.arbitration_id:#x}, Data: {msg.data.hex()}")
```

See the [core usage tutorial](tutorials/core_usage.md) for a full walkthrough.

### Trying out sockcan in existing python-can code

If you already have a working application based on python-can which uses `socketcan` as the interface, you can replace the internal python-can socketcan implementation with the one from this package with this one-liner:

```
from sockcan.interop import hijack_python_can

hijack_python_can()
```

No other changes needed. `send` and `recv` should perform one or two orders of magnitude faster.

> [!WARNING]
> `sockcan` only implements the socketcan interface, and unlike python-can is not a generic API over many CAN drivers.
This will only override the python-can implementation for socketcan, it won't change it for other drivers.

### Standalone commands for the daemon
>
>[!NOTE]
> `daemon` extra should be installed.

You can try out the userspace socketcan implementation (sockcan daemon) with the following commands out-of-the-box:

* `python -m sockcan.daemon --help`
```console
Usage: python -m sockcan.daemon [OPTIONS] COMMAND [ARGS]...

  Main entrypoint for all daemon-related commands

Options:
  --help  Show this message and exit.

Commands:
  busload     Connects the daemon and periodically displays the CAN bus...
  candump     Connects the daemon and dumps received CAN messages,...
  client      Connects the daemon and show all received CAN messages.
  ping        Connects the daemon and show all received CAN messages.
  run-daemon  
```

To start a socketcan daemon, run `python -m sockcan.daemon run-daemon`:
```
python -m sockcan.daemon run-daemon --help
Usage: python -m sockcan.daemon run-daemon [OPTIONS] [CHANNEL]

Options:
  -ip, --host-ip TEXT
  -p, --port INTEGER
  -v, --virtual
  -i, --interface TEXT
  -b, --bitrate INTEGER
  --help                 Show this message and exit.
```

The `candump` and `busload` CLI commands provide utilities similar to the ones from the `can-utils` package on Linux. You can get candumps, or evaluate bus load.
This is particularly useful when working with drivers that cannot natively share the bus between processes (e.g. PCAN Basic library).
```
Usage: python -m sockcan.daemon candump [OPTIONS]

  Connects the daemon and dumps received CAN messages, candump-style.

Options:
  -ip, --host-ip TEXT
  -p, --port INTEGER
  -c, --channel TEXT
  --help               Show this message and exit.
```

## Tutorials

This documentation has other tutorials:

* [Setting up CAN](tutorials/setting_up_can.md): generic Linux/`iproute2` steps to get a `vcan` or real CAN interface up
* [Core usage](tutorials/core_usage.md): writing an application directly with sockcan, without `python-can`
* [Daemon](tutorials/daemon.md): running the userspace socketcan daemon, without `python-can`
* [python-can interop](tutorials/interop.md): using sockcan as a drop-in, faster replacement for python-can's socketcan backend, on Linux and on Windows
* [Transcoders](tutorials/transcoders.md): encoding and decoding CAN messages from a DBC/KCD database

## Features

### What is Sockcan?

* **High Performance:** Optimized for faster message processing and lower latency compared to traditional `python-can` setups.
* **Userspace Implementation:** Enables CAN communication on platforms like Windows, where native SocketCAN is unavailable, by simulating SocketCAN behavior in userspace.
* **`python-can` Compatible Interface:** Minimizes migration effort for existing `python-can` applications.
* **Concurrency:** Supports concurrent access to the CAN bus, allowing multiple parts of an application to send and receive messages simultaneously.
* **Virtual CAN:** Facilitates the creation of virtual CAN interfaces for development, testing, and simulation without physical hardware.
* **Network-accessible daemon**: the userspace SocketCAN server can be exposed as an HTTP daemon over TCP, so consumers on other processes (or machines) can subscribe to a bus remotely.
* **Fast transcoders**: encode/decode CAN signals from a DBC/KCD database much faster than `cantools`, with a compatible interface.

**Use cases:**

* **Well-suited for performance-constrained environments**. Embedded systems running Linux (e.g., Raspberry Pi) might not even be able to process a full bus-load without having CPU throttling with `python-can`.
* **Ease porting Linux implementations to Windows**: can allow running a Linux SocketCAN-based application on Windows by simply running the userspace SocketCAN server provided by this package on top of the real bus. This can enable bus concurrency and virtualization on Windows, which are not available natively with PCAN drivers.

**Limitations:**

* **Timestamps**: Timestamps in SocketCAN rely on socket ancillary data, which does not exist on Windows. Thus, to keep compatibility with SocketCAN, built-in timestamps (which in SocketCAN would be coming from the CAN driver directly) have to be disabled, and timestamping is done at the network level (thus less accurate, and also subject to any lag in the reception chain).
* **Filters**: CAN filtering is currently only partially supported. You should make sure your application logic can properly reject unwanted messages.
* **Blocking policy**: custom timeouts in receive methods are not supported yet. When using selector protocols over the socket, receive is only non-blocking if both sides properly implement the protocol, and might currently end up blocking in case of unexpected payload.

See the [transcoders tutorial](tutorials/transcoders.md) for encoding/decoding CAN signals from a
DBC/KCD database.
