"""Decoder for Shearwater's Petrel Native Format (PNF) dive logs.

PNF is the on-device log format used by Shearwater's Petrel-family dive
computers (Petrel, Perdix, Teric, Nerd, Peregrine, and successors). Shearwater
Cloud's desktop app stores each dive as a ``sw-pnf`` blob in the ``log_data``
table of its exported SQLite database (``dive_data.db``): a 4-byte little-endian
uncompressed-length prefix followed by a gzip stream. The decompressed payload
is a sequence of fixed 32-byte records; byte 0 of each record is its type.

This module decodes that record stream. See ``SPEC.md`` for the full format
specification and the provenance of every field.

The field offsets follow libdivecomputer's ``shearwater_predator_parser.c`` for
the documented channels, extended with channels identified by observation and
reconciled against Shearwater Cloud's own parser. Record types that are not
understood (Shearwater's own desktop parser also leaves several of them
undecoded) are returned verbatim in ``Dive.unknown_records`` so nothing is
dropped.
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass, field

RECORD_SIZE = 32

# Record types (byte 0 of each 32-byte record).
DIVE_SAMPLE = 0x01
FREEDIVE_SAMPLE = 0x02
AVELO_SAMPLE = 0x03
OPENING_0, OPENING_9 = 0x10, 0x19
CLOSING_0, CLOSING_9 = 0x20, 0x29
INFO_EVENT = 0x30
DIVE_SAMPLE_EXT = 0xE1
FINAL = 0xFF

# Dive-sample byte 12 is a packed status/mode byte. Bit meanings:
# 0 gas-switch-needed, 1 external ppO2, 2 setpoint type (high/low),
# 3 circuit-switch type, 4 circuit mode (set = open circuit on this family),
# 5 CCR mode, 6-7 solenoid-fired count.
FLAG_GAS_SWITCH = 0x01
FLAG_PPO2_EXTERNAL = 0x02
FLAG_SETPOINT_HIGH = 0x04
FLAG_CIRCUIT_SWITCH = 0x08
FLAG_CIRCUIT_MODE = 0x10
FLAG_CCR_MODE = 0x20
SOLENOID_COUNT_SHIFT = 6

# Bytes 30-31 hold surface air consumption; 0xFFFF means no air integration.
SAC_SENTINEL = 0xFFFF

DIVE_MODES = {
    0: "cc",
    1: "oc_tec",
    2: "gauge",
    3: "ppo2",
    4: "sc",
    5: "cc2",
    6: "oc_rec",
    7: "freedive",
    12: "avelo",
}

# Decompression model, from opening-record-2 byte 18. Under DCIEM the deco
# outputs at sample bytes 24-25 are a safe-ascent depth rather than a
# ceiling / GF99 pair.
DECO_MODELS = {0: "gf", 1: "vpmb", 2: "vpmb_gfs", 3: "dciem"}

# Final-record byte 13 computer-model ids.
COMPUTER_MODELS = {
    2: "Predator",
    3: "Petrel",
    4: "Nerd",
    5: "Perdix",
    6: "Perdix AI",
    7: "Nerd 2",
    8: "Teric",
    9: "Peregrine",
    10: "Petrel 3",
    11: "Perdix 2",
    12: "Tern",
}


def _u16be(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def _u24be(data: bytes, off: int) -> int:
    return (data[off] << 16) | (data[off + 1] << 8) | data[off + 2]


def _u32be(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def _bcd2dec(value: int) -> int:
    return ((value >> 4) & 0x0F) * 10 + (value & 0x0F)


@dataclass
class Sample:
    """One dive sample (a 0x01 record), at ``t_s`` seconds into the dive.

    Fields are ``None`` when the source computer did not record that channel
    (for example ``gf99`` is ``None`` while tissues are still on-gassing, and
    the air-integration and CCR fields are ``None`` on open-circuit dives).
    """

    t_s: float
    depth_m: float | None = None
    temp_c: float | None = None
    stop_depth_m: float | None = None
    stop_or_ndl_min: float | None = None  # stop time when in deco, NDL otherwise
    tts_min: float | None = None
    in_deco: int | None = None
    avg_ppo2: float | None = None
    o2_pct: float | None = None
    he_pct: float | None = None
    setpoint: float | None = None
    cns_pct: float | None = None
    gf99: float | None = None
    ceiling_m: float | None = None
    safe_ascent_depth_m: float | None = None  # DCIEM only (bytes 24-25)
    battery_pct: int | None = None
    battery_v: float | None = None
    sac: float | None = None
    sensor1_raw: int | None = None
    sensor2_raw: int | None = None
    sensor3_raw: int | None = None
    sensor1_ppo2: float | None = None
    sensor2_ppo2: float | None = None
    sensor3_ppo2: float | None = None
    status_flags: int | None = None  # raw byte 12
    gas_switch_needed: bool | None = None
    ppo2_external: bool | None = None
    setpoint_high: bool | None = None
    circuit_switch: bool | None = None
    circuit_mode: int | None = None
    ccr_mode: int | None = None
    solenoid_fired_count: int | None = None
    tank0_psi: float | None = None
    tank1_psi: float | None = None
    gas_time_min: float | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Gas:
    index: int
    o2_pct: int
    he_pct: int
    diluent: bool
    enabled: bool


@dataclass
class Event:
    t_s: float
    event: int
    timestamp: int
    w1: int
    w2: int
    raw_hex: str


@dataclass
class Dive:
    """A decoded dive: a header dict, the samples, gases, events, and any
    record types this decoder did not interpret (kept verbatim)."""

    header: dict
    samples: list[Sample]
    gases: list[Gas]
    events: list[Event]
    unknown_records: list[tuple[str, int, bytes]]  # (type_key, seq, raw)
    sample_interval_ms: int


def decompress(blob: bytes) -> bytes:
    """Return the raw 32-byte record stream from a ``sw-pnf`` blob.

    A blob is a 4-byte little-endian uncompressed-length prefix followed by a
    gzip stream. Raises ``ValueError`` if the decompressed length does not match
    the prefix or is not a multiple of the 32-byte record size.
    """
    expected = struct.unpack_from("<I", blob, 0)[0]
    data = gzip.decompress(blob[4:])
    if len(data) != expected:
        raise ValueError(f"PNF length mismatch: prefix {expected}, got {len(data)}")
    if len(data) % RECORD_SIZE:
        raise ValueError(f"PNF payload not a multiple of {RECORD_SIZE} bytes")
    return data


def decode(blob: bytes) -> Dive:
    """Decode one ``sw-pnf`` blob into a :class:`Dive`."""
    data = decompress(blob)

    opening: dict[int, int] = {}
    closing: dict[int, int] = {}
    final_off: int | None = None
    raw_records: list[tuple[int, int]] = []  # (type, offset), stream order

    for off in range(0, len(data), RECORD_SIZE):
        rec = data[off : off + RECORD_SIZE]
        if rec == b"\x00" * RECORD_SIZE:
            continue
        rtype = rec[0]
        raw_records.append((rtype, off))
        if OPENING_0 <= rtype <= OPENING_9:
            opening[rtype - OPENING_0] = off
        elif CLOSING_0 <= rtype <= CLOSING_9:
            closing[rtype - CLOSING_0] = off
        elif rtype == FINAL:
            final_off = off

    for i in range(5):
        if i not in opening or i not in closing:
            raise ValueError(f"missing opening/closing record {i}")

    header = _parse_header(data, opening, closing, final_off)
    interval_ms = header["sample_interval_ms"]
    calibrated = header["sensor_calibrated_mask"]
    calibration = header["sensor_calibration"]
    imperial = header["imperial"]
    is_dciem = header["deco_model"] == "dciem"

    gases = [
        Gas(
            index=i,
            o2_pct=header["gas_o2"][i],
            he_pct=header["gas_he"][i],
            diluent=i >= 5,
            enabled=bool(header["gas_enabled_mask"] & (1 << i)),
        )
        for i in range(10)
    ]

    samples: list[Sample] = []
    events: list[Event] = []
    unknown: list[tuple[str, int, bytes]] = []
    seq = 0
    t = 0.0

    for rtype, off in raw_records:
        rec = data[off : off + RECORD_SIZE]
        if rtype in (DIVE_SAMPLE, AVELO_SAMPLE):
            samples.append(
                _parse_sample(rec, t, imperial, calibrated, calibration, rtype, is_dciem)
            )
            t += interval_ms / 1000.0
        elif rtype == FREEDIVE_SAMPLE:
            for i in range(4):
                sub = rec[i * 8 : (i + 1) * 8]
                if sub == b"\x00" * 8:
                    break
                pressure_mbar = _u16be(sub, 1)
                depth = (
                    (pressure_mbar - header["atmospheric_mbar"])
                    * 100.0
                    / (header["water_density"] * 9.80665)
                )
                temp = struct.unpack_from(">h", sub, 3)[0] / 10.0
                samples.append(Sample(t_s=t, depth_m=round(depth, 2), temp_c=temp))
                t += interval_ms / 1000.0
        elif rtype == DIVE_SAMPLE_EXT:
            s = samples[-1] if samples else None
            for i, key in enumerate(("tank2_psi", "tank3_psi")):
                v = _u16be(rec, 1 + i * 2)
                if v < 0xFFF0 and (v & 0x0FFF) and s is not None:
                    s.extra[key] = (v & 0x0FFF) * 2
            for i, key in enumerate(("tank_dil_psi", "tank_o2_psi")):
                v = _u16be(rec, 1 + 4 + i * 2)
                if v and s is not None:
                    s.extra[key] = v * 2
        elif rtype == INFO_EVENT:
            events.append(
                Event(
                    t_s=t,
                    event=rec[1],
                    timestamp=_u32be(rec, 4),
                    w1=_u32be(rec, 8),
                    w2=_u32be(rec, 12),
                    raw_hex=rec.hex(),
                )
            )
        elif OPENING_0 <= rtype <= OPENING_9 or CLOSING_0 <= rtype <= CLOSING_9 or rtype == FINAL:
            pass  # consumed by _parse_header; raw copies kept in the header dict
        else:
            unknown.append((f"0x{rtype:02x}", seq, rec))
        seq += 1

    return Dive(
        header=header,
        samples=samples,
        gases=gases,
        events=events,
        unknown_records=unknown,
        sample_interval_ms=interval_ms,
    )


def _parse_sample(
    rec: bytes,
    t: float,
    imperial: bool,
    calibrated: int,
    calibration: list[float],
    rtype: int,
    is_dciem: bool,
) -> Sample:
    ft = 0.3048
    depth = _u16be(rec, 1) / 10.0
    stop_depth = float(_u16be(rec, 3))
    if imperial:
        depth *= ft
        stop_depth *= ft

    temp = struct.unpack_from("b", rec, 14)[0]
    if temp < 0:
        temp = min(temp + 102, 0)
    if imperial:
        temp = (temp - 32.0) * 5.0 / 9.0

    status = rec[12] if rtype != AVELO_SAMPLE else 0
    ccr = (status & FLAG_CIRCUIT_MODE) == 0 if rtype != AVELO_SAMPLE else False

    sample = Sample(
        t_s=t,
        depth_m=round(depth, 2),
        temp_c=float(temp),
        stop_depth_m=stop_depth,
        tts_min=float(_u16be(rec, 5)),
        stop_or_ndl_min=float(rec[10]),
        in_deco=1 if _u16be(rec, 3) else 0,
        avg_ppo2=rec[7] / 100.0,
        o2_pct=float(rec[8]),
        he_pct=float(rec[9]),
        cns_pct=float(rec[23]),
        battery_pct=rec[11],
        battery_v=((rec[17] << 8) | rec[18]) / 100.0,
        status_flags=status,
        sensor1_raw=rec[13],
        sensor2_raw=rec[15],
        sensor3_raw=rec[16],
    )

    if rtype != AVELO_SAMPLE:
        sample.gas_switch_needed = bool(status & FLAG_GAS_SWITCH)
        sample.ppo2_external = bool(status & FLAG_PPO2_EXTERNAL)
        sample.setpoint_high = bool(status & FLAG_SETPOINT_HIGH)
        sample.circuit_switch = bool(status & FLAG_CIRCUIT_SWITCH)
        sample.circuit_mode = (status >> 4) & 1
        sample.ccr_mode = (status >> 5) & 1
        sample.solenoid_fired_count = (status >> SOLENOID_COUNT_SHIFT) & 3

    # Bytes 24-25 are ceiling + GF99 under gradient-factor deco models. Under
    # DCIEM they are a safe-ascent depth: byte 24 whole metres minus byte 25
    # hundredths.
    if is_dciem:
        sample.safe_ascent_depth_m = rec[24] - rec[25] / 100.0
    else:
        if rec[24] != 0xFF:
            sample.ceiling_m = float(rec[24])
        if rec[25] != 0xFF:  # 0xFF = not available (still on-gassing)
            sample.gf99 = float(rec[25])

    if ccr:
        sample.setpoint = rec[19] / 100.0
        if not (status & FLAG_PPO2_EXTERNAL):
            if calibrated & 0x01:
                sample.sensor1_ppo2 = round(rec[13] * calibration[0], 3)
            if calibrated & 0x02:
                sample.sensor2_ppo2 = round(rec[15] * calibration[1], 3)
            if calibrated & 0x04:
                sample.sensor3_ppo2 = round(rec[16] * calibration[2], 3)

    # Wireless air integration: 12-bit pressure (x2 psi) in the low bits,
    # transmitter battery in the top nibble. 0xFFF0+ means no transmitter.
    for idx, key in ((20, "tank1_psi"), (28, "tank0_psi")):
        v = _u16be(rec, idx)
        if v < 0xFFF0:
            pressure = (v & 0x0FFF) * 2.0
            battery = (v >> 12) & 0x0F
            setattr(sample, key, pressure)
            if battery:
                sample.extra[key + "_battery"] = battery

    if rec[22] < 0xF0:
        sample.gas_time_min = float(rec[22])

    at_plus_five = _u16be(rec, 26)  # TTS if the diver stayed 5 more minutes
    if at_plus_five != 0xFFFF:
        sample.extra["at_plus_five_min"] = at_plus_five

    sac_raw = _u16be(rec, 30)
    if sac_raw != SAC_SENTINEL:
        sample.sac = sac_raw / 100.0

    return sample


def _parse_header(
    data: bytes,
    opening: dict[int, int],
    closing: dict[int, int],
    final_off: int | None,
) -> dict:
    o = opening
    c = closing

    logversion = data[o[4] + 16]
    imperial = data[o[0] + 8] == 1

    gas_o2 = [data[o[0] + 20 + i] for i in range(10)]
    gas_he = [data[o[0] + 30 + i] for i in range(2)] + [
        data[o[1] + 1 + i] for i in range(8)
    ]

    calib_base = o[3] + 6
    calibrated_mask = data[calib_base]
    calibration = [_u16be(data, calib_base + 1 + i * 2) / 100000.0 for i in range(3)]

    interval_ms = 10000
    if logversion >= 9 and 5 in o:
        interval_ms = _u16be(data, o[5] + 23) or 10000

    max_depth = _u16be(data, c[0] + 4) / 10.0
    if imperial:
        max_depth *= 0.3048

    header = {
        "logversion": logversion,
        "imperial": imperial,
        "divemode": DIVE_MODES.get(data[o[4] + 1], str(data[o[4] + 1])),
        "gf_low": data[o[0] + 4],
        "gf_high": data[o[0] + 5],
        "deco_model": DECO_MODELS.get(data[o[2] + 18], str(data[o[2] + 18])),
        "vpmb_conservatism": data[o[2] + 19],
        "atmospheric_mbar": _u16be(data, o[1] + 16),
        "water_density": _u16be(data, o[3] + 3),
        "sensor_calibrated_mask": calibrated_mask,
        "sensor_calibration": calibration,
        "gas_o2": gas_o2,
        "gas_he": gas_he,
        "gas_enabled_mask": _u16be(data, o[4] + 17),
        "ai_mode": data[o[4] + 28],
        "sample_interval_ms": interval_ms,
        "max_depth_m": round(max_depth, 2),
        "divetime_s": _u24be(data, c[0] + 6),
    }

    if final_off is not None:
        header["computer_serial"] = _u32be(data, final_off + 2)
        header["computer_firmware"] = _bcd2dec(data[final_off + 10])
        model_id = data[final_off + 13]
        header["computer_model_id"] = model_id
        header["computer_model"] = COMPUTER_MODELS.get(model_id, str(model_id))

    if logversion >= 17:
        for name, recs in (("gnss_start", o), ("gnss_end", c)):
            if 9 in recs:
                off = recs[9]
                fix = data[off + 16]
                if fix in (2, 3):  # 2D / 3D fix
                    lat = struct.unpack_from(">i", data, off + 21)[0] / 100000.0
                    lon = struct.unpack_from(">i", data, off + 25)[0] / 100000.0
                    header[name] = {"fix": fix, "lat": lat, "lon": lon}

    # Verbatim copies of every config record, keyed by record type.
    header["raw_opening"] = {
        f"0x{OPENING_0 + i:02x}": data[off : off + RECORD_SIZE].hex()
        for i, off in sorted(o.items())
    }
    header["raw_closing"] = {
        f"0x{CLOSING_0 + i:02x}": data[off : off + RECORD_SIZE].hex()
        for i, off in sorted(c.items())
    }
    if final_off is not None:
        header["raw_final"] = data[final_off : final_off + RECORD_SIZE].hex()

    return header
