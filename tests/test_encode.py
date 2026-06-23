"""
Test suite for generic encode/decode functions in this package.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cantools.database import load_file
from cantools.database.can import Database as CanDatabase

from sockcan.transcoders import SignalValue, build_encoder

DB_PATH = Path(__file__).parent / "kcd_sample.kcd"


@pytest.fixture
def db() -> CanDatabase:
    _db = load_file(DB_PATH)
    assert isinstance(_db, CanDatabase)
    return _db


@pytest.mark.parametrize(
    ("msg_name", "payload"),
    [
        (
            "CruiseControlStatus",
            {
                "CCEnabled": 1,
                "CCActivated": 1,
                "SpeedKm": 100,
            },
        ),
    ],
)
def test_simple_encode(db: CanDatabase, msg_name: str, payload: dict[str, SignalValue]) -> None:
    message = db.get_message_by_name(msg_name)
    encode = build_encoder(message)
    obtained = encode(payload)
    expected = message.encode(payload)
    assert obtained == expected, f"{obtained!r}!= {expected!r}"
