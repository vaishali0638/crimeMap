"""Behaviour of the deny-by-default policy engine (role + scope + classification + purpose)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from crimemap_api.policy.catalogue import Purpose, Role
from crimemap_api.policy.engine import (
    REAUTH_MAX_AGE,
    DenyReason,
    RoleGrant,
    Subject,
    Target,
    authorize,
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
PDY, KKL = uuid4(), uuid4()
PDY_NORTH, PDY_SOUTH = uuid4(), uuid4()


def subject(
    *grants: RoleGrant,
    mfa: bool = True,
    auth_time: datetime | None = NOW,
    user_id: UUID | None = None,
) -> Subject:
    return Subject(user_id=user_id or uuid4(), grants=grants, mfa=mfa, auth_time=auth_time)


def grant(
    role: Role, regions: tuple[UUID, ...] = (), stations: tuple[UUID, ...] = (), **kw
) -> RoleGrant:  # type: ignore[no-untyped-def]
    return RoleGrant(
        role=role, region_ids=frozenset(regions), station_ids=frozenset(stations), **kw
    )


def test_denies_when_no_role_holds_the_permission() -> None:
    d = authorize(subject(grant(Role.AUDITOR)), "admin.users", now=NOW)
    assert not d.allowed
    assert d.reason is DenyReason.NO_PERMISSION


def test_denies_a_subject_without_any_grant() -> None:
    assert authorize(subject(), "audit.read", now=NOW).reason is DenyReason.NO_PERMISSION


def test_allows_global_permission() -> None:
    d = authorize(subject(grant(Role.SYSTEM_ADMIN)), "admin.users", now=NOW)
    assert d.allowed
    assert d.matched_role is Role.SYSTEM_ADMIN


def test_unknown_permission_is_denied_not_raised() -> None:
    d = authorize(subject(grant(Role.SYSTEM_ADMIN)), "does.not.exist", now=NOW)
    assert d.reason is DenyReason.NO_PERMISSION


def test_region_scope_allows_target_inside_region() -> None:
    s = subject(grant(Role.CRIME_ANALYST, regions=(PDY,)))
    assert authorize(s, "map.points.read", target=Target(region_id=PDY), now=NOW).allowed


def test_region_scope_denies_target_in_another_region() -> None:
    s = subject(grant(Role.CRIME_ANALYST, regions=(PDY,)))
    d = authorize(s, "map.points.read", target=Target(region_id=KKL), now=NOW)
    assert d.reason is DenyReason.OUT_OF_SCOPE


def test_station_scope_allows_own_station() -> None:
    s = subject(grant(Role.STATION_OFFICER, stations=(PDY_NORTH,)))
    t = Target(region_id=PDY, station_id=PDY_NORTH)
    assert authorize(s, "map.points.read", target=t, now=NOW).allowed


def test_station_scope_denies_neighbouring_station() -> None:
    s = subject(grant(Role.STATION_OFFICER, stations=(PDY_NORTH,)))
    t = Target(region_id=PDY, station_id=PDY_SOUTH)
    assert authorize(s, "map.points.read", target=t, now=NOW).reason is DenyReason.OUT_OF_SCOPE


def test_station_scope_denies_region_wide_request() -> None:
    s = subject(grant(Role.STATION_OFFICER, stations=(PDY_NORTH,)))
    d = authorize(s, "map.points.read", target=Target(region_id=PDY), now=NOW)
    assert d.reason is DenyReason.OUT_OF_SCOPE


def test_station_scoped_role_without_stations_has_no_targeted_access() -> None:
    s = subject(grant(Role.SHO))
    t = Target(region_id=PDY, station_id=PDY_NORTH)
    assert authorize(s, "map.points.read", target=t, now=NOW).reason is DenyReason.OUT_OF_SCOPE


def test_purpose_bound_permission_requires_a_purpose() -> None:
    s = subject(grant(Role.DATA_STEWARD, regions=(PDY,)))
    d = authorize(s, "incident.location.exact", target=Target(region_id=PDY), now=NOW)
    assert d.reason is DenyReason.PURPOSE_REQUIRED


def test_purpose_bound_permission_rejects_other_purposes() -> None:
    s = subject(grant(Role.DATA_STEWARD, regions=(PDY,)))
    d = authorize(
        s,
        "incident.location.exact",
        target=Target(region_id=PDY),
        purpose=Purpose.BRIEFING,
        now=NOW,
    )
    assert d.reason is DenyReason.PURPOSE_NOT_PERMITTED


def test_purpose_bound_permission_allows_declared_purpose() -> None:
    s = subject(grant(Role.DATA_STEWARD, regions=(PDY,)))
    d = authorize(
        s,
        "incident.location.exact",
        target=Target(region_id=PDY),
        purpose=Purpose.DQ_REVIEW,
        now=NOW,
    )
    assert d.allowed


def test_reauth_permission_denied_when_login_is_old() -> None:
    s = subject(
        grant(Role.CRIME_ANALYST, regions=(PDY,)),
        auth_time=NOW - REAUTH_MAX_AGE - timedelta(seconds=1),
    )
    d = authorize(
        s, "report.export.pdf", target=Target(region_id=PDY), purpose=Purpose.BRIEFING, now=NOW
    )
    assert d.reason is DenyReason.REAUTH_REQUIRED


def test_reauth_permission_allowed_after_fresh_login() -> None:
    s = subject(grant(Role.CRIME_ANALYST, regions=(PDY,)), auth_time=NOW - timedelta(seconds=30))
    d = authorize(
        s, "report.export.pdf", target=Target(region_id=PDY), purpose=Purpose.BRIEFING, now=NOW
    )
    assert d.allowed


def test_reauth_permission_requires_a_purpose() -> None:
    s = subject(grant(Role.CRIME_ANALYST, regions=(PDY,)))
    d = authorize(s, "report.export.pdf", target=Target(region_id=PDY), now=NOW)
    assert d.reason is DenyReason.PURPOSE_REQUIRED


def test_case_data_role_without_mfa_is_denied() -> None:
    s = subject(grant(Role.CRIME_ANALYST, regions=(PDY,)), mfa=False)
    d = authorize(s, "map.points.read", target=Target(region_id=PDY), now=NOW)
    assert d.reason is DenyReason.MFA_REQUIRED


def test_demo_user_does_not_need_mfa() -> None:
    s = subject(grant(Role.DEMO_USER), mfa=False)
    assert authorize(s, "map.aggregate.read", now=NOW).allowed


def test_expired_grant_is_ignored() -> None:
    s = subject(grant(Role.SYSTEM_ADMIN, valid_to=NOW - timedelta(days=1)))
    assert authorize(s, "admin.users", now=NOW).reason is DenyReason.NO_PERMISSION


def test_future_grant_is_ignored() -> None:
    s = subject(grant(Role.SYSTEM_ADMIN, valid_from=NOW + timedelta(days=1)))
    assert authorize(s, "admin.users", now=NOW).reason is DenyReason.NO_PERMISSION


def test_any_matching_grant_allows() -> None:
    s = subject(
        grant(Role.CRIME_ANALYST, regions=(KKL,)),
        grant(Role.CRIME_ANALYST, regions=(PDY,)),
    )
    assert authorize(s, "map.points.read", target=Target(region_id=PDY), now=NOW).allowed


def test_assigned_scope_requires_the_subject_to_be_the_assignee() -> None:
    me = uuid4()
    s = subject(grant(Role.STATION_OFFICER, stations=(PDY_NORTH,)), user_id=me)
    mine = Target(region_id=PDY, station_id=PDY_NORTH, assignee_id=me)
    theirs = Target(region_id=PDY, station_id=PDY_NORTH, assignee_id=uuid4())
    assert authorize(s, "action.complete", target=mine, now=NOW).allowed
    assert authorize(s, "action.complete", target=theirs, now=NOW).reason is DenyReason.OUT_OF_SCOPE


def test_restricted_read_is_denied_to_every_role() -> None:
    s = subject(*(grant(r, regions=(PDY,), stations=(PDY_NORTH,)) for r in Role))
    d = authorize(s, "restricted.read", target=Target(region_id=PDY, station_id=PDY_NORTH), now=NOW)
    assert d.reason is DenyReason.NO_PERMISSION


# --- permission_scope: the territory a caller holds *for one permission* (list filtering) -------


def test_scope_for_a_permission_ignores_territory_held_through_other_roles() -> None:
    from crimemap_api.policy.engine import permission_scope

    s = subject(grant(Role.DATA_STEWARD, (KKL,)), grant(Role.CRIME_ANALYST, (PDY,)))
    scope = permission_scope(s, "dq.manage", now=NOW)
    assert scope.region_ids == frozenset({KKL})
    assert not scope.is_global
    assert scope.any


def test_scope_is_empty_without_the_permission_or_without_mfa() -> None:
    from crimemap_api.policy.engine import permission_scope

    assert not permission_scope(subject(grant(Role.AUDITOR)), "dq.manage", now=NOW).any
    no_mfa = subject(grant(Role.DATA_STEWARD, (KKL,)), mfa=False)
    assert not permission_scope(no_mfa, "dq.manage", now=NOW).any


def test_scope_is_global_for_a_global_grant_and_ignores_expired_grants() -> None:
    from crimemap_api.policy.engine import permission_scope

    assert permission_scope(subject(grant(Role.AUDITOR)), "lineage.read", now=NOW).is_global
    expired = grant(Role.DATA_STEWARD, (KKL,), valid_to=NOW - timedelta(seconds=1))
    assert not permission_scope(subject(expired), "dq.manage", now=NOW).any


def test_scope_collects_stations_for_station_scoped_grants() -> None:
    from crimemap_api.policy.engine import permission_scope

    s = subject(grant(Role.SHO, stations=(PDY_NORTH,)))
    scope = permission_scope(s, "incident.summary.read", now=NOW)
    assert scope.station_ids == frozenset({PDY_NORTH})
    assert scope.region_ids == frozenset()


def test_territorial_grant_without_territory_confers_nothing_even_without_a_target() -> None:
    """Phase 2 audit F4 (Phase 1 B17): an empty-scope grant used to pass every route-level check."""
    empty = subject(grant(Role.DATA_STEWARD))
    decision = authorize(empty, "dq.manage", now=NOW)
    assert not decision.allowed
    assert decision.reason is DenyReason.OUT_OF_SCOPE
    assert authorize(subject(grant(Role.DATA_STEWARD, (KKL,))), "dq.manage", now=NOW).allowed
    assert authorize(subject(grant(Role.AUDITOR)), "audit.read", now=NOW).allowed


def test_scope_can_require_the_grant_conditions_such_as_purpose() -> None:
    """The steward's exact-location grant is usable only under DQ_REVIEW (ADR-020)."""
    from crimemap_api.policy.engine import permission_scope

    s = subject(grant(Role.DATA_STEWARD, (PDY,)))
    assert permission_scope(s, "incident.location.exact", now=NOW).region_ids == frozenset({PDY})
    assert not permission_scope(s, "incident.location.exact", now=NOW, conditions=True).any
    with_purpose = permission_scope(
        s, "incident.location.exact", now=NOW, conditions=True, purpose=Purpose.DQ_REVIEW
    )
    assert with_purpose.region_ids == frozenset({PDY})
    analyst = subject(grant(Role.CRIME_ANALYST, (KKL,)))
    assert permission_scope(analyst, "incident.location.exact", now=NOW, conditions=True).any
