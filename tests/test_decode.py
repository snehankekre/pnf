"""Decoder tests against synthetic blobs built record by record.

The builder mirrors the real record layout so the tests double as executable
documentation of the format described in SPEC.md.
"""

import gzip
import struct

import pytest

import pnf
from pnf import _decode


def _record(rtype: int, payload=None) -> bytearray:
    rec = bytearray(32)
    rec[0] = rtype
    for offset, data in (payload or {}).items():
        rec[offset : offset + len(data)] = data
    return rec


def _sample(depth_dm: int, **kw) -> bytearray:
    rec = _record(_decode.DIVE_SAMPLE)
    struct.pack_into(">H", rec, 1, depth_dm)
    struct.pack_into(">H", rec, 3, kw.get("stop_m", 0))
    struct.pack_into(">H", rec, 5, kw.get("tts_min", 0))
    rec[7] = kw.get("ppo2_raw", 21)
    rec[8] = kw.get("o2", 21)
    rec[9] = kw.get("he", 0)
    rec[10] = kw.get("ndl_min", 99)
    rec[11] = kw.get("battery_pct", 100)
    rec[12] = kw.get("flags", _decode.FLAG_CIRCUIT_MODE)  # open circuit
    rec[14] = kw.get("temp", 27) & 0xFF
    rec[17] = kw.get("battery_msb", 0)
    rec[18] = kw.get("battery_raw", 152)
    struct.pack_into(">H", rec, 20, 0xFFFF)  # AI off
    rec[22] = 0xFF  # gas time not paired
    rec[23] = kw.get("cns", 0)
    rec[24] = kw.get("ceiling", 0xFF)
    rec[25] = kw.get("gf99", 0xFF)
    struct.pack_into(">H", rec, 26, kw.get("at_plus_five", 0))
    struct.pack_into(">H", rec, 28, 0xFFFF)  # AI off
    struct.pack_into(">H", rec, 30, kw.get("sac_raw", 0xFFFF))  # SAC n/a
    return rec


def build_blob(samples=None, interval_ms=10000, extra_records=None, deco_model=0) -> bytes:
    records = []

    opening0 = _record(0x10)
    opening0[4], opening0[5] = 40, 80  # GF low/high
    opening0[8] = 0  # metric
    opening0[20] = 21  # gas 0 O2
    opening0[21] = 50  # gas 1 O2
    records.append(opening0)

    opening1 = _record(0x11)
    struct.pack_into(">H", opening1, 16, 1013)  # atmospheric mbar
    records.append(opening1)

    opening2 = _record(0x12)
    opening2[18] = deco_model  # 0=GF, 1=VPM-B, 2=VPM-B/GFS, 3=DCIEM
    records.append(opening2)

    opening3 = _record(0x13)
    struct.pack_into(">H", opening3, 3, 1020)  # water density
    records.append(opening3)

    opening4 = _record(0x14)
    opening4[1] = 1  # OC tec dive mode
    opening4[16] = 13  # log version
    struct.pack_into(">H", opening4, 17, 0b11)  # gases 0,1 enabled
    records.append(opening4)

    opening5 = _record(0x15)
    struct.pack_into(">H", opening5, 23, interval_ms)
    records.append(opening5)

    records.extend(samples or [])
    records.extend(extra_records or [])

    for i in range(5):
        rec = _record(0x20 + i)
        if i == 0:
            struct.pack_into(">H", rec, 4, 528)  # max depth dm
            rec[6:9] = (3374).to_bytes(3, "big")  # dive time s
        records.append(rec)

    final = _record(0xFF)
    struct.pack_into(">I", final, 2, 0xA823C228)  # serial
    final[13] = 11  # Perdix 2
    records.append(final)

    raw = b"".join(bytes(r) for r in records)
    return struct.pack("<I", len(raw)) + gzip.compress(raw)


def test_header():
    dive = pnf.decode(build_blob(samples=[_sample(24)]))
    h = dive.header
    assert (h["gf_low"], h["gf_high"]) == (40, 80)
    assert h["deco_model"] == "gf"
    assert h["atmospheric_mbar"] == 1013
    assert h["water_density"] == 1020
    assert h["divemode"] == "oc_tec"
    assert h["max_depth_m"] == 52.8
    assert h["divetime_s"] == 3374
    assert h["computer_serial"] == 0xA823C228
    assert h["computer_model"] == "Perdix 2"
    assert h["sample_interval_ms"] == 10000


def test_samples():
    dive = pnf.decode(
        build_blob(
            samples=[
                _sample(24, ndl_min=99),
                _sample(528, stop_m=9, tts_min=14, cns=4, ceiling=9, gf99=30),
            ]
        )
    )
    assert len(dive.samples) == 2
    s0, s1 = dive.samples
    assert (s0.t_s, s1.t_s) == (0.0, 10.0)
    assert s0.depth_m == 2.4
    assert s0.gf99 is None and s0.ceiling_m is None  # 0xFF sentinel
    assert s0.battery_v == 1.52
    assert s0.battery_pct == 100
    assert s0.sac is None  # 0xFFFF sentinel
    assert s1.depth_m == 52.8
    assert (s1.stop_depth_m, s1.tts_min, s1.in_deco) == (9.0, 14.0, 1)
    assert (s1.ceiling_m, s1.gf99, s1.cns_pct) == (9.0, 30.0, 4.0)


def test_battery_voltage_is_16bit():
    # A higher-voltage computer (e.g. a rechargeable Teric) overflows a single
    # byte, so the decoder must combine bytes 17-18.
    dive = pnf.decode(build_blob(samples=[_sample(24, battery_msb=1, battery_raw=164)]))
    assert dive.samples[0].battery_v == round((1 << 8 | 164) / 100.0, 2)  # 4.20 V


def test_sac_present():
    dive = pnf.decode(build_blob(samples=[_sample(24, sac_raw=1234)]))
    assert dive.samples[0].sac == 12.34


def test_status_byte_bits():
    flags = 0x01 | 0x20 | (2 << 6)  # gas switch + CCR mode + solenoid count 2
    s = pnf.decode(build_blob(samples=[_sample(24, flags=flags)])).samples[0]
    assert s.gas_switch_needed is True
    assert s.ccr_mode == 1
    assert s.solenoid_fired_count == 2
    assert s.setpoint_high is False
    assert s.status_flags == flags


def test_dciem_uses_safe_ascent_depth():
    dive = pnf.decode(build_blob(samples=[_sample(300, ceiling=6, gf99=50)], deco_model=3))
    assert dive.header["deco_model"] == "dciem"
    s = dive.samples[0]
    assert s.ceiling_m is None and s.gf99 is None
    assert s.safe_ascent_depth_m == 6 - 0.50  # 5.5 m


def test_unknown_records_preserved():
    mystery = _record(0x51, {1: b"\xde\xad\xbe\xef"})
    dive = pnf.decode(build_blob(samples=[_sample(24)], extra_records=[mystery]))
    assert len(dive.unknown_records) == 1
    type_key, _seq, raw = dive.unknown_records[0]
    assert type_key == "0x51"
    assert raw == bytes(mystery)


def test_length_prefix_validated():
    blob = build_blob(samples=[_sample(24)])
    corrupted = struct.pack("<I", 999999) + blob[4:]
    with pytest.raises(ValueError, match="length mismatch"):
        pnf.decode(corrupted)


def test_decompress_roundtrip():
    blob = build_blob(samples=[_sample(24)])
    data = pnf.decompress(blob)
    assert len(data) % pnf.RECORD_SIZE == 0
