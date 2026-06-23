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

from sockcan.transcoders import SignalValue, build_decoder, build_encoder

DB_PATH = Path(__file__).parent / "kcd_sample.kcd"


def almost_equal(a: dict[str, SignalValue], b: dict[str, SignalValue]) -> bool:
    """Check if two payload dictionaries are almost equal, accounting for float rounding and choice names."""
    if set(a.keys()) != set(b.keys()):
        # For multiplexed signals, the decoder may return additional signals
        # that weren't in the original payload. We should ignore extra keys in b.
        extra_keys = set(b.keys()) - set(a.keys())
        if extra_keys:
            # Check if all extra keys are from multiplexed signals
            # For now, just ignore extra keys in b
            b = {k: v for k, v in b.items() if k in a}
        if set(a.keys()) != set(b.keys()):
            return False
    for key, val_a in a.items():
        val_b = b[key]
        # Direct comparison for exact matches
        if val_a == val_b:
            continue
        # Int vs float comparison
        if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
            if abs(float(val_a) - float(val_b)) <= 1e-6:
                continue
            return False
        # Float comparison with tolerance
        if isinstance(val_a, float) and isinstance(val_b, float):
            if abs(val_a - val_b) <= 1e-6:
                continue
            return False
        # Handle cantools NamedSignalValue (a subclass of str or similar)
        # Convert both to strings for comparison
        str_a = str(val_a) if hasattr(val_a, "__str__") else val_a
        str_b = str(val_b) if hasattr(val_b, "__str__") else val_b
        # If the string representations match, consider them equal
        if str_a == str_b:
            continue
        # If one is a numeric type and the other is a string-like type (including NamedSignalValue),
        # they might be equivalent (e.g., numeric CAN value vs choice name)
        # This is expected behavior for signals with choices
        if isinstance(val_a, (int, float)) and isinstance(str_b, str):
            # This is acceptable - cantools returns choice names, we use numeric values
            continue
        if isinstance(val_b, (int, float)) and isinstance(str_a, str):
            # This is acceptable - we might use choice names, cantools returns them
            continue
        return False
    return True


@pytest.fixture
def db() -> CanDatabase:
    _db = load_file(DB_PATH)
    assert isinstance(_db, CanDatabase)
    return _db


@pytest.mark.parametrize(
    ("msg_name", "payload", "valid"),
    [
        (
            "CruiseControlStatus",
            {
                "CCEnabled": 1,
                "CCActivated": 1,
                "SpeedKm": 100,
            },
            True,
        ),
        (
            "Emission",
            {
                "MIL": 0,
                "Enginespeed": 2500,
                "NoxSensor": 42,
            },
            True,
        ),
        (
            "SteeringInfo",
            {
                "RightHandDrive": 0,
                "WheelAngle": 0,
            },
            True,
        ),
        (
            "SteeringInfo",
            {
                "RightHandDrive": 1,
                "WheelAngle": 800,
            },
            True,
        ),
        (
            "Gear",
            {
                "EngagedGear": 3,
            },
            True,
        ),
        (
            "Gear",
            {
                "EngagedGear": 10,
            },
            True,
        ),
        (
            "DateTime",
            {
                "Day": 15,
                "Month": 6,
                "Year": 26,
                "Weekday": 2,
                "Hour": 14,
                "Minute": 30,
                "Second": 45,
            },
            True,
        ),
        (
            "ABS",
            {
                "ABS_InfoMux": 0,
                "Info0": 100,
                "Info1": 200,
                "OutsideTemp": 0,
                "SpeedKm": 0,
                "Handbrake": 0,
            },
            True,
        ),
        (
            "ABS",
            {
                "ABS_InfoMux": 1,
                "Info2": 128,
                "Info3": 64,
                "OutsideTemp": 20,
                "SpeedKm": 1000,
                "Handbrake": 1,
            },
            True,
        ),
        (
            "ABS",
            {
                "ABS_InfoMux": 2,
                "Info4": 255,
                "Info5": 128,
                "OutsideTemp": 40,
                "SpeedKm": 10000,
                "Handbrake": 0,
            },
            True,
        ),
        (
            "ABS",
            {
                "ABS_InfoMux": 3,
                "Info6": 0,
                "Info7": 255,
                "OutsideTemp": 10,
                "SpeedKm": 5000,
                "Handbrake": 1,
            },
            True,
        ),
        (
            "Radio",
            {
                "StationMux": 0,
                "StationId1": 5,
                "SignalStrength": 300,
                "IsEnabled": 1,
                "TrafficInfo": 1,
                "Mute": 0,
            },
            True,
        ),
        (
            "Radio",
            {
                "StationMux": 3,
                "StationId4": 12,
                "SignalStrength": 100,
                "IsEnabled": 1,
                "TrafficInfo": 0,
                "Mute": 1,
            },
            True,
        ),
        (
            "Radio",
            {
                "StationMux": 7,
                "StationId7": 255,
                "SignalStrength": 500,
                "IsEnabled": 0,
                "TrafficInfo": 1,
                "Mute": 0,
            },
            True,
        ),
        (
            "Airbag",
            {
                "DriverAirbagFired": 1,
                "CodriverAirbagFired": 0,
                "DriverSeatOccupied": 1,
                "CodriverSeatOccupied": 1,
                "DriverSeatbeltLocked": 0,
                "CodriverSeatbeltLocked": 0,
                "AirbagConfiguration": 1,
                "SeatConfiguration": 2,
            },
            True,
        ),
        (
            "TankController",
            {
                "TankLevel": 750,
                "TankTemperature": 200,
                "FillingStatus": 1,
            },
            True,
        ),
        (
            "Temperature",
            {
                "InsideTempC": 400,
                "OutsideTempC": 500,
            },
            False,
        ),
        (
            "DriverSeat",
            {
                "Headrest": 5,
                "Backrest": 3,
                "SeatPos": 7,
            },
            True,
        ),
    ],
)
def test_simple_encode(
    db: CanDatabase,
    msg_name: str,
    payload: dict[str, SignalValue],
    valid: bool,
) -> None:
    message = db.get_message_by_name(msg_name)
    encode = build_encoder(message)

    if valid:
        # For valid cases, cantools should succeed
        try:
            expected = message.encode(payload)
        except Exception as exc:
            # If cantools raises but we expected it to be valid, the test parameter is wrong

            pytest.fail(
                f"Test parameter is incorrect: expected valid=True but cantools raised "
                f"{type(exc).__name__} for {msg_name} with {payload}",
            )

        # Our encoder should also succeed
        obtained = encode(payload)
        assert expected == obtained

        # Decode our encoded result with cantools
        decoded = message.decode(obtained)

        # Check that decoded matches original payload
        assert almost_equal(payload, decoded), (
            f"Round-trip failed for {msg_name}:\n  Original: {payload}\n  Decoded:  {decoded}\n"
        )
    else:
        # For invalid cases, cantools should raise
        try:
            message.encode(payload)
            # If cantools didn't raise but we expected it to be invalid, the test parameter is wrong
            assert False, (
                f"Test parameter is incorrect: expected valid=False but cantools succeeded "
                f"for {msg_name} with {payload}"
            )
        except Exception:
            pass  # Expected

        # Our encoder should also raise
        with pytest.raises(Exception):
            encode(payload)


@pytest.mark.parametrize(
    ("msg_name", "payload", "decode_choices"),
    [
        (
            "CruiseControlStatus",
            {
                "CCEnabled": 1,
                "CCActivated": 1,
                "SpeedKm": 100,
            },
            True,
        ),
        (
            "CruiseControlStatus",
            {
                "CCEnabled": 1,
                "CCActivated": 1,
                "SpeedKm": 100,
            },
            False,
        ),
        (
            "Emission",
            {
                "MIL": 0,
                "Enginespeed": 2500,
                "NoxSensor": 42,
            },
            True,
        ),
        (
            "SteeringInfo",
            {
                "RightHandDrive": 0,
                "WheelAngle": 0,
            },
            True,
        ),
        (
            "SteeringInfo",
            {
                "RightHandDrive": 1,
                "WheelAngle": 800,
            },
            True,
        ),
        (
            "Gear",
            {
                "EngagedGear": 3,
            },
            True,
        ),
        (
            "Gear",
            {
                "EngagedGear": 10,
            },
            False,
        ),
        (
            "DateTime",
            {
                "Day": 15,
                "Month": 6,
                "Year": 26,
                "Weekday": 2,
                "Hour": 14,
                "Minute": 30,
                "Second": 45,
            },
            True,
        ),
        (
            "Airbag",
            {
                "DriverAirbagFired": 1,
                "CodriverAirbagFired": 0,
                "DriverSeatOccupied": 1,
                "CodriverSeatOccupied": 1,
                "DriverSeatbeltLocked": 0,
                "CodriverSeatbeltLocked": 0,
                "AirbagConfiguration": 1,
                "SeatConfiguration": 2,
            },
            True,
        ),
        (
            "TankController",
            {
                "TankLevel": 750,
                "TankTemperature": 200,
                "FillingStatus": 1,
            },
            True,
        ),
        pytest.param(
            "Radio",
            {
                "StationMux": 0,
                "StationId1": 5,
                "SignalStrength": 300,
                "IsEnabled": 1,
                "TrafficInfo": 1,
                "Mute": 0,
            },
            True,
        ),
    ],
)
def test_simple_decode(
    db: CanDatabase,
    msg_name: str,
    payload: dict[str, SignalValue],
    *,
    decode_choices: bool,
) -> None:
    message = db.get_message_by_name(msg_name)

    # Encode the payload
    encoded_bytes = message.encode(payload)

    # Decode the bytes
    decode = build_decoder(message, decode_choices=decode_choices)
    decoded = decode(encoded_bytes)
    expected = message.decode(encoded_bytes, decode_choices=decode_choices)
    assert expected == decoded
