"""Create or update the single gate comment on the PR. Stdlib only."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request
from typing import Any

MARKER = "<!-- evmqtt-security-review -->"
BOT = "github-actions[bot]"
API = "https://api.github.com"
_HIDDEN = re.compile("[\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def clean(value: Any, limit: int = 300) -> str:
    text = _HIDDEN.sub("?", str(value))
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "..."
    for a, b in (
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ("@", "&#64;"),
        ("|", "&#124;"),
        ("`", "&#96;"),
        ("[", "&#91;"),
        ("]", "&#93;"),
        ("*", "&#42;"),
        ("_", "&#95;"),
        ("!", "&#33;"),
        ("#", "&#35;"),
        ("~", "&#126;"),
        ("\\", "&#92;"),
    ):
        text = text.replace(a, b)
    return text


def render(result: dict[str, Any] | None, job_status: str, run_url: str) -> str:
    lines = [MARKER]
    if result is None:
        lines += [
            "## security-review: FAIL",
            "",
            f"The gate produced no result (job status: {clean(job_status)}). Treat as failed.",
        ]
    else:
        lines.append(f"## security-review: {result['verdict']}")
        lines.append("")
        if result.get("deferred"):
            lines.append(
                "Draft PR. The review runs when the PR is marked ready for review."
            )
        else:
            mode = "strict" if result.get("strict") else "normal"
            head = clean(result.get("head_sha", ""))[:12]
            lines.append(
                f"Head `{head}`, mode {mode}, blocking at {result.get('threshold')} and above, "
                f"model {clean(result.get('model', ''))}."
            )
        for r in result.get("reasons", []):
            lines.append(f"- {clean(r)}")
        if result.get("sensitive"):
            lines.append("")
            lines.append(
                "Sensitive files: "
                + ", ".join(clean(p, 120) for p in result["sensitive"])
            )
        pre = result.get("prechecks") or []
        if pre:
            lines += [
                "",
                "### Pre-check failures",
                "",
                "| File | Line | Category | Issue | Evidence |",
                "|---|---|---|---|---|",
            ]
            for i in pre:
                lines.append(
                    f"| {clean(i['file'], 120)} | {i['line']} | {clean(i['category'])} "
                    f"| {clean(i['title'])} | {clean(i['evidence'], 160)} |"
                )
        findings = result.get("findings") or []
        live = [f for f in findings if f["status"] != "refuted"]
        dead = [f for f in findings if f["status"] == "refuted"]
        if live:
            lines += [
                "",
                "### Findings",
                "",
                "| Severity | Status | Location | Category | Finding | Evidence |",
                "|---|---|---|---|---|---|",
            ]
            for f in live:
                lines.append(
                    f"| {clean(f['severity'])} | {clean(f['status'])} "
                    f"| {clean(f['file'], 120)}:{f['line']} | {clean(f['category'])} "
                    f"| {clean(f['title'])}: {clean(f['rationale'])} | {clean(f['evidence'], 160)} |"
                )
        if dead:
            lines += ["", "<details><summary>Refuted by the verifier</summary>", ""]
            for f in dead:
                lines.append(
                    f"- {clean(f['severity'])} {clean(f['file'], 120)}:{f['line']} "
                    f"{clean(f['title'])}. Verifier: {clean(f.get('verifier_note', ''))}"
                )
            lines += ["", "</details>"]
        for label, text in (result.get("summaries") or {}).items():
            if text:
                lines += ["", f"{label.capitalize()} summary: {clean(text, 600)}"]
    lines += ["", f"[Run log]({run_url})" if run_url.startswith("https://") else ""]
    return "\n".join(lines).rstrip() + "\n"


def call(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "evmqtt-security-review",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    return json.loads(data) if data else None


def find_existing(repo: str, number: int, token: str) -> int | None:
    page = 1
    while True:
        batch = call(
            "GET",
            f"/repos/{repo}/issues/{number}/comments?per_page=100&page={page}",
            token,
        )
        for c in batch:
            if (c.get("user") or {}).get("login") == BOT and MARKER in (
                c.get("body") or ""
            ):
                return int(c["id"])
        if len(batch) < 100:
            return None
        page += 1


def upsert(repo: str, number: int, token: str, body: str) -> None:
    existing = find_existing(repo, number, token)
    if existing is None:
        call("POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": body})
    else:
        call(
            "PATCH", f"/repos/{repo}/issues/comments/{existing}", token, {"body": body}
        )


def main() -> int:
    raw = os.environ.get("RESULT_B64", "")
    result = None
    if raw:
        try:
            result = json.loads(base64.b64decode(raw))
        except ValueError:
            result = None
    body = render(
        result,
        os.environ.get("REVIEW_STATUS", "unknown"),
        os.environ.get("RUN_URL", ""),
    )
    upsert(
        os.environ["GITHUB_REPOSITORY"],
        int(os.environ["PR_NUMBER"]),
        os.environ["GITHUB_TOKEN"],
        body,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
