"""
Generic encode/decode implementation for CAN messages.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, NamedTuple, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from cantools.database.can.message import Message

type SignalValue = int | float | str


class _SignalProperties(NamedTuple):
    name: str
    mask: int
    bit_offset: int
    scale: float = 1.0
    offset: float = 0.0
    named_values: dict[str, int] | None = None
    reverse_named_values: dict[int, str] | None = None


def iter_signal_properties(message: Message) -> Iterator[_SignalProperties]:
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
        yield _SignalProperties(
            name=signal.name,
            bit_offset=signal.start,
            mask=mask,
            scale=signal.scale,
            offset=signal.offset or 0.0,
            named_values=named_values,
            reverse_named_values=reversed_named_values,
        )


def encode(
    payload: dict[str, SignalValue],
    signals: list[_SignalProperties],
    dlc: int = 8,
    *,
    decode_choices: bool = True,
) -> bytes:
    encoded_message = 0
    for name, mask, bit_offset, scale, offset, named_values, _ in signals:
        value = payload[name]
        if named_values is not None and decode_choices:
            value = cast("str", value)
            value = named_values[value]
        else:
            value = cast("int | float", value)
            value = int(scale * value + offset)
        encoded_message |= (value << bit_offset) & mask
    return encoded_message.to_bytes(dlc, "little", signed=True)


def build_encoder(message: Message) -> Callable[[dict[str, SignalValue]], bytes]:
    signals = list(iter_signal_properties(message))
    dlc = message.length
    return partial(encode, dlc=dlc, signals=signals)
