"""Read ``sw-pnf`` blobs from a Shearwater Cloud database.

Shearwater Cloud's desktop app exports an SQLite database (``dive_data.db``,
via "Export Database") whose ``log_data`` table holds one row per dive with the
native log in a ``data_bytes_1`` BLOB, tagged ``sw-pnf`` in ``format``.

These helpers pull those blobs out and decode them. They read the database
read-only and only touch ``log_data``, so they are insensitive to the rest of
Cloud's (large and frequently-changing) schema. Everything else about a dive,
including the start time, dive number, computer serial, model, and
configuration, comes from the decoded log itself.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from ._decode import Dive, decode

PNF_FORMAT = "sw-pnf"


def _connect_readonly(path: str | Path) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def iter_log_blobs(path: str | Path) -> Iterator[tuple[str, bytes]]:
    """Yield ``(log_id, blob)`` for every ``sw-pnf`` log in ``dive_data.db``."""
    con = _connect_readonly(path)
    try:
        rows = con.execute(
            "SELECT log_id, data_bytes_1 FROM log_data WHERE format = ?",
            (PNF_FORMAT,),
        )
        for log_id, blob in rows:
            if blob:
                yield str(log_id), bytes(blob)
    finally:
        con.close()


def decode_database(path: str | Path) -> Iterator[tuple[str, Dive]]:
    """Yield ``(log_id, Dive)`` for every ``sw-pnf`` log in ``dive_data.db``.

    A blob that fails to decode raises. Use :func:`iter_log_blobs` and call
    :func:`decode` yourself if you want to skip bad logs.
    """
    for log_id, blob in iter_log_blobs(path):
        yield log_id, decode(blob)
