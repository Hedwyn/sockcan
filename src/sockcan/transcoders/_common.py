"""
Common utilities for both encoders and decoders.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from cantools.database.can.message import Message

type SignalValue = int | float | str


class SignalProperties(NamedTuple):
    name: str
    mask: int
    bit_offset: int
    scale: float = 1.0
    offset: float = 0.0
    named_values: dict[str, int] | None = None
    reverse_named_values: dict[int, str] | None = None


def extract_signal_properties(message: Message) -> list[SignalProperties]:
    signal_properties: list[SignalProperties] = []
    for signal in message.signals:
        named_values: dict[str, int] | None = None
        mask = (1 << signal.length) - 1
        mask <<= signal.start
        named_values = (
            {str(val): key for key, val in signal.choices.items()}
            if signal.choices is not None
            else None
        )
        reversed_named_values = (
            {val: key for key, val in named_values.items()} if named_values is not None else None
        )
        properties = SignalProperties(
            name=signal.name,
            bit_offset=signal.start,
            mask=mask,
            scale=signal.scale,
            offset=signal.offset or 0.0,
            named_values=named_values,
            reverse_named_values=reversed_named_values,
        )
        signal_properties.append(properties)
    return signal_properties
