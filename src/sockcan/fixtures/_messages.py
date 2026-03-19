"""
Implementss CAN messages random generation strategies using hypothesis.

@date: 19.03.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import can
from hypothesis import strategies as st

CAN_11BIT_ID_MASK = 0x3FF
CAN_29BIT_ID_MASK = 0x1FFF_FFFF


@st.composite
def can_messages(draw: st.DrawFn) -> can.Message:
    """
    A composite strategy generating random CAN messages.
    """
    is_extended = draw(st.booleans())
    id_bytes = draw(st.binary(min_size=0, max_size=4))
    data = draw(st.binary(min_size=0, max_size=8))
    dlc = len(data)
    can_id_mask = CAN_29BIT_ID_MASK if is_extended else CAN_11BIT_ID_MASK
    normalized_id = int.from_bytes(id_bytes) & can_id_mask

    return can.Message(
        arbitration_id=normalized_id,
        is_extended_id=is_extended,
        dlc=dlc,
        data=data,
    )
