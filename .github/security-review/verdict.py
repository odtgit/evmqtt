"""Combine pre-checks and both passes into PASS or FAIL. Fail closed."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import policy

RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def threshold(strict: bool) -> str:
    return "medium" if strict else "high"


def merge(p1: dict[str, Any], p2: dict[str, Any]) -> list[dict[str, Any]]:
    by_index: dict[int, dict[str, Any]] = {}
    for a in p2.get("assessments", []):
        i = a["index"]
        prev = by_index.get(i)
        if prev is None or RANK[a["severity"]] > RANK[prev["severity"]]:
            by_index[i] = a
    out = []
    for i, f in enumerate(p1.get("findings", [])):
        a = by_index.get(i)
        item = {**f, "source": "reviewer"}
        if a is None:
            item.update(
                status="unverified", verifier_note="verifier gave no assessment"
            )
        elif a["verdict"] == "uncertain":
            sev = max(f["severity"], a["severity"], key=RANK.__getitem__)
            item.update(status="uncertain", severity=sev, verifier_note=a["reasoning"])
        else:
            item.update(
                status=a["verdict"],
                severity=a["severity"],
                verifier_note=a["reasoning"],
            )
        out.append(item)
    for f in p2.get("new_findings", []):
        out.append(
            {**f, "source": "verifier", "status": "confirmed", "verifier_note": ""}
        )
    return out


def blocking(findings: list[dict[str, Any]], strict: bool) -> list[dict[str, Any]]:
    bar = RANK[threshold(strict)]
    return [
        f for f in findings if f["status"] != "refuted" and RANK[f["severity"]] >= bar
    ]


def decide(
    pre: policy.PrecheckResult,
    p1: dict[str, Any] | None,
    p2: dict[str, Any] | None,
    errors: list[str],
) -> dict[str, Any]:
    reasons: list[str] = []
    findings: list[dict[str, Any]] = []
    if pre.failures:
        reasons.append(f"{len(pre.failures)} deterministic pre-check failure(s)")
    reasons += [f"gate error: {e}" for e in errors]
    if p1 is not None and p2 is not None:
        findings = merge(p1, p2)
        block = blocking(findings, pre.strict)
        if block:
            reasons.append(
                f"{len(block)} finding(s) at or above {threshold(pre.strict)} not refuted"
            )
    elif not pre.failures and not errors:
        reasons.append("AI review did not complete")
    return {
        "verdict": "FAIL" if reasons else "PASS",
        "reasons": reasons,
        "strict": pre.strict,
        "threshold": threshold(pre.strict),
        "prechecks": [asdict(i) for i in pre.failures],
        "sensitive": pre.sensitive,
        "notes": pre.notes,
        "findings": findings,
        "summaries": {
            "reviewer": (p1 or {}).get("summary", ""),
            "verifier": (p2 or {}).get("summary", ""),
        },
    }
