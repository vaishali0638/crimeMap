"""Permission catalogue and role → permission matrix.

This is the machine-readable form of docs/data-classification-and-access.md §4. A unit test parses
the documented table and fails if the two disagree, so widening a permission requires changing the
documentation (and, by policy, an ADR).
"""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    DATA_STEWARD = "DATA_STEWARD"  # specification: DATA_STEWARD / CRB_ANALYST
    CRIME_ANALYST = "CRIME_ANALYST"
    STATION_OFFICER = "STATION_OFFICER"
    SHO = "SHO"  # specification: SHO / STATION_SUPERVISOR
    DISTRICT_OR_REGION_SUPERVISOR = "DISTRICT_OR_REGION_SUPERVISOR"
    SENIOR_LEADERSHIP = "SENIOR_LEADERSHIP"
    AUDITOR = "AUDITOR"
    DEMO_USER = "DEMO_USER"


class Scope(StrEnum):
    GLOBAL = "GLOBAL"  # not limited by territory
    REGION = "REGION"  # within the grant's districts/regions
    STATION = "STATION"  # within the grant's stations/beats
    ASSIGNED = "ASSIGNED"  # within the grant's stations AND assigned to the user


class Purpose(StrEnum):
    OPERATIONAL_REVIEW = "OPERATIONAL_REVIEW"
    DQ_REVIEW = "DQ_REVIEW"
    PATTERN_ANALYSIS = "PATTERN_ANALYSIS"
    BRIEFING = "BRIEFING"
    OUTCOME_REVIEW = "OUTCOME_REVIEW"
    AUDIT = "AUDIT"


@dataclass(frozen=True)
class Grant:
    """How a role holds one permission."""

    scope: Scope
    aggregate_only: bool = False
    reauth: bool = False  # fresh login required at time of use; also requires a declared purpose
    purpose: Purpose | None = None  # only usable under this declared purpose
    note: str | None = None  # narrower use enforced by the feature that uses it (e.g. "DQ fields")


# Roles whose grants are ignored unless the token proves MFA (specification: mandatory MFA for all
# privileged and case-data roles). DEMO_USER sees synthetic aggregates only.
MFA_EXEMPT_ROLES: frozenset[Role] = frozenset({Role.DEMO_USER})

_R = Grant(Scope.REGION)
_S = Grant(Scope.STATION)
_G = Grant(Scope.GLOBAL)
_A = Grant(Scope.GLOBAL, aggregate_only=True)
_R_REAUTH = Grant(Scope.REGION, reauth=True)
_S_REAUTH = Grant(Scope.STATION, reauth=True)

ROLE_PERMISSIONS: dict[Role, dict[str, Grant]] = {
    Role.SYSTEM_ADMIN: {
        "ai.model.read": _G,
        "ai.config": Grant(Scope.GLOBAL, note="kill switch only"),
        "audit.read": Grant(Scope.GLOBAL, note="metadata"),
        "lineage.read": _G,
        "admin.config": _G,
        "admin.users": _G,
    },
    Role.DATA_STEWARD: {
        "map.aggregate.read": _R,
        "map.points.read": _R,
        "incident.location.exact": Grant(Scope.REGION, purpose=Purpose.DQ_REVIEW),
        "incident.summary.read": _R,
        "incident.detail.read": Grant(Scope.REGION, note="DQ fields"),
        "opsrecord.create": _R,
        "opsrecord.approve": Grant(Scope.REGION, note="corrections"),
        "dq.manage": _R,
        "analytics.read": Grant(Scope.REGION, note="DQ section"),
        "ai.insight.read": Grant(Scope.REGION, note="DQ insights"),
        "ai.insight.review": Grant(Scope.REGION, note="DQ insights"),
        "lineage.read": _R,
    },
    Role.CRIME_ANALYST: {
        "map.aggregate.read": _R,
        "map.points.read": _R,
        "incident.location.exact": _R,
        "incident.summary.read": _R,
        "incident.detail.read": _R,
        "incident.sensitive_category.read": _R,
        "opsrecord.create": _R,
        "analytics.read": _R,
        "analytics.compare": _R,
        "ai.insight.read": _R,
        "ai.insight.review": _R,
        "ai.model.read": _R,
        "pattern_review.create": _R,
        "action.propose": _R,
        "outcome.review": _R,
        "report.export.aggregate_csv": _R_REAUTH,
        "report.export.pdf": _R_REAUTH,
        "report.export.map_image": _R_REAUTH,
        "report.read_approved": _R,
    },
    Role.STATION_OFFICER: {
        "map.aggregate.read": _S,
        "map.points.read": _S,
        "incident.summary.read": _S,
        "incident.detail.read": _S,
        "opsrecord.create": _S,
        "action.complete": Grant(Scope.ASSIGNED),
    },
    Role.SHO: {
        "map.aggregate.read": _S,
        "map.points.read": _S,
        "incident.location.exact": _S,
        "incident.summary.read": _S,
        "incident.detail.read": _S,
        "incident.sensitive_category.read": _S,
        "opsrecord.create": _S,
        "opsrecord.approve": _S,
        "analytics.read": _S,
        "analytics.compare": _S,
        "ai.insight.read": _S,
        "pattern_review.create": _S,
        "pattern_review.decide": _S,
        "action.propose": _S,
        "action.approve": _S,
        "action.complete": _S,
        "outcome.review": _S,
        "report.export.aggregate_csv": _S_REAUTH,
        "report.export.pdf": _S_REAUTH,
        "report.export.map_image": _S_REAUTH,
        "report.read_approved": _S,
    },
    Role.DISTRICT_OR_REGION_SUPERVISOR: {
        "map.aggregate.read": _R,
        "map.points.read": _R,
        "incident.location.exact": _R,
        "incident.summary.read": _R,
        "incident.detail.read": _R,
        "incident.sensitive_category.read": _R,
        "opsrecord.create": _R,
        "opsrecord.approve": _R,
        "analytics.read": _R,
        "analytics.compare": _R,
        "ai.insight.read": _R,
        "pattern_review.create": _R,
        "pattern_review.decide": _R,
        "action.propose": _R,
        "action.approve": _R,
        "outcome.review": _R,
        "report.export.aggregate_csv": _R_REAUTH,
        "report.export.pdf": _R_REAUTH,
        "report.export.map_image": _R_REAUTH,
        "report.read_approved": _R,
    },
    Role.SENIOR_LEADERSHIP: {
        "map.aggregate.read": _A,
        "analytics.read": _A,
        "analytics.compare": _A,
        "report.read_approved": _G,
    },
    Role.AUDITOR: {
        "ai.insight.read": _G,
        "ai.model.read": _G,
        "report.read_approved": _G,
        "audit.read": _G,
        "lineage.read": _G,
    },
    Role.DEMO_USER: {
        "map.aggregate.read": _A,
        "analytics.read": _A,
        "analytics.compare": _A,
    },
}

# Every permission code, including ones no role holds by default (restricted.read is granted only
# individually, with purpose and re-authentication, and is out of scope for the demo).
PERMISSIONS: frozenset[str] = frozenset(
    {p for perms in ROLE_PERMISSIONS.values() for p in perms} | {"restricted.read"}
)

# Permissions that read case-level or case-derived data. A GLOBAL grant of any of these switches
# row-level security to all territories (app.scope_global); the API still enforces each
# permission's own scope and aggregate-only rule.
CASE_DATA_PERMISSIONS: frozenset[str] = frozenset(
    {
        "map.aggregate.read",
        "map.points.read",
        "incident.summary.read",
        "incident.detail.read",
        "incident.location.exact",
        "analytics.read",
        "analytics.compare",
    }
)
