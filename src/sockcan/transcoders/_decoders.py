"""
Implements the decode logic for CAN messages.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, cast, overload

from cantools.database.can.signal import Signal

from ._common import (
    SignalProperties,
    SignalValue,
    build_signal_properties,
    extract_signal_properties,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from cantools.database.can.message import Message


@overload
def decode(
    payload: bytes,
    signals: dict[int, list[SignalProperties]],
    *,
    decode_choices: bool = True,
    container: dict[str, SignalValue] | None,
    mux_selector: SignalProperties,
) -> dict[str, SignalValue]: ...
@overload
def decode(
    payload: bytes,
    signals: list[SignalProperties],
    *,
    decode_choices: bool = True,
    container: dict[str, SignalValue] | None,
    mux_selector: None = None,
) -> dict[str, SignalValue]: ...


def decode(
    payload: bytes,
    signals: list[SignalProperties] | dict[int, list[SignalProperties]],
    *,
    decode_choices: bool = True,
    container: dict[str, SignalValue] | None = None,
    mux_selector: SignalProperties | None = None,
) -> dict[str, SignalValue]:
    payload_i64 = int.from_bytes(payload, "little")
    decoded = container or {}

    if mux_selector is not None:
        signals = cast("dict[int, list[SignalProperties]]", signals)
        name, mask, bit_offset, *_ = mux_selector
        mux_id = (payload_i64 & mask) >> bit_offset
        signals_converted: list[SignalProperties] = signals[mux_id]
    else:
        signals = cast("list[SignalProperties]", signals)
        signals_converted = signals
    for name, mask, bit_offset, scale, offset, _, named_values in signals_converted:
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
    mux_mapping = {}

    if not message.is_multiplexed():
        signals_list = extract_signal_properties(message)
        return partial(
            decode,
            signals=signals_list,
            decode_choices=decode_choices,
            container=container,
        )

    # multiplexed case
    all_mux_ids: set[int] = set()
    selectors: list[Signal] = []
    for sig in message.signals:
        if sig.is_multiplexer:
            selectors.append(sig)
        if sig.multiplexer_ids is not None:
            all_mux_ids.update(sig.multiplexer_ids)

    for mux_id in all_mux_ids:
        signals = extract_signal_properties(message, selector=mux_id)
        mux_mapping[mux_id] = signals
    assert len(selectors) == 1, "invalid handling of mux selectors"
    selector = build_signal_properties(selectors.pop())

    return partial(
        decode,
        signals=mux_mapping,
        decode_choices=decode_choices,
        container=container,
        mux_selector=selector,
    )
