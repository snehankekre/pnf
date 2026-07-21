# pnf

A pure-Python decoder for **Shearwater's Petrel Native Format (PNF)**, the
on-device log format used by Shearwater's Petrel-family dive computers (Petrel,
Perdix, Teric, Nerd, Peregrine, and successors).

Shearwater Cloud can export your dives to XML, CSV, or its own SQLite database.
The XML and CSV are lossy: they drop channels the computer actually recorded,
including GF99, the deco ceiling, per-sample battery voltage and percentage, and
the "@+5" prediction. The SQLite export keeps the real thing: each dive is
stored as a compressed native-format blob. `pnf` decodes those blobs.

No dependencies, standard library only, Python 3.9+.

```bash
pip install pnf
```

## Usage

Point it at a Shearwater Cloud "Export Database" file (`dive_data.db`):

```python
import pnf

for log_id, dive in pnf.decode_database("dive_data.db"):
    h = dive.header
    print(f"{h['computer_model']} #{h['computer_serial']:X}: "
          f"{h['max_depth_m']} m, {len(dive.samples)} samples")
    for s in dive.samples:
        if s.gf99 is not None:
            print(f"  t={s.t_s:.0f}s depth={s.depth_m}m gf99={s.gf99} "
                  f"ceiling={s.ceiling_m}m battery={s.battery_v}V")
```

Or decode a single `sw-pnf` blob you already have:

```python
dive = pnf.decode(blob)
```

## What you get

`decode()` returns a `Dive` with:

- `header` — a dict of dive configuration: gases, gradient factors, deco model,
  water density, atmospheric pressure, sample interval, computer serial / model
  / firmware, GNSS entry/exit fix (on computers that log it), and verbatim
  copies of the raw config records.
- `samples` — a list of `Sample`, one per log interval (typically every 10 s),
  each with depth, temperature, TTS, next-stop depth, NDL, average and per-cell
  ppO2, CNS, **GF99**, **deco ceiling**, **battery voltage and percentage**,
  gas fractions, setpoint, wireless-air-integration tank pressures, SAC, the
  decoded status/mode bits (including CCR solenoid-fired count), and the "@+5"
  prediction. Channels the computer did not record are `None`.
- `gases` — the ten programmed gases (O2/He, open- vs closed-circuit, enabled).
- `events` — info-event records.
- `unknown_records` — every record type this decoder does not interpret, kept
  verbatim as `(type_key, seq, raw_bytes)`. Shearwater's own desktop parser also
  leaves several record types (tissue snapshots, validity maps, surface ppO2,
  button pushes, DiveCAN events) undecoded; `pnf` preserves them rather than
  discarding them, so a future decoder can revisit them.

Under the DCIEM deco model, sample bytes 24-25 encode a safe-ascent depth
instead of a ceiling / GF99 pair; `pnf` decodes them to `safe_ascent_depth_m`
and leaves `ceiling_m` / `gf99` as `None`. See [SPEC.md](SPEC.md) for details.

## The format

[**SPEC.md**](SPEC.md) documents the container, the record types, and every byte
of the dive-sample record, with the provenance of each field.

## Provenance and scope

This is independent reverse engineering for **interoperability**: reading dive
logs you own, in Python, without going through Shearwater Cloud. The documented
sample fields follow the layout in
[libdivecomputer](https://github.com/libdivecomputer/libdivecomputer)'s
`shearwater_predator_parser.c`. The remaining fields were identified by
observation across real dives and cross-checked against Shearwater Cloud's own
outputs (its XML export and its per-dive `EndGF99` summary), then reconciled for
naming and edge cases such as the DCIEM branch.

`pnf` is not affiliated with or endorsed by Shearwater Research, contains no
Shearwater code, and does not redistribute any Shearwater software. Format facts
(byte offsets and field meanings) are not code. If you want a maintained,
officially-supported path, Shearwater offers a developer program.

Validated against Perdix 2 and Petrel 3 logs. The sample record layout is stable
across native-format versions, but other models may populate fields this decoder
has not seen. If a dive decodes wrong,
[open an issue](https://github.com/snehankekre/pnf/issues) with the blob; that is
exactly the data that improves the decoder.

## License

MIT. See [LICENSE](LICENSE).
