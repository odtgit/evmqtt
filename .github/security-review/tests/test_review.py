import json
import sys
import textwrap
from pathlib import Path

import llm
import policy
import pytest
import review
import sources

FIXTURES = Path(__file__).parent / "fixtures"
ALL = sorted(p for p in FIXTURES.iterdir() if p.is_dir())


def fake_cli(tmp_path, p1, p2):
    def result(out):
        return "\n".join(
            json.dumps(e)
            for e in (
                {
                    "type": "system",
                    "subtype": "init",
                    "tools": ["StructuredOutput"],
                    "mcp_servers": [],
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": out,
                    "modelUsage": {llm.MODEL: {}},
                },
            )
        )

    script = tmp_path / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""
            import sys
            sys.stdin.read()
            system = sys.argv[sys.argv.index("--system-prompt") + 1]
            print({result(p1)!r} if "independent adversarial verifier" not in system else {result(p2)!r})
            """
        )
    )
    script.chmod(0o755)
    return str(script)


CLEAN1 = {"summary": "nothing", "findings": []}
CLEAN2 = {"summary": "nothing", "assessments": [], "new_findings": []}
HIGH1 = {
    "summary": "bad",
    "findings": [
        {
            "file": "src/a.py",
            "line": 1,
            "category": "keystroke_logging",
            "severity": "high",
            "title": "t",
            "evidence": "e",
            "rationale": "r",
        }
    ],
}
HIGH2 = {
    "summary": "agree",
    "assessments": [
        {"index": 0, "verdict": "confirmed", "severity": "high", "reasoning": "x"}
    ],
    "new_findings": [],
}


def run(tmp_path, monkeypatch, fixture, p1=CLEAN1, p2=CLEAN2, token="tok", extra=()):
    if token:
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", token)
    else:
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    out = tmp_path / "result.json"
    code = review.main(
        [
            "--fixture",
            str(fixture),
            "--claude",
            fake_cli(tmp_path, p1, p2),
            "--out",
            str(out),
            *extra,
        ]
    )
    return code, json.loads(out.read_text())


def test_fixtures_are_inert_and_complete():
    assert {p.name for p in ALL} >= {"benign_docs_readme", "benign_ble_fix"}
    assert any(p.name.startswith("hist_") for p in ALL)
    for d in ALL:
        meta = json.loads((d / "meta.json").read_text())
        assert meta["expect"] in ("PASS", "FAIL")
        for f in d.rglob("*"):
            if f.is_file():
                assert f.name == "meta.json" or f.name.endswith(".fx"), f


@pytest.mark.parametrize("fixture", ALL, ids=[p.name for p in ALL])
def test_fixtures_pass_prechecks_so_the_ai_decides(fixture):
    pr = sources.from_dir(fixture)
    assert pr.files
    assert policy.run(pr).failures == []


def test_clean_ai_review_passes(tmp_path, monkeypatch):
    code, res = run(tmp_path, monkeypatch, FIXTURES / "benign_docs_readme")
    assert code == 0
    assert res["verdict"] == "PASS"


def test_confirmed_high_finding_fails(tmp_path, monkeypatch):
    code, res = run(
        tmp_path, monkeypatch, FIXTURES / "benign_docs_readme", HIGH1, HIGH2
    )
    assert code == 1
    assert res["verdict"] == "FAIL"


def test_missing_token_fails(tmp_path, monkeypatch):
    code, res = run(tmp_path, monkeypatch, FIXTURES / "benign_docs_readme", token="")
    assert code == 1
    assert any("CLAUDE_CODE_OAUTH_TOKEN" in r for r in res["reasons"])


def test_malformed_ai_output_fails(tmp_path, monkeypatch):
    code, res = run(
        tmp_path, monkeypatch, FIXTURES / "benign_docs_readme", {"summary": "x"}, CLEAN2
    )
    assert code == 1
    assert any("AI review failed" in r for r in res["reasons"])


def test_draft_is_deferred_and_fails(tmp_path, monkeypatch):
    d = tmp_path / "draft"
    (d / "after").mkdir(parents=True)
    (d / "after" / "a.txt.fx").write_text("x\n")
    (d / "meta.json").write_text(
        json.dumps({"title": "t", "draft": True, "expect": "PASS"})
    )
    code, res = run(tmp_path, monkeypatch, d)
    assert code == 1
    assert res.get("deferred")


def test_unloadable_pr_fails(tmp_path, monkeypatch):
    code, res = run(tmp_path, monkeypatch, tmp_path / "missing")
    assert code == 1
    assert any("cannot load PR" in r for r in res["reasons"])


def test_precheck_failure_skips_ai_and_fails(tmp_path, monkeypatch):
    d = tmp_path / "bidi"
    (d / "after").mkdir(parents=True)
    (d / "after" / "a.py.fx").write_text("x = 1  # {{U+202E}} y\n")
    (d / "meta.json").write_text(json.dumps({"title": "t", "expect": "FAIL"}))
    code, res = run(tmp_path, monkeypatch, d)
    assert code == 1
    assert res["prechecks"][0]["category"] == "trojan_source"
    assert res["ai"] == []


def test_github_output_is_base64_json(tmp_path, monkeypatch):
    gh_out = tmp_path / "gh_out"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    review.main(
        [
            "--fixture",
            str(FIXTURES / "benign_docs_readme"),
            "--claude",
            fake_cli(tmp_path, CLEAN1, CLEAN2),
        ]
    )
    import base64

    line = next(x for x in gh_out.read_text().splitlines() if x.startswith("result="))
    assert json.loads(base64.b64decode(line[len("result=") :]))["verdict"] == "PASS"


def test_from_dir_builds_unified_patches():
    pr = sources.from_dir(FIXTURES / "benign_ble_fix")
    f = next(f for f in pr.files if f.path == "src/evmqtt/sysinfo.py")
    assert f.status == "modified"
    assert f.patch.startswith("@@")
    assert policy.added_lines(f.patch)
