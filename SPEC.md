# The Petrel Native Format (PNF), an unofficial specification

This document describes **Shearwater's Petrel Native Format**, the on-device
dive-log format used by Shearwater's Petrel-family dive computers (Petrel,
Perdix, Teric, Nerd, Peregrine, and successors), as stored in the SQLite
database that Shearwater Cloud's desktop app exports.

It is an **unofficial, independent** specification, produced by reverse
engineering for interoperability. It is not affiliated with or endorsed by
Shearwater Research. See [Provenance](#provenance) for how each field was
established and verified.

Everything here is validated against Perdix 2 and Petrel 3 logs. Offsets in the
dive-sample record are stable across native-format versions 7 through 17; the
per-version differences are in the configuration records and in added features
(see [Versions](#versions)).

---

## 1. Container

Shearwater Cloud's desktop app can export dives via **File → Export → Export
Database**, producing a SQLite file (`dive_data.db`). Inside it, the `log_data`
table holds one row per dive:

| column | meaning |
| --- | --- |
| `log_id` | primary key for the log |
| `format` | log format tag; the native format is `sw-pnf` |
| `data_bytes_1` | the native-format log, as a BLOB |

A `sw-pnf` blob is:

```
+-------------------+--------------------------------------+
| 4 bytes           | rest of blob                         |
| uncompressed len  | gzip stream                          |
| (little-endian)   |                                      |
+-------------------+--------------------------------------+
```

Decode it as:

```python
import struct, gzip
expected = struct.unpack_from("<I", blob, 0)[0]
data = gzip.decompress(blob[4:])
assert len(data) == expected
assert len(data) % 32 == 0
```

The decompressed payload is a sequence of fixed **32-byte records**. Byte 0 of
each record is its **type**. Records appear in stream order; samples are
implicitly timestamped by their position (see [Timing](#timing)). All-zero
32-byte records are padding and are skipped.

---

## 2. Record types

Byte 0 of each record:

| byte 0 | name | notes |
| --- | --- | --- |
| `0x00` | padding | all-zero record, skipped |
| `0x01` | dive sample | the main per-interval record ([§4](#4-dive-sample-record)) |
| `0x02` | freedive sample | four 8-byte sub-samples per record |
| `0x03` | Avelo sample | Avelo-system sample variant |
| `0x10`–`0x19` | opening records 0–9 | dive configuration ([§3](#3-configuration-records)) |
| `0x20`–`0x29` | closing records 0–9 | end-of-dive configuration ([§3](#3-configuration-records)) |
| `0x30` | info event | timestamped event record |
| `0x40` | DiveCAN event | *not decoded* ([§7](#7-record-types-nobody-decodes)) |
| `0x50` | button push | *not decoded* |
| `0x60` | surface ppO2 | *not decoded* |
| `0x70`–`0x75` | tissue-loading snapshots | *not decoded* |
| `0x80`–`0x87` | opening-record validity maps | *not decoded* |
| `0x88` | dive-sample validity map | *not decoded* |
| `0xE1` | extended dive sample | extra air-integration tanks for the previous sample |
| `0xFD` | final log byte 2 | |
| `0xFF` | final log byte 1 | serial, model, firmware ([§5](#5-final-record)) |

The record types marked *not decoded* are named in Shearwater's own parser but
their payloads are not interpreted there either; see [§7](#7-record-types-nobody-decodes).

---

## 3. Configuration records

Each dive carries five to ten **opening** records (`0x10`+) with the
configuration at the start of the dive, and matching **closing** records
(`0x20`+) with the end-of-dive state. Records 0–4 are always present; 5 and 9
appear on newer log versions. Fields are at absolute offsets within the 32-byte
record.

**Opening 0 (`0x10`)**

| offset | field |
| --- | --- |
| 4 | gradient factor low |
| 5 | gradient factor high |
| 8 | units (0 = metric, 1 = imperial) |
| 20–29 | O2 percent for gases 0–9 |
| 30–31 | He percent for gases 0–1 |

**Opening 1 (`0x11`)**

| offset | field |
| --- | --- |
| 1–8 | He percent for gases 2–9 |
| 16–17 | atmospheric pressure, mbar (u16 BE) |

**Opening 2 (`0x12`)**

| offset | field |
| --- | --- |
| 18 | deco model (see [§6](#6-deco-models)) |
| 19 | VPM-B conservatism |

**Opening 3 (`0x13`)**

| offset | field |
| --- | --- |
| 3–4 | water density (u16 BE) |
| 6 | O2 sensor calibration mask (bit per cell) |
| 7–12 | three u16 BE cell calibration coefficients (÷100000) |

**Opening 4 (`0x14`)**

| offset | field |
| --- | --- |
| 1 | dive mode (see [§6](#6-deco-models)) |
| 16 | log version |
| 17–18 | gas-enabled bitmask (u16 BE, bit per gas) |
| 28 | air-integration mode |

**Opening 5 (`0x15`)** — log version ≥ 9

| offset | field |
| --- | --- |
| 23–24 | sample interval, ms (u16 BE) |

**Opening 9 (`0x19`)** — log version ≥ 17: GNSS entry fix (see [§8](#8-gnss)).

**Closing 0 (`0x20`)**

| offset | field |
| --- | --- |
| 4–5 | maximum depth, decimetres (u16 BE) |
| 6–8 | dive time, seconds (u24 BE) |

Closing 9 (`0x29`) carries the GNSS exit fix. The remaining closing records
mirror their opening counterparts with end-of-dive values.

---

## 4. Dive sample record

A `0x01` record. This is where the per-interval dive data lives. All multi-byte
integers are **big-endian**.

| offset | field | type / scaling | notes |
| --- | --- | --- | --- |
| 0 | record type | `0x01` | |
| 1–2 | depth | u16 BE, ÷10 → m | |
| 3–4 | next stop depth | u16 BE → m | 0 = no deco stop |
| 5–6 | time to surface (TTS) | u16 BE → min | |
| 7 | average ppO2 | ÷100 → ata | |
| 8 | current gas O2 | percent | |
| 9 | current gas He | percent | |
| 10 | NDL **or** next-stop time | minutes | NDL if next-stop-depth (3–4) is 0, otherwise next-stop time |
| 11 | battery percent remaining | percent | 0–100 |
| 12 | status / mode bits | bitfield | see [§4.1](#41-status-byte-byte-12) |
| 13 | O2 cell 1 | millivolts (raw) | ×calibration → ppO2 on CC |
| 14 | water temperature | °C | signed byte; see note below |
| 15 | O2 cell 2 | millivolts (raw) | |
| 16 | O2 cell 3 | millivolts (raw) | |
| 17 | battery voltage, high byte | — | with byte 18: `(17<<8 | 18) / 100` → volts |
| 18 | battery voltage, low byte | — | high byte is 0 for a ~1.5 V cell but non-zero on higher-voltage computers |
| 19 | current ppO2 setpoint | ÷100 → ata | closed circuit |
| 20–21 | tank 1 pressure (AI) | see [§4.2](#42-air-integration-bytes-20-21-and-28-29) | |
| 22 | gas time remaining (AI) | minutes | `0xFF` = n/a |
| 23 | CNS | percent | |
| 24 | deco ceiling **or** safe-ascent depth (int) | m | ceiling under GF/VPM; under DCIEM this is the integer metres of the safe-ascent depth ([§6](#6-deco-models)) |
| 25 | GF99 **or** safe-ascent depth (frac) | percent / hundredths | GF99 under GF/VPM (`0xFF` = n/a while on-gassing); under DCIEM this is the safe-ascent-depth fraction (÷100) |
| 26–27 | @+5 prediction | u16 BE → min | TTS if the diver stayed five more minutes; `0xFFFF` = n/a |
| 28–29 | tank 0 pressure (AI) | see [§4.2](#42-air-integration-bytes-20-21-and-28-29) | |
| 30–31 | surface air consumption (SAC) | u16 BE, ÷100 | `0xFFFF` = n/a (no air integration) |

**Water temperature note.** Byte 14 is a signed byte read directly as °C for
above-freezing water. Sub-zero encodings follow the offset used by
libdivecomputer's Petrel parser; only above-freezing samples were available to
verify here.

### 4.1 Status byte (byte 12)

A packed bitfield:

| bit(s) | field | meaning |
| --- | --- | --- |
| 0 | gas switch needed | a suggested gas switch is pending |
| 1 | external ppO2 | ppO2 comes from an external monitor |
| 2 | setpoint type | high (1) vs low (0) setpoint selected |
| 3 | circuit switch type | |
| 4 | circuit mode | set = open circuit on this family |
| 5 | CCR mode | closed-circuit rebreather mode active |
| 6–7 | solenoid fired count | count of O2 solenoid firings this interval (0–3) |

### 4.2 Air integration (bytes 20–21 and 28–29)

Each wireless transmitter is a u16 BE:

- `>= 0xFFF0` → no transmitter paired.
- otherwise: **pressure** = `(value & 0x0FFF) * 2` psi, and the transmitter's
  **battery level** is the top nibble `(value >> 12) & 0x0F`.

The `0xE1` extended-sample record, when present, immediately follows a dive
sample and carries pressures for additional tanks (2, 3, and the diluent/O2
tanks) in the same 12-bit ×2 encoding.

---

## 5. Final record

A `0xFF` record ends the log and identifies the computer:

| offset | field | type |
| --- | --- | --- |
| 2–5 | serial number | u32 BE |
| 10 | firmware version | BCD |
| 13 | computer model id | byte (see below) |

Model ids: 2 Predator, 3 Petrel, 4 Nerd, 5 Perdix, 6 Perdix AI, 7 Nerd 2,
8 Teric, 9 Peregrine, 10 Petrel 3, 11 Perdix 2, 12 Tern.

---

## 6. Deco models

Opening record 2, byte 18:

| value | model |
| --- | --- |
| 0 | Bühlmann ZHL-16C + gradient factors (GF) |
| 1 | VPM-B |
| 2 | VPM-B/GFS |
| 3 | DCIEM |

**This selects how sample bytes 24–25 are read.** Under GF, VPM-B, and
VPM-B/GFS, byte 24 is the deco ceiling (metres) and byte 25 is GF99 (percent).
Under **DCIEM**, which does not use gradient factors, the same two bytes are a
safe-ascent depth: byte 24 is whole metres and byte 25 is hundredths, so the
depth is `byte24 - byte25/100`. A decoder that reads byte 25 as GF99
unconditionally will mis-decode DCIEM dives.

Dive mode (opening 4, byte 1) is a separate field: 0 CC, 1 OC tec, 2 gauge,
3 ppO2, 4 SC, 5 CC2, 6 OC rec, 7 freedive, 12 Avelo.

---

## 7. Record types nobody decodes

The record types `0x40` (DiveCAN event), `0x50` (button push), `0x60` (surface
ppO2), `0x70`–`0x75` (tissue-loading snapshots), and `0x80`–`0x88` (validity
maps) are **named** in Shearwater's own desktop parser but their payloads are
**not decoded** there; the parser routes them to its unknown-record handler.
Their internal layout is therefore not publicly known. A conforming decoder
should preserve these records verbatim rather than drop them, so that a future
decoder can revisit them. There is, in particular, no recoverable per-compartment
tissue-loading data beyond what the named-but-unparsed tissue records might one
day yield.

---

## 8. GNSS

On log version ≥ 17, opening record 9 (`0x19`) and closing record 9 (`0x29`)
carry the surface GNSS fix at entry and exit:

| offset | field |
| --- | --- |
| 16 | fix type (2 = 2D, 3 = 3D; other = no fix) |
| 21–24 | latitude, i32 BE, ÷100000 → degrees |
| 25–28 | longitude, i32 BE, ÷100000 → degrees |

---

## 9. Timing

Samples carry no per-record timestamp; each dive sample is one sample interval
after the previous one. The interval is 10 s by default, or the value in opening
record 5 (bytes 23–24) on log version ≥ 9. The dive's wall-clock start time is
in Shearwater Cloud's `dive_logs` table, not in the blob; note that Shearwater's
sample timestamps are a local wall clock with no timezone attached.

---

## 10. Versions

The dive-sample record layout in [§4](#4-dive-sample-record) is identical across
native-format parser versions 7 through 17: the same offsets carry depth (1–2),
GF99 (25), deco ceiling (24), battery voltage (17–18), and so on in every
version. Version differences are in the configuration records and in added
capabilities: the sample interval field (opening 5) appears at log version ≥ 9,
and GNSS fixes (opening/closing 9) at log version ≥ 17. A single dive-sample
decoder is therefore correct across the whole version range; only the header
parsing needs to be version-aware.

---

## Provenance

The fields in this specification were established in three layers, each checked
against the next:

1. **libdivecomputer.** The documented sample fields (depth, next stop, TTS, gas
   fractions, temperature, CNS, and the cell/AI layout) follow
   [`shearwater_predator_parser.c`](https://github.com/libdivecomputer/libdivecomputer/blob/master/src/shearwater_predator_parser.c).
   PNF prepends the record-type byte, so documented sample offsets sit one byte
   later than in the legacy Predator format.

2. **Observation.** The channels the exports omit (GF99, deco ceiling, battery
   voltage, @+5) were identified by decoding candidate bytes across many real
   dives and testing each hypothesis: byte 25 converges on Shearwater Cloud's own
   per-dive `EndGF99` summary and reads a sentinel exactly while tissues are
   on-gassing; byte 24 tracks the 3 m-quantized stop depth from below and clears
   when deco clears; byte 18 equals the XML export's `batteryVoltage × 100`.

3. **Reconciliation.** The naming, the 16-bit battery voltage (bytes 17–18), the
   battery-percent byte (11), the SAC bytes (30–31), and the DCIEM branch for
   bytes 24–25 were confirmed against the field layout in Shearwater Cloud's own
   log parser. This step named the bytes; it did not copy any code.

The result was verified end to end: the decoded documented channels match
Shearwater Cloud's XML export byte-for-byte across every shared sample of 142
dives; the decoded GF99's surfacing value agrees with Cloud's `EndGF99` on 142 of
142 dives within one gradient-factor point; the decoded ceiling never exceeds the
decoded stop depth across tens of thousands of samples; and the decoded depth
profile agrees with a completely independent second dive computer (a Garmin
Descent) to a median of ~0.16 m after clock alignment.

## Acknowledgments

The [libdivecomputer](https://github.com/libdivecomputer/libdivecomputer)
project, whose Shearwater parser is the closest thing to a public reference for
this family, and the [Subsurface](https://subsurface-divelog.org/) project, whose
Shearwater importer reads Cloud's pre-decoded tables. This document exists to
cover the gap they leave: the native-format blob itself.

## Interoperability and legal note

This specification documents facts about a file format (byte offsets and field
meanings) for the purpose of interoperability: letting owners read their own dive
logs. Format facts are not software and contain no Shearwater code. This work is
independent of Shearwater Research. Shearwater has historically supported
third-party developers and offers a developer program for those who want an
officially-supported integration path.
