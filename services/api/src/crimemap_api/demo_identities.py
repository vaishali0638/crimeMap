"""Fictional demo identities — one per role — used only in DEMO/SANDBOX.

The ``idp_subject`` values are fixed so that the Keycloak realm import
(infrastructure/docker/keycloak/crimemap-demo-realm.json) and the CRIMEMAP grants line up; a test
checks the two stay identical. Passwords are never stored here: they are generated at bootstrap
into the git-ignored ``secrets/`` directory.

Station-scoped roles (STATION_OFFICER, SHO) are scoped to a fictional station once boundaries are
seeded (Phase 2); without stations their grants give no case-data access (deny by default).
"""

from dataclasses import dataclass

from crimemap_api.policy.catalogue import Role


@dataclass(frozen=True)
class DemoIdentity:
    username: str
    idp_subject: str
    display_name: str
    role: Role
    region_codes: tuple[str, ...] = ()
    station_codes: tuple[str, ...] = ()


DEMO_IDENTITIES: tuple[DemoIdentity, ...] = (
    DemoIdentity(
        "demo.admin", "222982e5-bfc9-47f7-bb6b-cf4e7f3f187e", "Demo System Admin", Role.SYSTEM_ADMIN
    ),
    DemoIdentity(
        "demo.steward",
        "50101935-0677-4141-87dd-dab821a52345",
        "Demo Data Steward",
        Role.DATA_STEWARD,
        ("PDY",),
    ),
    DemoIdentity(
        "demo.analyst",
        "fa44f4b2-f0fc-4f40-b6a7-3a2bbd893403",
        "Demo Crime Analyst",
        Role.CRIME_ANALYST,
        ("PDY", "KKL"),
    ),
    DemoIdentity(
        "demo.officer",
        "4ae94043-caf1-44ed-a3ef-f9f8fba4002d",
        "Demo Station Officer",
        Role.STATION_OFFICER,
        station_codes=("PDY-S01",),
    ),
    DemoIdentity(
        "demo.sho",
        "d851dcb4-1dd9-4385-bfba-9bb5b27d83f4",
        "Demo Station House Officer",
        Role.SHO,
        station_codes=("PDY-S01",),
    ),
    DemoIdentity(
        "demo.supervisor",
        "12c7d5af-3970-47ab-a9e9-96b239039736",
        "Demo Region Supervisor",
        Role.DISTRICT_OR_REGION_SUPERVISOR,
        ("KKL",),
    ),
    DemoIdentity(
        "demo.leadership",
        "ff3a9ae3-2cf7-4462-a59f-757ee776f358",
        "Demo Senior Leadership",
        Role.SENIOR_LEADERSHIP,
    ),
    DemoIdentity(
        "demo.auditor", "a194fc9f-76ef-4402-a7fd-c7f443b13b96", "Demo Auditor", Role.AUDITOR
    ),
    DemoIdentity(
        "demo.viewer", "61801396-4ecb-4197-b90a-e8686a3406a5", "Demo Viewer", Role.DEMO_USER
    ),
)
