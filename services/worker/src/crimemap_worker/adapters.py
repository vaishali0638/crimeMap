"""Source adapters (spec: pluggable adapter architecture; demo adapter built fully; approved-source
adapters as interfaces/stubs only).

An adapter only *delivers* signed batch files. Everything after delivery (signature check, staging,
validation, enrichment, DQ, lineage, audit) is the shared pipeline in ``crimemap_worker.ingest``, so
a future approved adapter cannot bypass any control. No adapter for a government system can run:
each is a stub that raises ``AdapterNotApproved`` until a real, authorised integration replaces it
(docs/integration-contracts.md).
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol

RUNNABLE_STATUSES = frozenset({"DEMO", "APPROVED"})


class AdapterNotApproved(PermissionError):
    """Raised when an adapter may not run (source not approved, or no authorised integration)."""


class SourceAdapter(Protocol):
    @property
    def source_code(self) -> str: ...

    def pending_batches(self) -> list[Path]:
        """Signed batch files waiting to be ingested, in delivery order."""
        ...


@dataclass(frozen=True)
class SignedFileDropAdapter:
    """Reads ``crimemap-batch/1`` files from ``<inbox>/<source code, lower case>/``.

    Used by the demo feeds. The files are produced by the synthetic generator; the adapter trusts
    nothing in them: the pipeline verifies each signature before staging a single record."""

    source_code: str
    inbox: Path

    @property
    def directory(self) -> Path:
        return self.inbox / self.source_code.lower()

    def pending_batches(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(
            p
            for p in self.directory.glob("*.json")
            # Regular files only: a symbolic link could point the worker at a file elsewhere.
            if p.is_file() and not p.is_symlink() and not p.name.endswith("-manifest.json")
        )


@dataclass(frozen=True)
class NotApprovedAdapter:
    """Interface stub for a government system. It has no connection code and always refuses."""

    source_code: str
    system_name: str

    def pending_batches(self) -> list[Path]:
        raise AdapterNotApproved(
            f"{self.source_code} ({self.system_name}) has no authorised integration; connecting it "
            "requires written authorisation, legal review, a data-sharing agreement, security "
            "testing and owner approval"
        )


_STUBS = {
    "CCTNS": "Crime and Criminal Tracking Network & Systems",
    "ERSS_112": "Emergency Response Support System 112",
    "ICJS": "Inter-operable Criminal Justice System",
    "EDAR": "e-Detailed Accident Report",
    "ECHALLAN": "e-challan",
    "CCTV_ICCC": "CCTV / Integrated Command and Control Centre metadata",
}


def _stub(code: str, name: str, _inbox: Path) -> SourceAdapter:
    return NotApprovedAdapter(code, name)


# Registry: source code -> factory building its adapter for an inbox directory.
AdapterFactory = Callable[[Path], SourceAdapter]
ADAPTERS: dict[str, AdapterFactory] = {
    "DEMO_SYNTHETIC": partial(SignedFileDropAdapter, "DEMO_SYNTHETIC"),
    "DEMO_SYNTHETIC_LEGACY": partial(SignedFileDropAdapter, "DEMO_SYNTHETIC_LEGACY"),
    **{code: partial(_stub, code, name) for code, name in _STUBS.items()},
}


def adapter_for(source_code: str, approved_status: str, inbox: Path) -> SourceAdapter:
    """Build the adapter for a source, refusing sources that are not approved to run."""
    if approved_status not in RUNNABLE_STATUSES:
        raise AdapterNotApproved(f"{source_code} is {approved_status}; it may not be ingested")
    factory = ADAPTERS.get(source_code)
    if factory is None:
        raise AdapterNotApproved(f"no adapter is registered for {source_code}")
    return factory(inbox)
