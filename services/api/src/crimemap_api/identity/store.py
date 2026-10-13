"""Identity persistence: users, role grants, region lookup. All SQL is parameterised."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.identity.tokens import TokenClaims
from crimemap_api.policy.catalogue import Role
from crimemap_api.policy.engine import RoleGrant


@dataclass(frozen=True)
class UserRow:
    id: UUID
    idp_subject: str
    username: str | None
    name: str | None
    email: str | None
    status: str
    mfa_status: str
    last_login_at: datetime | None
    is_demo: bool


@dataclass(frozen=True)
class GrantRow:
    id: UUID
    user_id: UUID
    role: str
    region_codes: tuple[str, ...]
    station_ids: tuple[UUID, ...]
    valid_from: datetime
    valid_to: datetime | None
    grant_reason: str
    granted_by: UUID | None
    revoked_at: datetime | None


def _user(row: Any) -> UserRow:
    return UserRow(*row)


async def provision_user(
    session: AsyncSession, claims: TokenClaims, *, environment: str
) -> UserRow:
    """Return the user for a verified token, creating it (with no grants) on first sight.

    New users are flagged ``is_demo`` outside PRODUCTION only (found in Phase 2: the flag was
    always true, which would have marked every real production user as a demo identity)."""
    await session.execute(
        text(
            "INSERT INTO core.app_user (idp_subject, username, name, email, is_demo) "
            "VALUES (:sub, :username, :name, :email, :is_demo) ON CONFLICT (idp_subject) DO NOTHING"
        ),
        {
            "sub": claims.subject,
            "username": claims.username,
            "name": claims.name,
            "email": claims.email,
            "is_demo": environment != "PRODUCTION",
        },
    )
    row = (
        await session.execute(
            text(
                "SELECT id, idp_subject, username, name, email, status, mfa_status, last_login_at, is_demo "
                "FROM core.app_user WHERE idp_subject = :sub"
            ),
            {"sub": claims.subject},
        )
    ).one()
    return _user(row)


async def load_role_grants(session: AsyncSession, user_id: UUID) -> tuple[RoleGrant, ...]:
    """Grants active *now according to the database clock*.

    The database is the single time authority for grant validity: grants are written with the
    database's ``now()``, and comparing them with the API host's clock made new grants briefly
    unusable whenever the database clock ran ahead (found in Phase 1). ``valid_to`` is still passed
    to the policy engine so a grant that expires during a long request is not used.
    """
    rows = await session.execute(
        text(
            "SELECT role_code, scope_region_ids, scope_station_ids, valid_to "
            "FROM core.user_role_grant WHERE user_id = :uid AND revoked_at IS NULL "
            "AND valid_from <= now() AND (valid_to IS NULL OR valid_to > now())"
        ),
        {"uid": user_id},
    )
    return tuple(
        RoleGrant(
            role=Role(r.role_code),
            region_ids=frozenset(r.scope_region_ids or ()),
            station_ids=frozenset(r.scope_station_ids or ()),
            valid_to=r.valid_to,
        )
        for r in rows
    )


async def record_login(session: AsyncSession, user_id: UUID, *, mfa: bool) -> None:
    await session.execute(
        text(
            "UPDATE core.app_user SET last_login_at = now(), updated_at = now(), "
            "mfa_status = CASE WHEN :mfa THEN 'ENROLLED' ELSE mfa_status END WHERE id = :uid"
        ),
        {"uid": user_id, "mfa": mfa},
    )


async def get_user(session: AsyncSession, user_id: UUID) -> UserRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, idp_subject, username, name, email, status, mfa_status, last_login_at, is_demo "
                "FROM core.app_user WHERE id = :uid"
            ),
            {"uid": user_id},
        )
    ).one_or_none()
    return _user(row) if row else None


async def list_users(session: AsyncSession, *, after: UUID | None, limit: int) -> list[UserRow]:
    """Keyset pagination on id (stable order, no OFFSET)."""
    rows = await session.execute(
        text(
            "SELECT id, idp_subject, username, name, email, status, mfa_status, last_login_at, is_demo "
            "FROM core.app_user "
            "WHERE (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid)) ORDER BY id LIMIT :limit"
        ),
        {"after": after, "limit": limit},
    )
    return [_user(r) for r in rows]


async def region_codes_by_id(session: AsyncSession) -> dict[UUID, str]:
    rows = await session.execute(
        text("SELECT id, code FROM core.organizational_unit WHERE unit_type = 'REGION'")
    )
    return {r.id: r.code for r in rows}


async def region_ids_for_codes(session: AsyncSession, codes: list[str]) -> dict[str, UUID]:
    rows = await session.execute(
        text(
            "SELECT code, id FROM core.organizational_unit "
            "WHERE unit_type = 'REGION' AND code = ANY(:codes)"
        ),
        {"codes": codes},
    )
    return {r.code: r.id for r in rows}


def _grant(r: Any) -> GrantRow:
    return GrantRow(
        id=r.id,
        user_id=r.user_id,
        role=r.role_code,
        region_codes=tuple(r.region_codes),
        station_ids=tuple(r.scope_station_ids or ()),
        valid_from=r.valid_from,
        valid_to=r.valid_to,
        grant_reason=r.grant_reason,
        granted_by=r.granted_by,
        revoked_at=r.revoked_at,
    )


async def list_grants(session: AsyncSession, user_id: UUID) -> list[GrantRow]:
    rows = await session.execute(
        text(
            """
SELECT g.id, g.user_id, g.role_code,
       coalesce((SELECT array_agg(o.code ORDER BY o.code) FROM core.organizational_unit o
                 WHERE o.id = ANY(g.scope_region_ids)), '{}') AS region_codes,
       g.scope_station_ids, g.valid_from, g.valid_to, g.grant_reason, g.granted_by, g.revoked_at
FROM core.user_role_grant g
WHERE g.user_id = :uid ORDER BY g.created_at, g.id
"""
        ),
        {"uid": user_id},
    )
    return [_grant(r) for r in rows]


async def get_grant(session: AsyncSession, user_id: UUID, grant_id: UUID) -> GrantRow | None:
    row = (
        await session.execute(
            text(
                """
SELECT g.id, g.user_id, g.role_code,
       coalesce((SELECT array_agg(o.code ORDER BY o.code) FROM core.organizational_unit o
                 WHERE o.id = ANY(g.scope_region_ids)), '{}') AS region_codes,
       g.scope_station_ids, g.valid_from, g.valid_to, g.grant_reason, g.granted_by, g.revoked_at
FROM core.user_role_grant g
WHERE g.user_id = :uid AND g.id = :gid
"""
            ),
            {"uid": user_id, "gid": grant_id},
        )
    ).one_or_none()
    return _grant(row) if row else None


async def create_grant(
    session: AsyncSession,
    *,
    user_id: UUID,
    role: Role,
    region_ids: list[UUID],
    valid_to: datetime | None,
    reason: str,
    granted_by: UUID,
) -> GrantRow:
    gid = (
        await session.execute(
            text(
                "INSERT INTO core.user_role_grant "
                "(user_id, role_code, scope_region_ids, valid_to, grant_reason, granted_by) "
                "VALUES (:uid, :role, :regions, :valid_to, :reason, :by) RETURNING id"
            ),
            {
                "uid": user_id,
                "role": role.value,
                "regions": region_ids,
                "valid_to": valid_to,
                "reason": reason,
                "by": granted_by,
            },
        )
    ).scalar_one()
    grant = await get_grant(session, user_id, gid)
    if grant is None:
        raise RuntimeError("created grant not found")
    return grant


async def revoke_grant(session: AsyncSession, grant_id: UUID, *, revoked_by: UUID) -> None:
    await session.execute(
        text(
            "UPDATE core.user_role_grant SET revoked_at = now(), revoked_by = :by, updated_at = now() "
            "WHERE id = :gid AND revoked_at IS NULL"
        ),
        {"gid": grant_id, "by": revoked_by},
    )
