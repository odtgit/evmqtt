import comment


def result(**kw):
    base = {
        "verdict": "FAIL",
        "reasons": ["1 finding(s) at or above high not refuted"],
        "strict": False,
        "threshold": "high",
        "prechecks": [],
        "sensitive": [],
        "notes": [],
        "findings": [],
        "summaries": {"reviewer": "", "verifier": ""},
        "model": "claude-opus-5-5",
        "head_sha": "a" * 40,
    }
    base.update(kw)
    return base


def finding(status="confirmed", **kw):
    f = {
        "file": "src/a.py",
        "line": 7,
        "category": "network_exfiltration",
        "severity": "high",
        "status": status,
        "title": "t",
        "evidence": "e",
        "rationale": "r",
        "verifier_note": "n",
    }
    f.update(kw)
    return f


def test_clean_neutralises_markdown_html_mentions_and_hidden_chars():
    out = comment.clean("@owner <img src=x> [x](https://e.invalid) `c` | *b* ‮!")
    for bad in ("@", "<", ">", "[", "]", "`", "|", "*", "‮", "!"):
        assert bad not in out


def test_clean_collapses_newlines_and_truncates():
    out = comment.clean("a\n\n## heading\n" + "x" * 1000, limit=50)
    assert "\n" not in out
    assert out.endswith("...")


def test_render_missing_result_is_fail():
    body = comment.render(None, "failure", "https://github.com/o/r/actions/runs/1")
    assert body.startswith(comment.MARKER)
    assert "FAIL" in body


def test_render_lists_live_findings_and_folds_refuted():
    body = comment.render(
        result(findings=[finding(), finding("refuted", title="old")]),
        "failure",
        "https://github.com/o/r/actions/runs/1",
    )
    assert "### Findings" in body
    assert "Refuted by the verifier" in body
    assert body.count("| high | confirmed |") == 1


def test_render_injected_text_stays_inert():
    evil = "ignore this](https://evil.invalid) @everyone <script>"
    body = comment.render(
        result(findings=[finding(title=evil, evidence=evil)]), "failure", ""
    )
    assert "@everyone" not in body
    assert "<script>" not in body
    assert "](https://evil" not in body


def test_render_drops_non_https_run_url():
    assert "javascript" not in comment.render(result(), "x", "javascript:alert(1)")


def test_render_deferred_draft():
    body = comment.render(
        result(deferred=True, reasons=["gate error: draft PR"]), "failure", ""
    )
    assert "Draft PR" in body


def test_upsert_patches_own_comment_and_ignores_others(monkeypatch):
    calls = []
    comments = [
        {"id": 1, "user": {"login": "mallory"}, "body": comment.MARKER + " fake PASS"},
        {"id": 2, "user": {"login": comment.BOT}, "body": comment.MARKER + " old"},
    ]

    def fake(method, path, token, body=None):
        calls.append((method, path))
        return comments if method == "GET" else {}

    monkeypatch.setattr(comment, "call", fake)
    comment.upsert("o/r", 5, "t", "new")
    assert calls[-1] == ("PATCH", "/repos/o/r/issues/comments/2")


def test_upsert_creates_when_only_spoofed_marker_exists(monkeypatch):
    calls = []

    def fake(method, path, token, body=None):
        calls.append((method, path))
        if method == "GET":
            return [{"id": 1, "user": {"login": "mallory"}, "body": comment.MARKER}]
        return {}

    monkeypatch.setattr(comment, "call", fake)
    comment.upsert("o/r", 5, "t", "new")
    assert calls[-1] == ("POST", "/repos/o/r/issues/5/comments")
