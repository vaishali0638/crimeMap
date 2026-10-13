"""Signed batch envelopes for file-based sources (the demo adapter, and a model for approved feeds).

A batch file is ``{"envelope": {...}, "signature": "<hex HMAC-SHA256>"}``; the HMAC covers the
canonical JSON of the envelope (sorted keys, no whitespace). Verification uses a constant-time
comparison and happens before any record is staged.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT = "crimemap-batch/1"
MAX_BATCH_BYTES = 64 * 1024 * 1024


class BatchSignatureError(Exception):
    """The batch is unsigned, malformed, or its signature does not verify."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(envelope: dict[str, Any], key: bytes) -> str:
    if len(key) < 32:
        raise ValueError("signing key must be at least 32 bytes")
    return hmac.new(key, canonical_json(envelope), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Batch:
    envelope: dict[str, Any]
    signature: str
    path: Path


def read_batch(path: Path, key: bytes) -> Batch:
    if path.stat().st_size > MAX_BATCH_BYTES:
        raise BatchSignatureError("batch file too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        envelope, signature = data["envelope"], data["signature"]
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        # RecursionError: deeply nested JSON (a parser bomb) is malformed, not a crash.
        raise BatchSignatureError("malformed batch file") from exc
    if not isinstance(envelope, dict) or not isinstance(signature, str):
        raise BatchSignatureError("malformed batch file")
    if not hmac.compare_digest(sign(envelope, key), signature):
        raise BatchSignatureError("signature does not verify")
    if envelope.get("format") != FORMAT:
        raise BatchSignatureError("unsupported batch format")
    records = envelope.get("records")
    if not isinstance(records, list) or envelope.get("record_count") != len(records):
        raise BatchSignatureError("record_count does not match records")
    return Batch(envelope=envelope, signature=signature, path=path)
