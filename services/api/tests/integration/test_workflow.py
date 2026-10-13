"""Phase 6: Review Queue workflow (pattern review decisions, operational actions, outcome reviews).

Acceptance (spec Phase 6): state transitions are authorised and audited; an action cannot be
approved by an unprivileged user; an outcome review cannot be finalised without a baseline and
evidence references. Each rule is checked through the API and, where it matters, in the database
as the application role (ADR-036).
"""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest

from crimemap_api.policy.catalogue import Role

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration

MakeUser = Callable[..., tuple[str, str]]
LOCATION = {
    "region": "PDY",
    "period_from": "2026-04-01",
    "period_to": "2026-06-30",
    "unit": "HEX",
    "hex_size_m": "500",
}


def as_user(sub: str, **kw: Any) -> dict[str, str]:
    return bearer(mint(sub, **kw))


def sql(database: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(database.migrator) as conn:
        return conn.execute(query, list(params)).fetchall()


def app_conn(database: Database, user_id: str) -> psycopg.Connection[Any]:
    region = sql(database, "SELECT id::text FROM core.organizational_unit WHERE code = 'PDY'")[0][0]
    conn = psycopg.connect(database.app)
    conn.execute(
        "SELECT set_config('app.user_id', %s, false), set_config('app.region_ids', %s, false)",
        [user_id, region],
    )
    return conn


async def draft_review(
    api: httpx.AsyncClient, analyst: str, params: dict[str, Any] = LOCATION
) -> dict[str, Any]:
    snap = await api.post(
        "/api/v1/evidence-snapshots",
        json={"section": "location", "params": params},
        headers=as_user(analyst),
    )
    assert snap.status_code == 201, snap.text
    stored = (
        await api.get(f"/api/v1/evidence-snapshots/{snap.json()['id']}", headers=as_user(analyst))
    ).json()
    cells = sorted(stored["data"]["map"]["features"], key=lambda f: -f["properties"]["count"])[:3]
    created = await api.post(
        "/api/v1/pattern-reviews",
        json={
            "review_type": "HOTSPOT",
            "evidence_snapshot_id": snap.json()["id"],
            "cell_keys": [c["properties"]["key"] for c in cells],
            "narrative": "Three cells hold the highest recorded counts in the quarter.",
            "recommended_next_step": "Consider patrol visibility at the evening peak.",
        },
        headers=as_user(analyst),
    )
    assert created.status_code == 201, created.text
    return created.json()  # type: ignore[no-any-return]


async def submit(api: httpx.AsyncClient, sub: str, review_id: str) -> httpx.Response:
    return await api.post(
        f"/api/v1/pattern-reviews/{review_id}/submissions",
        json={"analyst_assessment": "The concentration persisted for three months."},
        headers=as_user(sub),
    )


async def decide(
    api: httpx.AsyncClient, sub: str, review_id: str, decision: str, **headers: str
) -> httpx.Response:
    return await api.post(
        f"/api/v1/pattern-reviews/{review_id}/decisions",
        json={"decision": decision, "rationale": "Evidence and assessment reviewed in full."},
        headers={**as_user(sub), **headers},
    )


async def approved_review(
    api: httpx.AsyncClient, make_user: MakeUser
) -> tuple[dict[str, Any], str]:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    supervisor, _ = make_user(Role.DISTRICT_OR_REGION_SUPERVISOR, ("PDY",))
    review = await draft_review(api, analyst)
    assert (await submit(api, analyst, review["id"])).status_code == 200
    decided = await decide(api, supervisor, review["id"], "APPROVE")
    assert decided.status_code == 200, decided.text
    return decided.json(), analyst


def action_body(review: dict[str, Any], assignee: str | None = None) -> dict[str, Any]:
    return {
        "pattern_review_id": review["id"],
        "action_type": "PATROL_VISIBILITY",
        "description": "Visible patrol in the selected cells between 18:00 and 22:00.",
        "cell_keys": review["cell_keys"],
        "station": "PDY-S01",
        "assignee_id": assignee,
        "start_at": "2026-05-16",
        "end_at": "2026-06-30",
    }


async def test_pattern_review_decisions_are_authorised_audited_and_kept_in_history(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    analyst, analyst_id = make_user(Role.CRIME_ANALYST, ("PDY",))
    supervisor, supervisor_id = make_user(Role.DISTRICT_OR_REGION_SUPERVISOR, ("PDY",))
    other_region, _ = make_user(Role.DISTRICT_OR_REGION_SUPERVISOR, ("KKL",))
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    review = await draft_review(api, analyst)
    rid = review["id"]

    # Nothing to decide before submission; only the author submits.
    assert (await decide(api, supervisor, rid, "APPROVE")).status_code == 409
    assert (await submit(api, supervisor, rid)).status_code == 409
    assert (await submit(api, analyst, rid)).status_code == 200

    assert (await decide(api, analyst, rid, "APPROVE")).status_code == 403  # no decide permission
    assert (await decide(api, officer, rid, "APPROVE")).status_code == 403
    assert (await decide(api, other_region, rid, "APPROVE")).status_code == 404  # out of territory
    personal = await api.post(
        f"/api/v1/pattern-reviews/{rid}/decisions",
        json={"decision": "APPROVE", "rationale": "The suspect lives nearby, approve."},
        headers=as_user(supervisor),
    )
    assert personal.status_code == 422

    request_id = str(uuid.uuid4())
    returned = await decide(api, supervisor, rid, "RETURN", **{"X-Request-ID": request_id})
    assert returned.status_code == 200, returned.text
    assert (returned.json()["analyst_status"], returned.json()["supervisor_status"]) == (
        "DRAFT",
        "RETURNED",
    )
    [event] = audit_rows(request_id)
    assert (event["action"], event["result"], event["actor_user_id"]) == (
        "WORKFLOW_TRANSITION",
        "ALLOW",
        supervisor_id,
    )
    assert (event["before_meta"], event["after_meta"]["state"]) == (
        {"state": "PENDING"},
        "RETURNED",
    )
    assert "rationale" not in event["after_meta"]  # free text stays out of the audit log

    revised = await api.patch(
        f"/api/v1/pattern-reviews/{rid}",
        json={"narrative": "Three cells hold the highest counts; the evening peak is clear."},
        headers=as_user(analyst),
    )
    assert revised.status_code == 200, revised.text
    assert (await submit(api, analyst, rid)).status_code == 200
    approved = await decide(api, supervisor, rid, "APPROVE")
    assert approved.json()["supervisor_status"] == "APPROVED"
    assert approved.json()["decided_by"] is not None
    assert (await decide(api, supervisor, rid, "REJECT")).status_code == 409  # already decided
    assert (
        await api.patch(
            f"/api/v1/pattern-reviews/{rid}",
            json={"narrative": "Changing the text after approval must be refused."},
            headers=as_user(analyst),
        )
    ).status_code == 409

    history = await api.get(
        f"/api/v1/workflow-history?object_type=PATTERN_REVIEW&object_id={rid}",
        headers=as_user(analyst),
    )
    steps = [(h["from_state"], h["to_state"]) for h in history.json()["data"]]
    # Written by the database (migration 0011), including creation and the revision.
    assert steps == [
        (None, "DRAFT"),
        ("DRAFT", "PENDING"),
        ("PENDING", "RETURNED"),
        ("RETURNED", "REVISED"),
        ("RETURNED", "PENDING"),
        ("PENDING", "APPROVED"),
    ]
    assert history.json()["data"][2]["rationale"] == "Evidence and assessment reviewed in full."
    actors = sql(
        ingested.database,
        "SELECT actor_id::text FROM core.workflow_transition WHERE object_id = %s ORDER BY id",
        rid,
    )
    assert [a for (a,) in actors] == [
        analyst_id,
        analyst_id,
        supervisor_id,
        analyst_id,
        analyst_id,
        supervisor_id,
    ]


async def test_an_author_never_decides_their_own_review(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    sho, sho_id = make_user(Role.SHO, stations=("PDY-S01",))  # may create and decide
    review = await draft_review(api, sho, {**LOCATION, "station": ["PDY-S01"]})
    assert (await submit(api, sho, review["id"])).status_code == 200
    request_id = str(uuid.uuid4())
    own = await decide(api, sho, review["id"], "APPROVE", **{"X-Request-ID": request_id})
    assert own.status_code == 409
    assert [(e["result"], e["reason"]) for e in audit_rows(request_id)] == [
        ("DENY", "SEPARATION_OF_DUTIES")
    ]
    # The database refuses it too, as the application role with the author's session.
    with app_conn(ingested.database, sho_id) as conn, pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE core.pattern_review SET supervisor_status = 'APPROVED', "
            "decision_rationale = 'self approval attempt', decided_by = %s WHERE id = %s",
            [sho_id, review["id"]],
        )


async def test_an_action_cannot_be_approved_by_an_unprivileged_user(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    review, analyst = await approved_review(api, make_user)
    analyst_id = sql(
        ingested.database, "SELECT id::text FROM core.app_user WHERE idp_subject = %s", analyst
    )[0][0]
    officer, officer_id = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    other_officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    other_sho, _ = make_user(Role.SHO, stations=("PDY-S02",))
    second_analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))

    # Actions follow an approved review, within its cells, at a station of its region.
    draft = await draft_review(api, analyst)
    assert (
        await api.post("/api/v1/actions", json=action_body(draft), headers=as_user(analyst))
    ).status_code == 409
    outside = {**action_body(review), "cell_keys": ["PDY:500:999:999"]}
    assert (
        await api.post("/api/v1/actions", json=outside, headers=as_user(analyst))
    ).status_code == 422
    assert (
        await api.post(
            "/api/v1/actions",
            json={**action_body(review), "station": "KKL-S01"},
            headers=as_user(analyst),
        )
    ).status_code == 422
    assert (
        await api.post("/api/v1/actions", json=action_body(review), headers=as_user(officer))
    ).status_code == 403  # officers do not propose
    not_an_officer = action_body(review, assignee=analyst_id)
    assert (
        await api.post("/api/v1/actions", json=not_an_officer, headers=as_user(analyst))
    ).status_code == 422

    proposed = await api.post(
        "/api/v1/actions", json=action_body(review, assignee=officer_id), headers=as_user(analyst)
    )
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    aid = action["id"]
    assert (action["approval_status"], action["completion_status"]) == ("PROPOSED", "NOT_STARTED")
    assert action["outcome_review_due_at"] == "2026-07-28"  # 28 days after the end

    approve = {"decision": "APPROVE", "rationale": "Proportionate and within the reviewed cells."}
    for sub, status in ((analyst, 403), (second_analyst, 403), (officer, 403), (other_sho, 404)):
        r = await api.post(f"/api/v1/actions/{aid}/approvals", json=approve, headers=as_user(sub))
        assert r.status_code == status, (sub, r.text)
    # Completion needs approval first.
    assert (
        await api.post(
            f"/api/v1/actions/{aid}/completions",
            json={"evidence": "Patrol logs for the period."},
            headers=as_user(officer),
        )
    ).status_code == 409
    # The database refuses approval by the proposer, or without a rationale.
    with app_conn(ingested.database, analyst_id) as conn, pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE core.operational_action SET approval_status = 'APPROVED', "
            "approval_rationale = 'self approval attempt', approved_by = %s WHERE id = %s",
            [analyst_id, aid],
        )
    with app_conn(ingested.database, officer_id) as conn, pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE core.operational_action SET approval_status = 'APPROVED', approved_by = %s "
            "WHERE id = %s",
            [officer_id, aid],
        )

    queue = await api.get("/api/v1/review-queue?queue=actions", headers=as_user(sho))
    [item] = [i for i in queue.json()["data"] if i["id"] == aid]
    assert (item["status"], item["next_step"]) == ("PROPOSED", "APPROVE")
    # A station-scoped SHO does not see region-wide evidence (row-level security, ADR-036); the
    # region supervisor does.
    assert item["evidence_snapshot_id"] is None
    supervisor, _ = make_user(Role.DISTRICT_OR_REGION_SUPERVISOR, ("PDY",))
    queue = await api.get("/api/v1/review-queue?queue=actions", headers=as_user(supervisor))
    [item] = [i for i in queue.json()["data"] if i["id"] == aid]
    assert item["evidence_snapshot_id"] == review["evidence_snapshot_id"]
    assert item["priority"] == "HIGH"  # the action period has already ended
    request_id = str(uuid.uuid4())
    approved = await api.post(
        f"/api/v1/actions/{aid}/approvals",
        json=approve,
        headers={**as_user(sho), "X-Request-ID": request_id},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "APPROVED"
    assert [(e["action"], e["after_meta"]["state"]) for e in audit_rows(request_id)] == [
        ("WORKFLOW_TRANSITION", "APPROVED")
    ]
    assert (
        await api.post(f"/api/v1/actions/{aid}/approvals", json=approve, headers=as_user(sho))
    ).status_code == 409

    # Only the assigned officer (or the station's SHO) records completion.
    done = {"evidence": "Patrol logs PL-2026-05 to PL-2026-06 filed.", "reference": "PL-2026-06"}
    assert (
        await api.post(
            f"/api/v1/actions/{aid}/completions", json=done, headers=as_user(other_officer)
        )
    ).status_code == 404
    for sub in (analyst, supervisor):  # they see the action but may not complete it
        r = await api.post(f"/api/v1/actions/{aid}/completions", json=done, headers=as_user(sub))
        assert r.status_code == 403, r.text
    officer_queue = await api.get("/api/v1/review-queue?queue=actions", headers=as_user(officer))
    assert [i["next_step"] for i in officer_queue.json()["data"] if i["id"] == aid] == ["COMPLETE"]
    completed = await api.post(
        f"/api/v1/actions/{aid}/completions", json=done, headers=as_user(officer)
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["completion_status"] == "COMPLETED"
    assert (
        await api.post(f"/api/v1/actions/{aid}/completions", json=done, headers=as_user(officer))
    ).status_code == 409
    history = await api.get(
        f"/api/v1/workflow-history?object_type=ACTION&object_id={aid}", headers=as_user(sho)
    )
    assert [h["to_state"] for h in history.json()["data"]] == ["PROPOSED", "APPROVED", "COMPLETED"]

    # An SHO may propose and approve, but never approve their own proposal. (A station-scoped
    # SHO proposes from a review of their station: region-wide reviews are not visible to them.)
    station_review = await draft_review(api, analyst, {**LOCATION, "station": ["PDY-S01"]})
    assert (await submit(api, analyst, station_review["id"])).status_code == 200
    assert (await decide(api, supervisor, station_review["id"], "APPROVE")).status_code == 200
    own = await api.post(
        "/api/v1/actions", json=action_body(station_review, officer_id), headers=as_user(sho)
    )
    assert own.status_code == 201, own.text
    request_id = str(uuid.uuid4())
    self_approval = await api.post(
        f"/api/v1/actions/{own.json()['id']}/approvals",
        json=approve,
        headers={**as_user(sho), "X-Request-ID": request_id},
    )
    assert self_approval.status_code == 409
    assert [(e["result"], e["reason"]) for e in audit_rows(request_id)] == [
        ("DENY", "SEPARATION_OF_DUTIES")
    ]


async def completed_action(
    api: httpx.AsyncClient, make_user: MakeUser
) -> tuple[dict[str, Any], dict[str, Any], str]:
    review, analyst = await approved_review(api, make_user)
    officer, officer_id = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    action = (
        await api.post(
            "/api/v1/actions", json=action_body(review, officer_id), headers=as_user(analyst)
        )
    ).json()
    await api.post(
        f"/api/v1/actions/{action['id']}/approvals",
        json={"decision": "APPROVE", "rationale": "Proportionate and within the reviewed cells."},
        headers=as_user(sho),
    )
    r = await api.post(
        f"/api/v1/actions/{action['id']}/completions",
        json={"evidence": "Patrol logs for the whole period."},
        headers=as_user(officer),
    )
    assert r.status_code == 200, r.text
    return review, r.json(), analyst


OUTCOME = {
    "baseline_start": "2026-04-01",
    "baseline_end": "2026-05-15",
    "after_start": "2026-05-16",
    "after_end": "2026-06-30",
    "comparison_kind": "STATION",
    "comparison_keys": ["PDY-S02"],
}


async def test_an_outcome_review_cannot_be_finalised_without_baseline_and_evidence(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    review, action, analyst = await completed_action(api, make_user)
    other_analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    body = {**OUTCOME, "action_id": action["id"]}
    swapped = {**body, "baseline_end": "2026-05-20"}  # baseline overlaps the after period
    assert (
        await api.post("/api/v1/outcomes", json=swapped, headers=as_user(analyst))
    ).status_code == 422

    queue = await api.get("/api/v1/review-queue?queue=outcomes", headers=as_user(analyst))
    [due] = [i for i in queue.json()["data"] if i["id"] == action["id"]]
    assert (due["status"], due["overdue"], due["priority"], due["due"]) == (
        "DUE",
        True,
        "HIGH",
        "2026-07-28",
    )

    created = await api.post("/api/v1/outcomes", json=body, headers=as_user(analyst))
    assert created.status_code == 201, created.text
    outcome = created.json()
    oid = outcome["id"]
    linked = await api.get(f"/api/v1/actions/{action['id']}", headers=as_user(analyst))
    assert (linked.json()["outcome_id"], linked.json()["outcome_status"]) == (oid, "DRAFT")
    before, after = outcome["before_metrics"], outcome["after_metrics"]
    # The two periods split the review's quarter, so the action-area counts add up to the review's.
    assert before["action_area"] + after["action_area"] == review["record_count"]
    assert before["period"]["days"] == 45
    assert after["period"]["days"] == 46
    assert before["comparison_area"] is not None
    assert before["surrounding_ring"] >= 0
    assert any("not evidence of either" in line for line in outcome["summary"])
    assert (
        await api.post("/api/v1/outcomes", json=body, headers=as_user(analyst))
    ).status_code == 409

    # Finalising without evidence references or a conclusion is refused, and audited.
    request_id = str(uuid.uuid4())
    refused = await api.post(
        f"/api/v1/outcomes/{oid}/finalisations",
        headers={**as_user(analyst), "X-Request-ID": request_id},
    )
    assert refused.status_code == 422
    assert "evidence references" in refused.json()["title"]
    [event] = audit_rows(request_id)
    assert (event["result"], event["reason"]) == ("DENY", "INCOMPLETE")

    unknown = {"evidence_references": [{"kind": "EVIDENCE_SNAPSHOT", "value": str(uuid.uuid4())}]}
    assert (
        await api.patch(f"/api/v1/outcomes/{oid}", json=unknown, headers=as_user(analyst))
    ).status_code == 404
    edit = {
        "evidence_references": [
            {"kind": "EVIDENCE_SNAPSHOT", "value": review["evidence_snapshot_id"]},
            {"kind": "DOCUMENT", "value": "Patrol log register PL-2026-06"},
        ],
        "displacement_diffusion_notes": "The ring next to the cells did not rise in the after period.",
        "reviewer_conclusion": "Recorded counts fell in the action area; no rise next to it.",
    }
    assert (
        await api.patch(f"/api/v1/outcomes/{oid}", json=edit, headers=as_user(other_analyst))
    ).status_code == 409  # only the reviewer
    assert (
        await api.patch(f"/api/v1/outcomes/{oid}", json=edit, headers=as_user(analyst))
    ).status_code == 200
    final = await api.post(f"/api/v1/outcomes/{oid}/finalisations", headers=as_user(analyst))
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "FINAL"
    assert final.json()["finalised_at"]
    assert (
        await api.patch(f"/api/v1/outcomes/{oid}", json=edit, headers=as_user(analyst))
    ).status_code == 409
    queue = await api.get("/api/v1/review-queue?queue=outcomes", headers=as_user(analyst))
    assert action["id"] not in [i["id"] for i in queue.json()["data"]]


async def test_the_database_refuses_a_final_outcome_without_evidence(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    _review, action, analyst = await completed_action(api, make_user)
    created = await api.post(
        "/api/v1/outcomes", json={**OUTCOME, "action_id": action["id"]}, headers=as_user(analyst)
    )
    oid = created.json()["id"]
    with (
        psycopg.connect(ingested.database.migrator) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute(
            "UPDATE core.outcome_review SET status = 'FINAL', "
            "reviewer_conclusion = 'A conclusion long enough to pass.' WHERE id = %s",
            [oid],
        )
    analyst_id = sql(
        ingested.database, "SELECT id::text FROM core.app_user WHERE idp_subject = %s", analyst
    )[0][0]
    with app_conn(ingested.database, analyst_id) as conn, pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE core.outcome_review SET after_metrics = '{}'::jsonb WHERE id = %s", [oid]
        )
    # The trigger itself (not only column privileges) keeps the measured values fixed.
    with (
        psycopg.connect(ingested.database.migrator) as conn,
        pytest.raises(psycopg.errors.CheckViolation, match="measured values"),
    ):
        conn.execute(
            "UPDATE core.outcome_review SET after_metrics = '{}'::jsonb WHERE id = %s", [oid]
        )


async def test_workflow_items_are_invisible_outside_territory(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    review, action, _analyst = await completed_action(api, make_user)
    outsider, _ = make_user(Role.CRIME_ANALYST, ("KKL",))
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    for sub in (outsider, steward):
        for path in (
            f"/api/v1/pattern-reviews/{review['id']}",
            f"/api/v1/actions/{action['id']}",
            f"/api/v1/workflow-history?object_type=ACTION&object_id={action['id']}",
        ):
            assert (await api.get(path, headers=as_user(sub))).status_code == 404, (sub, path)
        for queue in ("patterns", "actions", "outcomes"):
            items = (
                await api.get(f"/api/v1/review-queue?queue={queue}", headers=as_user(sub))
            ).json()
            assert action["id"] not in [i["id"] for i in items["data"]]
            assert review["id"] not in [i["id"] for i in items["data"]]
    summary = (await api.get("/api/v1/review-queue/summary", headers=as_user(steward))).json()
    assert (summary["pattern_reviews"], summary["actions"], summary["outcomes"]) == (
        None,
        None,
        None,
    )
