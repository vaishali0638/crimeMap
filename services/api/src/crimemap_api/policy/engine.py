"""Deny-by-default policy engine: role + territorial scope + MFA + purpose + fresh authentication.

``authorize`` is a pure function. Callers (the FastAPI dependency layer) audit every decision; the
database applies row-level security as a second, independent layer (ADR-002).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from crimemap_api.policy.catalogue import (
    MFA_EXEMPT_ROLES,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    Grant,
    Purpose,
    Role,
    Scope,
)

# A permission marked "re-authentication required" may only be used this soon after login.
REAUTH_MAX_AGE = timedelta(minutes=5)


class DenyReason(StrEnum):
    NO_PERMISSION = "NO_PERMISSION"
    MFA_REQUIRED = "MFA_REQUIRED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    PURPOSE_REQUIRED = "PURPOSE_REQUIRED"
    PURPOSE_NOT_PERMITTED = "PURPOSE_NOT_PERMITTED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"


@dataclass(frozen=True)
class RoleGrant:
    """One role granted to a user, limited to a territory and a validity window."""

    role: Role
    region_ids: frozenset[UUID] = frozenset()
    station_ids: frozenset[UUID] = frozenset()
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        if self.valid_from is not None and now < self.valid_from:
            return False
        return not (self.valid_to is not None and now >= self.valid_to)


@dataclass(frozen=True)
class Subject:
    """The authenticated caller, as established from a verified token and stored grants."""

    user_id: UUID
    grants: tuple[RoleGrant, ...]
    mfa: bool
    auth_time: datetime | None


@dataclass(frozen=True)
class Target:
    """Where the requested data lives. ``None`` fields mean "not territory-specific"."""

    region_id: UUID | None = None
    station_id: UUID | None = None
    assignee_id: UUID | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: DenyReason | None = None
    matched_role: Role | None = None
    grant: Grant | None = None


def _in_scope(grant: Grant, role_grant: RoleGrant, subject: Subject, target: Target | None) -> bool:
    if grant.scope is Scope.GLOBAL:
        return True
    if target is None:
        # Not territory-specific (e.g. a route-level check or a source-wide issue): a territorial
        # grant still counts only if it covers some territory. A grant with an empty scope confers
        # nothing (Phase 2 audit F4 / Phase 1 finding B17).
        if grant.scope is Scope.REGION:
            return bool(role_grant.region_ids)
        return bool(role_grant.station_ids)
    if grant.scope is Scope.REGION:
        return target.region_id is not None and target.region_id in role_grant.region_ids
    station_ok = target.station_id is not None and target.station_id in role_grant.station_ids
    if grant.scope is Scope.STATION:
        return station_ok
    # Scope.ASSIGNED: own station and personally assigned.
    return station_ok and target.assignee_id == subject.user_id


def _conditions(
    grant: Grant, subject: Subject, purpose: Purpose | None, now: datetime
) -> DenyReason | None:
    if grant.purpose is not None:
        if purpose is None:
            return DenyReason.PURPOSE_REQUIRED
        if purpose is not grant.purpose:
            return DenyReason.PURPOSE_NOT_PERMITTED
    if grant.reauth:
        if purpose is None:
            return DenyReason.PURPOSE_REQUIRED
        if subject.auth_time is None or now - subject.auth_time > REAUTH_MAX_AGE:
            return DenyReason.REAUTH_REQUIRED
    return None


def authorize(
    subject: Subject,
    permission: str,
    *,
    target: Target | None = None,
    purpose: Purpose | None = None,
    now: datetime | None = None,
) -> Decision:
    """Return whether ``subject`` may use ``permission`` on ``target`` for ``purpose``.

    Checks run in a fixed order and the reason reports the first check that no grant could pass:
    permission held → MFA → territory → purpose / fresh authentication.
    """
    now = now or datetime.now(UTC)
    if permission not in PERMISSIONS:
        return Decision(False, DenyReason.NO_PERMISSION)

    held = [
        (grant, rg)
        for rg in subject.grants
        if rg.is_active(now) and (grant := ROLE_PERMISSIONS[rg.role].get(permission)) is not None
    ]
    if not held:
        return Decision(False, DenyReason.NO_PERMISSION)

    with_mfa = [(g, rg) for g, rg in held if subject.mfa or rg.role in MFA_EXEMPT_ROLES]
    if not with_mfa:
        return Decision(False, DenyReason.MFA_REQUIRED)

    in_scope = [(g, rg) for g, rg in with_mfa if _in_scope(g, rg, subject, target)]
    if not in_scope:
        return Decision(False, DenyReason.OUT_OF_SCOPE)

    first_failure: DenyReason | None = None
    for grant, rg in in_scope:
        failure = _conditions(grant, subject, purpose, now)
        if failure is None:
            return Decision(True, matched_role=rg.role, grant=grant)
        first_failure = first_failure or failure
    return Decision(False, first_failure)


@dataclass(frozen=True)
class PermissionScope:
    """The territory in which a subject may use one permission (for filtering lists).

    Row-level security filters by the union of all the caller's grants; this is narrower: only
    grants that confer *this* permission count, so a steward for one region who is an analyst for
    another does not see the second region's data-quality queue.
    """

    is_global: bool = False
    region_ids: frozenset[UUID] = frozenset()
    station_ids: frozenset[UUID] = frozenset()

    @property
    def any(self) -> bool:
        return self.is_global or bool(self.region_ids) or bool(self.station_ids)


def permission_scope(
    subject: Subject,
    permission: str,
    *,
    now: datetime | None = None,
    conditions: bool = False,
    purpose: Purpose | None = None,
) -> PermissionScope:
    """With ``conditions=True`` only grants whose purpose / fresh-login conditions are met (for the
    given ``purpose``) count, e.g. the steward's exact-location grant only under DQ_REVIEW."""
    now = now or datetime.now(UTC)
    is_global, regions, stations = False, set[UUID](), set[UUID]()
    for rg in subject.grants:
        grant = ROLE_PERMISSIONS[rg.role].get(permission)
        if grant is None or not rg.is_active(now):
            continue
        if not (subject.mfa or rg.role in MFA_EXEMPT_ROLES):
            continue
        if conditions and _conditions(grant, subject, purpose, now) is not None:
            continue
        if grant.scope is Scope.GLOBAL:
            is_global = True
        elif grant.scope is Scope.REGION:
            regions |= rg.region_ids
        else:
            stations |= rg.station_ids
    return PermissionScope(is_global, frozenset(regions), frozenset(stations))
