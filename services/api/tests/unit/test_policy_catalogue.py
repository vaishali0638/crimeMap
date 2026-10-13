"""The role → permission matrix in code must equal the matrix in the documentation.

docs/data-classification-and-access.md §4 is the single source of truth (ADR-002). If either side
changes without the other, this test fails.
"""

import re
from pathlib import Path

import pytest

from crimemap_api.policy.catalogue import (
    PERMISSIONS,
    ROLE_PERMISSIONS,
    Grant,
    Purpose,
    Role,
    Scope,
)

DOC = Path(__file__).resolve().parents[4] / "docs" / "data-classification-and-access.md"

# Column order of the documented matrix.
DOC_COLUMNS = [
    Role.SYSTEM_ADMIN,
    Role.DATA_STEWARD,
    Role.CRIME_ANALYST,
    Role.STATION_OFFICER,
    Role.SHO,
    Role.DISTRICT_OR_REGION_SUPERVISOR,
    Role.SENIOR_LEADERSHIP,
    Role.AUDITOR,
    Role.DEMO_USER,
]

SCOPE_LETTERS = {"✓": Scope.GLOBAL, "R": Scope.REGION, "S": Scope.STATION, "A": Scope.GLOBAL}


def parse_cell(cell: str) -> Grant | None:
    """Translate one documented matrix cell into the Grant it describes."""
    cell = cell.strip()
    if cell == "\u2013":  # en dash, as written in the documented matrix
        return None
    m = re.fullmatch(r"(✓|R|S|A)( ⊕)?(?: \((.+)\))?", cell)
    assert m, f"unrecognised matrix cell {cell!r}"
    letter, reauth, note = m.group(1), bool(m.group(2)), m.group(3)
    scope = SCOPE_LETTERS[letter]
    aggregate_only = letter == "A" or note == "A"
    purpose = None
    if note == "assigned":
        scope, note = Scope.ASSIGNED, None
    elif note == "DQ":
        purpose, note = Purpose.DQ_REVIEW, None
    elif note == "A":
        note = None
    return Grant(
        scope=scope, aggregate_only=aggregate_only, reauth=reauth, purpose=purpose, note=note
    )


def documented_matrix() -> dict[str, dict[Role, Grant]]:
    rows: dict[str, dict[Role, Grant]] = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| ([a-z_]+\.[a-z_.]+) \|(.*)\|$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        assert len(cells) == len(DOC_COLUMNS), f"row {m.group(1)} has {len(cells)} cells"
        grants = {}
        for role, cell in zip(DOC_COLUMNS, cells, strict=True):
            grant = parse_cell(cell)
            if grant is not None:
                grants[role] = grant
        rows[m.group(1)] = grants
    return rows


def test_documented_matrix_is_found() -> None:
    assert len(documented_matrix()) >= 30


def test_every_documented_permission_exists_in_code() -> None:
    assert set(documented_matrix()) == set(PERMISSIONS)


@pytest.mark.parametrize("permission", sorted(documented_matrix()))
def test_code_grants_match_documentation(permission: str) -> None:
    expected = documented_matrix()[permission]
    actual = {
        role: perms[permission] for role, perms in ROLE_PERMISSIONS.items() if permission in perms
    }
    assert actual == expected


def test_no_role_holds_restricted_read_by_default() -> None:
    assert all("restricted.read" not in perms for perms in ROLE_PERMISSIONS.values())


def test_system_admin_has_no_case_data_permission() -> None:
    case_data = {p for p in PERMISSIONS if p.startswith(("map.", "incident.", "restricted."))}
    assert case_data.isdisjoint(ROLE_PERMISSIONS[Role.SYSTEM_ADMIN])


def test_every_role_is_defined() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)
