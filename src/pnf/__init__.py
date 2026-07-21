"""pnf: a pure-Python decoder for Shearwater's Petrel Native Format dive logs.

Quick start::

    import pnf

    # Decode every dive in a Shearwater Cloud "Export Database" file:
    for log_id, dive in pnf.decode_database("dive_data.db"):
        print(dive.header["computer_model"], len(dive.samples), "samples")
        for s in dive.samples:
            if s.gf99 is not None:
                print(s.t_s, s.depth_m, s.gf99)

    # Or decode a single sw-pnf blob you already have in hand:
    dive = pnf.decode(blob)

See ``SPEC.md`` for the format specification. This is independent reverse
engineering for interoperability and is not affiliated with or endorsed by
Shearwater Research.
"""

from ._database import PNF_FORMAT, decode_database, iter_log_blobs
from ._decode import (
    COMPUTER_MODELS,
    DECO_MODELS,
    DIVE_MODES,
    RECORD_SIZE,
    Dive,
    Event,
    Gas,
    Sample,
    decode,
    decompress,
)

__version__ = "0.1.0"

__all__ = [
    "decode",
    "decompress",
    "decode_database",
    "iter_log_blobs",
    "Dive",
    "Sample",
    "Gas",
    "Event",
    "COMPUTER_MODELS",
    "DECO_MODELS",
    "DIVE_MODES",
    "RECORD_SIZE",
    "PNF_FORMAT",
    "__version__",
]
