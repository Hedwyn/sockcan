# Sockcan

Sockcan is a fast and lightweight Socketcan implementation for Python. [SocketCAN](./https://docs.kernel.org/networking/can.html) is an implementation of CAN protocols within the Linux kernel that provides a unified interface over a standard socket API.<br>
This library has two main goals: providing a performant client-side side implementation (in comparison of python-can), and providing a server side implementation to run SocketCAN-alike communications on Windows systems (which do not have socketCAN natively).

## Getting started

`sockcan` is available on [PyPI](https://pypi.org/project/sockcan/):

```
pip install sockcan
```

## Why Sockcan?

### Cross-Platform Compatibility with Userspace CAN

This library provides an implementation of a SocketCAN server, which can run on systems like Windows that do not have native SocketCAN support. By running in userspace, Sockcan unlocks several critical capabilities:

* **Concurrency on the Bus:** Overcome the limitations of single-threaded CAN bus access found in drivers like PCAN, enabling multiple applications or threads to interact with the CAN bus simultaneously.
* **Virtual CAN:** Create and manage virtual CAN interfaces on interfaces that do not provide them natively, facilitating isolated testing environments, simulation, and development without requiring physical CAN hardware.

### Performance and Efficiency

`python-can` is great at providing an common abstract interface over many CAN drivers, but not so great when it comes to performance. `sockcan` implementation of SocketCAN should run 3-4x faster than python-can's one. Sockcan provides an interfaces that's partially compatible with python-can, and can even replace the python-can's implementation by a faster one with a one-liner.

## Getting Started

Explore our tutorials to quickly get up and running with Sockcan.

[Getting Started with Sockcan](tutorials/getting_started.md)
