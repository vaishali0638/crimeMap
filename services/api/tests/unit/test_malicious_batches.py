"""Malicious or malformed batch files are rejected before anything is staged (Phase 8 security
tests: malicious upload). Batches are the only file input; the API has no upload endpoint."""

import json
from pathlib import Path

import pytest

from crimemap_api.ingest.signing import (
    FORMAT,
    MAX_BATCH_BYTES,
    BatchSignatureError,
    read_batch,
    sign,
)

KEY = b"k" * 32


def envelope(records: list[object]) -> dict[str, object]:
    return {"format": FORMAT, "records": records, "record_count": len(records)}


def write(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "batch.json"
    path.write_bytes(content)
    return path


def test_a_valid_batch_is_read(tmp_path: Path) -> None:
    env = envelope([{"a": 1}])
    path = write(tmp_path, json.dumps({"envelope": env, "signature": sign(env, KEY)}).encode())
    assert read_batch(path, KEY).envelope == env


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json",
        b"\xff\xfe\x00 invalid utf-8",
        b"[]",
        b'{"envelope": [], "signature": "x"}',
        b'{"envelope": {}, "signature": 5}',
        b'{"envelope": {"format": "crimemap-batch/1"}}',
        # Deep nesting: the parser's recursion limit must become a rejection, not a crash.
        b'{"envelope": ' + b"[" * 200_000 + b"]" * 200_000 + b', "signature": "x"}',
    ],
    ids=[
        "empty",
        "not-json",
        "bad-utf8",
        "array",
        "envelope-array",
        "signature-number",
        "no-signature",
        "deep-nesting",
    ],
)
def test_malformed_files_are_rejected(tmp_path: Path, content: bytes) -> None:
    with pytest.raises(BatchSignatureError):
        read_batch(write(tmp_path, content), KEY)


def test_an_oversized_file_is_rejected_without_being_read(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    with path.open("wb") as f:
        f.truncate(MAX_BATCH_BYTES + 1)  # sparse: no real disk use
    with pytest.raises(BatchSignatureError, match="too large"):
        read_batch(path, KEY)


def test_a_forged_or_altered_batch_is_rejected(tmp_path: Path) -> None:
    env = envelope([{"a": 1}])
    good = sign(env, KEY)
    forged = sign(env, b"x" * 32)
    for signature in (forged, good.upper(), good[:-1] + ("0" if good[-1] != "0" else "1"), ""):
        path = write(tmp_path, json.dumps({"envelope": env, "signature": signature}).encode())
        with pytest.raises(BatchSignatureError):
            read_batch(path, KEY)
    altered = {**env, "records": [{"a": 2}]}
    path = write(tmp_path, json.dumps({"envelope": altered, "signature": good}).encode())
    with pytest.raises(BatchSignatureError):
        read_batch(path, KEY)


def test_a_signed_batch_with_a_wrong_record_count_is_rejected(tmp_path: Path) -> None:
    env = {**envelope([{"a": 1}]), "record_count": 2}
    path = write(tmp_path, json.dumps({"envelope": env, "signature": sign(env, KEY)}).encode())
    with pytest.raises(BatchSignatureError, match="record_count"):
        read_batch(path, KEY)
