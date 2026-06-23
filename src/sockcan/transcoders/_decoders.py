"""
Implements the decode logic for CAN messages.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from ._common import (
    SignalValue,
    _SignalProperties,
    extract_signal_properties,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from cantools.database.can.message import Message


def decode(
    payload: bytes,
    signals: list[_SignalProperties],
    *,
    decode_choices: bool = True,
    container: dict[str, SignalValue] | None = None,
) -> dict[str, SignalValue]:
    payload_i64 = int.from_bytes(payload, "little")
    decoded = container or {}
    for name, mask, bit_offset, scale, offset, _, named_values in signals:
        raw_value = (payload_i64 & mask) >> bit_offset
        if named_values is not None and decode_choices:
            decoded[name] = named_values[raw_value]
        else:
            value = int(scale * raw_value + offset)
            decoded[name] = (value - offset) / scale
    return decoded


def build_decoder(
    message: Message,
    *,
    decode_choices: bool = True,
    container: dict[str, SignalValue] | None = None,
) -> Callable[[bytes], dict[str, SignalValue]]:
    signals = extract_signal_properties(message)
    return partial(decode, signals=signals, decode_choices=decode_choices, container=container)
