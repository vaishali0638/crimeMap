"""Phase 6 audit: regression tests for each defect the audit proved (docs/phase-6-audit.md)."""

import asyncio
import uuid
from typing import Any

import httpx
import psycopg
import pytest

from crimemap_api.policy.catalogue import Role

from .conftest import Ingested
from .test_workflow import (
    LOCATION,
    OUTCOME,
    MakeUser,
    action_body,
    app_conn,
    approved_review,
    as_user,
    completed_action,
    decide,
    draft_review,
    sql,
    submit,
)

pytestmark = pytest.mark.integration


def uid(ingested: Ingested, sub: str) -> str:
    return sql(ingested.database, "SELECT id::text FROM core.app_user WHERE idp_subject = %s", sub)[
        0
    ][0]


async def approved(
    api: httpx.AsyncClient, make_user: MakeUser, params: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    supervisor, _ = make_user(Role.DISTRICT_OR_REGION_SUPERVISOR, ("PDY",))
    review = await draft_review(api, analyst, params)
    assert (await submit(api, analyst, review["id"])).status_code == 200
    r = await decide(api, supervisor, review["id"], "APPROVE")
    assert r.status_code == 200, r.text
    return r.json(), analyst


async def test_a1_sensitive_category_is_not_shown_without_the_evidence_access(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """A station officer saw a sensitive category label (and snapshot id) on an assigned action."""
    params = {**LOCATION, "station": ["PDY-S01"], "category": ["CRIMES_AGAINST_WOMEN"]}
    review, analyst = await approved(api, make_user, params)
    officer, officer_id = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    created = await api.post(
        "/api/v1/actions", json=action_body(review, officer_id), headers=as_user(analyst)
    )
    assert created.status_code == 201, created.text
    assert created.json()["category"] == "CRIMES_AGAINST_WOMEN"  # the analyst has the access
    aid = created.json()["id"]
    seen = (await api.get(f"/api/v1/actions/{aid}", headers=as_user(officer))).json()
    assert (seen["category"], seen["evidence_snapshot_id"]) == (None, None)
    assert seen["pattern_review_visible"] is False


async def test_a2_outcome_numbers_need_the_access_that_produced_them(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """An SHO of one station read comparison counts for another station, computed unmasked."""
    _review, action, analyst = await completed_action(api, make_user)
    created = await api.post(
        "/api/v1/outcomes", json={**OUTCOME, "action_id": action["id"]}, headers=as_user(analyst)
    )
    assert created.status_code == 201, created.text
    assert "_coverage" not in created.json()["before_metrics"]
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    seen = (await api.get(f"/api/v1/outcomes/{created.json()['id']}", headers=as_user(sho))).json()
    assert seen["metrics_visible"] is False
    assert (seen["before_metrics"], seen["after_metrics"], seen["summary"]) == ({}, {}, [])
    peer, _ = make_user(Role.CRIME_ANALYST, ("PDY",))  # same access as the reviewer
    assert (
        await api.get(f"/api/v1/outcomes/{created.json()['id']}", headers=as_user(peer))
    ).json()["metrics_visible"]


async def test_a3_a4_comparison_keys_are_validated(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    _review, action, analyst = await completed_action(api, make_user)
    base = {**OUTCOME, "action_id": action["id"]}
    bad = [
        {"comparison_kind": "NONE", "comparison_keys": ["Suspect, phone 9876543210"]},
        {"comparison_kind": "CELLS", "comparison_keys": ["KKL:500:1:1"]},  # another region
        {"comparison_kind": "CELLS", "comparison_keys": ["PDY:500:1:1", "PDY:500:1:1"]},
        {
            "comparison_kind": "CELLS",
            "comparison_keys": action["cell_keys"][:1],
        },  # the action's own
        {"comparison_kind": "STATION", "comparison_keys": ["KKL-S01"]},
        {"comparison_kind": "STATION", "comparison_keys": ["PDY-S01"]},  # the action's station
    ]
    for change in bad:
        r = await api.post("/api/v1/outcomes", json={**base, **change}, headers=as_user(analyst))
        assert r.status_code == 422, (change, r.text)
    with (
        psycopg.connect(ingested.database.migrator) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute(
            "INSERT INTO core.outcome_review (action_id, selected_measures, baseline_start, baseline_end, "
            "after_start, after_end, comparison_kind, comparison_keys, before_metrics, after_metrics, region_id, "
            "created_by) SELECT id, '{x}', '2026-01-01', '2026-01-31', '2026-05-16', '2026-06-30', 'NONE', "
            "'{free text}', '{}', '{}', region_id, proposed_by FROM core.operational_action WHERE id = %s",
            [action["id"]],
        )


async def test_a5_a6_triggers_freeze_decisions_and_approvals(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, analyst_id = make_user(Role.CRIME_ANALYST, ("PDY",))
    supervisor, supervisor_id = make_user(Role.DISTRICT_OR_REGION_SUPERVISOR, ("PDY",))
    review = await draft_review(api, analyst)
    await submit(api, analyst, review["id"])
    assert (await decide(api, supervisor, review["id"], "RETURN")).status_code == 200
    for change in (
        "decision_rationale = 'Supervisor says: looks perfect.'",
        f"decided_by = '{analyst_id}'",
        "submitted_at = now() - interval '30 days'",
    ):
        with app_conn(ingested.database, analyst_id) as conn, pytest.raises(psycopg.Error):
            conn.execute(f"UPDATE core.pattern_review SET {change} WHERE id = %s", [review["id"]])

    _review, action, _ = await completed_action(api, make_user)
    completer = sql(
        ingested.database,
        "SELECT completed_by::text FROM core.operational_action WHERE id = %s",
        action["id"],
    )[0][0]
    del supervisor_id
    # A completed action's approval cannot be rewritten (reset to test the completion branch).
    rewrite = (
        "UPDATE core.operational_action SET completion_status = 'COMPLETED', completed_by = %s, "
        "completion_evidence = 'Patrol logs filed', approval_rationale = 'Rewritten approval rationale' "
        "WHERE id = %s"
    )
    review2, analyst2 = await approved_review(api, make_user)
    _officer, officer_id = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    fresh = (
        await api.post(
            "/api/v1/actions", json=action_body(review2, officer_id), headers=as_user(analyst2)
        )
    ).json()
    await api.post(
        f"/api/v1/actions/{fresh['id']}/approvals",
        json={"decision": "APPROVE", "rationale": "Proportionate and within the reviewed cells."},
        headers=as_user(sho),
    )
    with app_conn(ingested.database, officer_id) as conn, pytest.raises(psycopg.Error):
        conn.execute(rewrite, [officer_id, fresh["id"]])
    # Approval cannot pre-fill completion fields.
    review3, analyst3 = await approved_review(api, make_user)
    third = (
        await api.post(
            "/api/v1/actions", json=action_body(review3, officer_id), headers=as_user(analyst3)
        )
    ).json()
    sho_id = uid(ingested, sho)
    with app_conn(ingested.database, sho_id) as conn, pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE core.operational_action SET approval_status = 'APPROVED', approved_by = %s, "
            "approval_rationale = 'Approved with notes', completion_evidence = 'pre-filled' WHERE id = %s",
            [sho_id, third["id"]],
        )
    assert completer  # the completed action from the helper is untouched


async def test_a7_history_cannot_be_written_by_the_application_role(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    review, analyst = await approved_review(api, make_user)
    officer, officer_id = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    del officer
    region = sql(
        ingested.database, "SELECT id::text FROM core.organizational_unit WHERE code = 'PDY'"
    )[0][0]
    with (
        app_conn(ingested.database, officer_id) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(
            "INSERT INTO core.workflow_transition (object_type, object_id, from_state, to_state, rationale, "
            "region_id, actor_id) VALUES ('PATTERN_REVIEW', %s, 'APPROVED', 'REJECTED', 'forged', %s, %s)",
            [review["id"], region, officer_id],
        )
    del analyst


async def test_a8_concurrent_outcome_creation_is_409_not_500(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    _review, action, analyst = await completed_action(api, make_user)
    body = {**OUTCOME, "action_id": action["id"]}
    results = await asyncio.gather(
        *[api.post("/api/v1/outcomes", json=body, headers=as_user(analyst)) for _ in range(4)]
    )
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409]


async def test_a9_queues_select_actionable_items_and_report_more(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Old completed actions filled the 500-row scan and hid new proposals. Uses region MAH so the
    bulk rows (which cannot be deleted) do not reach other tests."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("MAH",))
    supervisor, _ = make_user(Role.DISTRICT_OR_REGION_SUPERVISOR, ("MAH",))
    officer, officer_id = make_user(Role.STATION_OFFICER, stations=("MAH-S01",))
    sho, _ = make_user(Role.SHO, stations=("MAH-S01",))
    review = await draft_review(api, analyst, {**LOCATION, "region": "MAH"})
    await submit(api, analyst, review["id"])
    review = (await decide(api, supervisor, review["id"], "APPROVE")).json()
    body = {**action_body(review, officer_id), "station": "MAH-S01"}
    first = (await api.post("/api/v1/actions", json=body, headers=as_user(analyst))).json()
    approve = {"decision": "APPROVE", "rationale": "Proportionate and within the reviewed cells."}
    await api.post(f"/api/v1/actions/{first['id']}/approvals", json=approve, headers=as_user(sho))
    done = await api.post(
        f"/api/v1/actions/{first['id']}/completions",
        json={"evidence": "Patrol logs for the period."},
        headers=as_user(officer),
    )
    assert done.status_code == 200, done.text
    # (Inserted proposed, then moved to completed: new actions always start proposed, even here.)
    with psycopg.connect(ingested.database.migrator) as conn:
        conn.execute(
            "INSERT INTO core.operational_action (pattern_review_id, action_type, description, grid_cell_ids, "
            "region_id, station_id, assignee_id, start_at, end_at, proposed_by, outcome_review_due_at) "
            "SELECT pattern_review_id, action_type, 'Old completed action for the queue limit test', "
            "grid_cell_ids, region_id, station_id, assignee_id, DATE '2020-01-01', DATE '2020-01-31', "
            "proposed_by, DATE '2020-02-28' FROM core.operational_action, generate_series(1, 510) WHERE id = %s",
            [first["id"]],
        )
        conn.execute(
            "UPDATE core.operational_action o SET approval_status = 'APPROVED', approval_rationale = s.approval_rationale, "
            "approved_by = s.approved_by, approved_at = s.approved_at, completion_status = 'COMPLETED', "
            "completion_evidence = s.completion_evidence, completed_by = s.completed_by, completed_at = s.completed_at "
            "FROM core.operational_action s WHERE s.id = %s "
            "AND o.description = 'Old completed action for the queue limit test'",
            [first["id"]],
        )
    fresh = await api.post("/api/v1/actions", json=body, headers=as_user(analyst))
    # Another SHO of the station (the bulk rows were approved by `sho`): completed actions must not
    # crowd out, or appear as, work to do.
    other_sho, _ = make_user(Role.SHO, stations=("MAH-S01",))
    queue = (await api.get("/api/v1/review-queue?queue=actions", headers=as_user(other_sho))).json()
    ids = [i["id"] for i in queue["data"]]
    assert fresh.json()["id"] in ids
    assert first["id"] not in ids
    assert queue["more"] is False
    outcomes = (
        await api.get("/api/v1/review-queue?queue=outcomes", headers=as_user(analyst))
    ).json()
    assert outcomes["more"] is True  # 511 completed actions without a review: more than shown
    assert len(outcomes["data"]) == 200
    badge = (await api.get("/api/v1/review-queue/summary", headers=as_user(analyst))).json()
    assert badge["more"] is True


async def test_a10_outcome_counts_use_the_reviews_own_filters(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """The outcome ignored the review's hour window (6 records reviewed, 23 measured)."""
    params = {**LOCATION, "hour_from": 18, "hour_to": 22}
    review, analyst = await approved(api, make_user, params)
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
        json={"evidence": "Patrol logs for the period."},
        headers=as_user(officer),
    )
    assert r.status_code == 200, r.text
    # Baseline and after periods split the review's quarter (2026-04-01..06-30).
    outcome = await api.post(
        "/api/v1/outcomes",
        json={
            **OUTCOME,
            "comparison_kind": "NONE",
            "comparison_keys": [],
            "action_id": action["id"],
        },
        headers=as_user(analyst),
    )
    assert outcome.status_code == 201, outcome.text
    before, after = outcome.json()["before_metrics"], outcome.json()["after_metrics"]
    assert before["action_area"] + after["action_area"] == review["record_count"]
    assert "18" in str(before["filters"])  # the hour window is recorded


async def test_a11_dates_follow_the_action(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    _review, action, analyst = await completed_action(api, make_user)
    base = {**OUTCOME, "action_id": action["id"]}
    for change in (
        {"baseline_start": "2026-04-20", "baseline_end": "2026-05-20", "after_start": "2026-05-21"},
        {"after_end": "2026-12-31"},  # future, but within the analytics period limits
    ):
        r = await api.post("/api/v1/outcomes", json={**base, **change}, headers=as_user(analyst))
        assert r.status_code == 422, (change, r.text)
    # An action cannot be completed before it starts.
    review, analyst2 = await approved_review(api, make_user)
    officer, officer_id = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    future = {**action_body(review, officer_id), "start_at": "2099-01-01", "end_at": "2099-01-31"}
    created = (await api.post("/api/v1/actions", json=future, headers=as_user(analyst2))).json()
    await api.post(
        f"/api/v1/actions/{created['id']}/approvals",
        json={"decision": "APPROVE", "rationale": "Proportionate and within the reviewed cells."},
        headers=as_user(sho),
    )
    r = await api.post(
        f"/api/v1/actions/{created['id']}/completions",
        json={"evidence": "Patrol logs for the period."},
        headers=as_user(officer),
    )
    assert r.status_code == 409


async def test_a12_assignee_lists_are_audited(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    rid = str(uuid.uuid4())
    r = await api.get(
        "/api/v1/actions/assignees?station=PDY-S01", headers={**as_user(sho), "X-Request-ID": rid}
    )
    assert r.status_code == 200
    assert [e["action"] for e in audit_rows(rid)] == ["RECORD_VIEW"]


async def test_a13_approver_does_not_complete_and_completer_does_not_review(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    review, analyst = await approved_review(api, make_user)
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    action = (
        await api.post("/api/v1/actions", json=action_body(review), headers=as_user(analyst))
    ).json()
    approve = {"decision": "APPROVE", "rationale": "Proportionate and within the reviewed cells."}
    assert (
        await api.post(
            f"/api/v1/actions/{action['id']}/approvals", json=approve, headers=as_user(sho)
        )
    ).status_code == 200
    rid = str(uuid.uuid4())
    r = await api.post(
        f"/api/v1/actions/{action['id']}/completions",
        json={"evidence": "Patrol logs for the period."},
        headers={**as_user(sho), "X-Request-ID": rid},
    )
    assert r.status_code == 409
    assert [(e["result"], e["reason"]) for e in audit_rows(rid)] == [
        ("DENY", "SEPARATION_OF_DUTIES")
    ]
    other_sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    assert (
        await api.post(
            f"/api/v1/actions/{action['id']}/completions",
            json={"evidence": "Patrol logs for the period."},
            headers=as_user(other_sho),
        )
    ).status_code == 200
    # The SHO who completed it may not review its outcome.
    r = await api.post(
        "/api/v1/outcomes", json={**OUTCOME, "action_id": action["id"]}, headers=as_user(other_sho)
    )
    assert r.status_code == 409


async def test_queue_filters_and_outcome_text_can_be_cleared(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    _review, action, analyst = await completed_action(api, make_user)
    url = "/api/v1/review-queue?queue=outcomes"
    ids = lambda body: [i["id"] for i in body["data"]]  # noqa: E731
    assert action["id"] in ids(
        (await api.get(f"{url}&overdue=true&region=PDY", headers=as_user(analyst))).json()
    )
    assert action["id"] not in ids(
        (await api.get(f"{url}&region=KKL", headers=as_user(analyst))).json()
    )
    assert action["id"] not in ids(
        (await api.get(f"{url}&mine=true", headers=as_user(analyst))).json()
    )
    assert action["id"] not in ids(
        (await api.get(f"{url}&priority=LOW", headers=as_user(analyst))).json()
    )
    created = (
        await api.post(
            "/api/v1/outcomes",
            json={**OUTCOME, "action_id": action["id"]},
            headers=as_user(analyst),
        )
    ).json()
    assert action["id"] in ids((await api.get(f"{url}&mine=true", headers=as_user(analyst))).json())
    patch = f"/api/v1/outcomes/{created['id']}"
    await api.patch(
        patch,
        json={"displacement_diffusion_notes": "No change next door."},
        headers=as_user(analyst),
    )
    cleared = await api.patch(
        patch, json={"displacement_diffusion_notes": ""}, headers=as_user(analyst)
    )
    assert cleared.json()["displacement_diffusion_notes"] is None


async def test_dq_issue_history_comes_from_the_audit_log(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    issues = (
        await api.get("/api/v1/dq/issues?state=OPEN&region=PDY&limit=1", headers=as_user(steward))
    ).json()
    issue = issues["data"][0]["id"]
    r = await api.patch(
        f"/api/v1/dq/issues/{issue}",
        json={"state": "ASSIGNED", "resolution_note": None},
        headers=as_user(steward),
    )
    assert r.status_code == 200, r.text
    history = await api.get(
        f"/api/v1/workflow-history?object_type=DQ_ISSUE&object_id={issue}", headers=as_user(steward)
    )
    assert [(h["from_state"], h["to_state"]) for h in history.json()["data"]] == [
        ("OPEN", "ASSIGNED")
    ]
    outsider, _ = make_user(Role.DATA_STEWARD, ("KKL",))
    assert (
        await api.get(
            f"/api/v1/workflow-history?object_type=DQ_ISSUE&object_id={issue}",
            headers=as_user(outsider),
        )
    ).status_code == 404


async def test_snapshot_references_are_ids_not_free_text(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """A random snapshot id was refused about 5% of the time as a 'phone number' (the flaky test)."""
    _review, action, analyst = await completed_action(api, make_user)
    oid = (
        await api.post(
            "/api/v1/outcomes",
            json={**OUTCOME, "action_id": action["id"]},
            headers=as_user(analyst),
        )
    ).json()["id"]
    phone_like = {
        "evidence_references": [
            {"kind": "EVIDENCE_SNAPSHOT", "value": "98765432-1098-4abc-8def-0123456789ab"}
        ]
    }
    r = await api.patch(f"/api/v1/outcomes/{oid}", json=phone_like, headers=as_user(analyst))
    assert r.status_code == 404  # a well-formed id that is not a visible snapshot, not a 422
    for bad in (
        {"kind": "EVIDENCE_SNAPSHOT", "value": "not an id"},
        {"kind": "DOCUMENT", "value": "Call 98765 43210 for the log"},
    ):
        r = await api.patch(
            f"/api/v1/outcomes/{oid}", json={"evidence_references": [bad]}, headers=as_user(analyst)
        )
        assert r.status_code == 422, bad
