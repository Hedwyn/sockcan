"""
Common utilities for both encoders and decoders.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from cantools.database.can.message import Message
    from cantools.database.can.signal import Signal

type SignalValue = int | float | str


class SignalProperties(NamedTuple):
    name: str
    mask: int
    bit_offset: int
    scale: float = 1.0
    offset: float = 0.0
    named_values: dict[str, int] | None = None
    reverse_named_values: dict[int, str] | None = None


def build_signal_properties(signal: Signal) -> SignalProperties:
    """
    Generates the properties of a signal given a cantools `Signal` object.
    """
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
    return SignalProperties(
        name=signal.name,
        bit_offset=signal.start,
        mask=mask,
        scale=signal.scale,
        offset=signal.offset or 0.0,
        named_values=named_values,
        reverse_named_values=reversed_named_values,
    )


def extract_signal_properties(
    message: Message,
    *,
    selector: int | None = None,
) -> list[SignalProperties]:
    signals = (
        sig
        for sig in message.signals
        if (selector is None or sig.multiplexer_ids is None or selector in sig.multiplexer_ids)
    )
    return [build_signal_properties(sig) for sig in signals]
