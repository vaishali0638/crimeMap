"""Turn an analytics envelope into an export: aggregated tables only, never record-level rows.

Every export carries (spec "REPORTS"): requesting user, date/time, active filters, data
classification, source and refresh time, data-quality note, method/definition note, the watermark
``INTERNAL / RESTRICTED`` and the audit/expiry policy. CSV cells that a spreadsheet would treat as a
formula are prefixed with an apostrophe (CSV injection).
"""

import csv
import io
import json
import re
from datetime import datetime
from typing import Any

from crimemap_api.reports.pdf import PdfDocument

WATERMARK = "INTERNAL / RESTRICTED"
EXPIRY_POLICY = "This export expires 24 hours after it was generated; it is then deleted. Generating and downloading it were recorded in the audit log."

Table = tuple[str, list[str], list[list[str]]]


def _s(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def tables(section: str, data: dict[str, Any]) -> list[Table]:
    """Aggregated tables for a section (the same data the workbench's Table view shows)."""
    out: list[Table] = []
    if section == "overview":
        k = data["kpis"]
        out.append(
            (
                "Key figures",
                ["Measure", "Value"],
                [
                    ["Selected records", _s(k["selected"])],
                    ["Comparison period records", _s(k["comparison_selected"])],
                    ["Change vs comparison (%)", _s(k["change_pct"])],
                    ["Usable location (%)", _s(k["usable_location_pct"])],
                    ["Open review items", _s(k["open_review_items"])],
                ],
            )
        )
        out.append(
            (
                f"Trend by {data['trend']['grain']}",
                ["Bucket", "Records"],
                [[p["bucket"], _s(p["count"])] for p in data["trend"]["points"]],
            )
        )
        out.append(
            (
                "Categories",
                ["Category", "Records"],
                [[c["label"], _s(c["count"])] for c in data["categories"]],
            )
        )
        out.append(
            (
                "Data quality status",
                ["Status", "Records"],
                [[q["status"], _s(q["count"])] for q in data["quality"]],
            )
        )
    elif section == "location":
        feats = data["map"]["features"] if data.get("map") else []
        name = "Cell" if data["unit"] == "HEX" else data["unit"].title()
        out.append(
            (
                f"Counts by {name.lower()}",
                [name, "Records", "Comparison"],
                [
                    [
                        f["properties"].get("key") or f["properties"].get("name", ""),
                        _s(f["properties"].get("count")),
                        _s(f["properties"].get("comparison_count")),
                    ]
                    for f in sorted(feats, key=lambda f: -(f["properties"].get("count") or 0))[:200]
                ],
            )
        )
        out.append(
            (
                "Repeat locations",
                ["Locality", "Station", "Weeks with records", "Records"],
                [
                    [r["locality"], _s(r["station"]), _s(r["weeks"]), _s(r["count"])]
                    for r in data["repeat_locations"]
                ],
            )
        )
        if data.get("persistence"):
            out.append(
                (
                    "Cells in the top 10% (persistence)",
                    ["Cell", "Status", "Records", "Comparison"],
                    [
                        [
                            p["key"],
                            p["status"].replace("_", " ").lower(),
                            _s(p["count"]),
                            _s(p["comparison_count"]),
                        ]
                        for p in data["persistence"]
                    ],
                )
            )
        out.append(
            (
                "Density surface",
                ["Available", "Note"],
                [["yes" if data["kde"]["available"] else "no", _s(data["kde"]["reason"])]],
            )
        )
    elif section == "case_trends":
        out.append(
            (
                f"Records by {data['grain']}",
                ["Bucket", "Records"],
                [[p["bucket"], _s(p["count"])] for p in data["series"]],
            )
        )
        out.append(
            (
                "Day of week x hour (IST)",
                ["Day", *[f"{h:02d}" for h in data["day_hour"]["hours"]]],
                [
                    [d, *[_s(v) for v in row]]
                    for d, row in zip(
                        data["day_hour"]["days"], data["day_hour"]["counts"], strict=True
                    )
                ],
            )
        )
        out.append(
            (
                "Occurrence-to-report delay",
                ["Delay", "Records"],
                [[d["bucket"].replace("_", " "), _s(d["count"])] for d in data["delay"]],
            )
        )
        st = data["status"]
        if st.get("available"):
            out.append(
                (
                    "Case status by month",
                    ["Month", "Active", "Detected", "Closed"],
                    [
                        [p["bucket"], _s(p["active"]), _s(p["detected"]), _s(p["closed"])]
                        for p in st["points"]
                    ],
                )
            )
    elif section == "comparison":
        a, b = data["a"], data["b"]
        out.append(
            (
                "A vs B",
                ["", "A", "B", "Difference", "Change (%)"],
                [
                    [
                        "Records",
                        _s(a["count"]),
                        _s(b["count"]),
                        _s(data["difference"]["difference"]),
                        _s(data["difference"]["change_pct"]),
                    ]
                ],
            )
        )
        out.append(
            (
                f"A = {a['label']}; B = {b['label']}",
                ["Category", "A", "B", "Difference", "Change (%)"],
                [
                    [t["label"], _s(t["a"]), _s(t["b"]), _s(t["difference"]), _s(t["change_pct"])]
                    for t in data["table"]
                ],
            )
        )
    elif section == "data_quality":
        out.append(
            (
                "Data-quality measures",
                ["Measure", "Records", "%"],
                [[m["label"], _s(m["count"]), _s(m["pct"])] for m in data["measures"]],
            )
        )
        out.append(
            (
                "Location method",
                ["Method", "Records"],
                [[g["method"], _s(g["count"])] for g in data["geocode_methods"]],
            )
        )
        out.append(
            (
                "Location confidence",
                ["Band", "Records"],
                [[c["band"], _s(c["count"])] for c in data["confidence"]],
            )
        )
        out.append(
            (
                "Feed health",
                ["Source", "Status", "Health", "Last batch"],
                [
                    [f["name"], f["approved_status"], _s(f["health"]), _s(f["last_batch_at"])]
                    for f in data["feeds"]
                ],
            )
        )
    return out


def metadata_lines(
    meta: dict[str, Any], *, user: str, generated_at: datetime, snapshot_id: str
) -> list[tuple[str, str]]:
    return [
        ("Report", f"CRIMEMAP {meta['title']}"),
        ("Classification", f"{meta['classification']} ({WATERMARK})"),
        (
            "Environment",
            f"{meta['environment']} - DEMO / SYNTHETIC DATA"
            if meta["environment"] != "PRODUCTION"
            else meta["environment"],
        ),
        ("Requested by", user),
        ("Generated (IST)", generated_at.strftime("%Y-%m-%d %H:%M")),
        ("Active filters", json.dumps(meta["filters_applied"], sort_keys=True)),
        ("Records counted", str(meta["record_count"])),
        (
            "Excluded",
            f"{meta['excluded_count']} {json.dumps(meta['excluded_reasons'], sort_keys=True)}",
        ),
        ("Not counted", meta["suppressed"] or "-"),
        (
            "Source and refresh",
            f"CRIMEMAP analytical store (demo feeds); data refreshed {meta['data_freshness'] or 'unknown'}",
        ),
        ("Data quality", meta["data_quality_note"]),
        ("Method", f"{meta['method']} {json.dumps(meta['method_parameters'], sort_keys=True)}"),
        ("Caveats", " | ".join(meta["caveats"])),
        ("Evidence snapshot", snapshot_id),
        ("Audit and expiry", EXPIRY_POLICY),
    ]


_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def _safe_cell(value: str) -> str:
    """Neutralise text a spreadsheet would run as a formula; plain numbers (also negative) stay numbers."""
    if _NUMBER.match(value):
        return value
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _csv_value(value: str) -> str:
    return "" if value == "-" else _safe_cell(value)  # a missing value is an empty cell, not text


def render_csv(section: str, data: dict[str, Any], meta: dict[str, Any], **who: Any) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([f"# {WATERMARK}"])
    for k, v in metadata_lines(meta, **who):
        w.writerow([_safe_cell(f"# {k}"), _csv_value(v)])
    for title, headers, rows in tables(section, data):
        w.writerow([])
        w.writerow([_safe_cell(title)])
        w.writerow([_safe_cell(h) for h in headers])
        for row in rows:
            w.writerow([_csv_value(c) for c in row])
    w.writerow([])
    w.writerow([f"# {WATERMARK}"])
    return buf.getvalue().encode("utf-8-sig")


def render_pdf(section: str, data: dict[str, Any], meta: dict[str, Any], **who: Any) -> bytes:
    doc = PdfDocument(
        header=f"{WATERMARK} - CRIMEMAP {meta['title']} - {meta['environment']}",
        watermark=WATERMARK,
        footer=f"Generated for {who['user']} - expires 24 hours after generation",
    )
    doc.heading(f"CRIMEMAP briefing: {meta['title']}")
    doc.line(meta["explanation"])
    for k, v in metadata_lines(meta, **who):
        doc.line(f"{k}: {v}")
    for title, headers, rows in tables(section, data):
        doc.heading(title)
        doc.table(headers, rows[:300])
    return doc.render()
