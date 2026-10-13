"""Alert rules and the Grafana dashboard use only metrics the API exports, and every alert links to
a runbook section that exists (Phase 8)."""

import json
import re
from pathlib import Path

import yaml

from crimemap_api.metrics import SIGNALS, Registry, render

ROOT = Path(__file__).resolve().parents[4]
OBS = ROOT / "infrastructure" / "observability"
METRIC = re.compile(r"\bcrimemap_[a-z0-9_]+")


def exported() -> set[str]:
    registry = Registry()
    registry.observe("GET", "/x", 200, 0.1)
    body = render(registry, {name: 0.0 for name, _, _ in SIGNALS})
    return {
        line.split("{")[0].split(" ")[0] for line in body.splitlines() if not line.startswith("#")
    }


def rules() -> list[dict[str, object]]:
    doc = yaml.safe_load((OBS / "alerts.yml").read_text())
    return [rule for group in doc["groups"] for rule in group["rules"]]


def test_alert_expressions_use_exported_metrics_only() -> None:
    names = exported()
    for rule in rules():
        used = set(METRIC.findall(str(rule["expr"])))
        assert used or "up{" in str(rule["expr"]), rule["alert"]
        assert used <= names, (rule["alert"], used - names)


def test_dashboard_queries_use_exported_metrics_only() -> None:
    names = exported()
    dashboard = json.loads((OBS / "grafana" / "dashboards" / "crimemap-overview.json").read_text())
    exprs = [t["expr"] for p in dashboard["panels"] for t in p["targets"]]
    assert exprs
    for expr in exprs:
        assert set(METRIC.findall(expr)) <= names, expr


def test_spec_security_alerts_are_all_present() -> None:
    # Spec, Security: mass export, unusual geographic access, repeated denied access, privilege
    # changes, failed ingestion and model failures.
    alerts = {str(r["alert"]) for r in rules()}
    assert {
        "CrimemapMassExport",
        "CrimemapUnusualGeographicAccess",
        "CrimemapRepeatedDeniedAccess",
        "CrimemapPrivilegeChange",
        "CrimemapIngestionFailed",
        "CrimemapAiModelFailure",
    } <= alerts


def _anchors(markdown: str) -> set[str]:
    out = set()
    for line in markdown.splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            slug = re.sub(r"[^\w\- ]", "", title).replace(" ", "-")
            out.add(slug)
    return out


def test_every_alert_links_to_an_existing_runbook_section() -> None:
    for rule in rules():
        link = str(rule["annotations"]["runbook"])  # type: ignore[index]
        path, _, anchor = link.partition("#")
        doc = ROOT / path
        assert doc.is_file(), link
        assert anchor in _anchors(doc.read_text()), link
