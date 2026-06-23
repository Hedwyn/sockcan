"""
Implements the encode logic for CAN messages.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from ._common import _SignalProperties, extract_signal_properties

if TYPE_CHECKING:
    from collections.abc import Callable

    from cantools.database.can.message import Message

type SignalValue = int | float | str


def encode(
    payload: dict[str, SignalValue],
    signals: list[_SignalProperties],
    dlc: int = 8,
    *,
    named_values: bool | None = None,
) -> bytes:
    encoded_message = 0
    for name, mask, bit_offset, scale, offset, signal_named_values, _ in signals:
        if name not in payload:
            continue
        value = payload[name]
        is_named = (
            False
            if signal_named_values is not None
            else (named_values if named_values is not None else isinstance(value, str))
        )
        if is_named:
            assert signal_named_values is not None
            assert isinstance(value, str)
            value = signal_named_values[value]
        else:
            assert isinstance(value, int | float)
            value = int((value - offset) / scale)
        # Check if the value fits in the signal's bit length
        # The mask has the same number of bits as the signal length
        signal_length = mask.bit_count()
        max_value = (1 << signal_length) - 1
        if value < 0 or value > max_value:
            raise OverflowError(
                f"Value {value} out of range for signal {name} (0-{max_value})",
            )
        encoded_message |= (value << bit_offset) & mask
    return encoded_message.to_bytes(dlc, "little", signed=False)


def build_encoder(message: Message) -> Callable[[dict[str, SignalValue]], bytes]:
    signals = extract_signal_properties(message)
    dlc = message.length
    return partial(encode, dlc=dlc, signals=signals, named_values=None)
