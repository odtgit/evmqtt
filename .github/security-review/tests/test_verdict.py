import policy
import verdict


def finding(sev="high", cat="network_exfiltration"):
    return {
        "file": "src/a.py",
        "line": 3,
        "category": cat,
        "severity": sev,
        "title": "t",
        "evidence": "e",
        "rationale": "r",
    }


def assess(i, v, sev):
    return {"index": i, "verdict": v, "severity": sev, "reasoning": "why"}


def p1(*fs):
    return {"summary": "s1", "findings": list(fs)}


def p2(assessments=(), new=()):
    return {
        "summary": "s2",
        "assessments": list(assessments),
        "new_findings": list(new),
    }


NORMAL = policy.PrecheckResult()
STRICT = policy.PrecheckResult(sensitive=["pyproject.toml"])


def test_clean_review_passes():
    assert verdict.decide(NORMAL, p1(), p2(), [])["verdict"] == "PASS"


def test_confirmed_high_fails():
    r = verdict.decide(
        NORMAL, p1(finding("high")), p2([assess(0, "confirmed", "high")]), []
    )
    assert r["verdict"] == "FAIL"


def test_refuted_critical_passes_but_is_kept():
    r = verdict.decide(
        NORMAL, p1(finding("critical")), p2([assess(0, "refuted", "info")]), []
    )
    assert r["verdict"] == "PASS"
    assert r["findings"][0]["status"] == "refuted"


def test_verifier_downgrade_to_medium_passes_in_normal_mode():
    r = verdict.decide(
        NORMAL, p1(finding("high")), p2([assess(0, "confirmed", "medium")]), []
    )
    assert r["verdict"] == "PASS"


def test_medium_fails_in_strict_mode():
    r = verdict.decide(
        STRICT, p1(finding("medium")), p2([assess(0, "confirmed", "medium")]), []
    )
    assert r["verdict"] == "FAIL"
    assert r["threshold"] == "medium"


def test_low_passes_in_strict_mode():
    r = verdict.decide(
        STRICT, p1(finding("low")), p2([assess(0, "confirmed", "low")]), []
    )
    assert r["verdict"] == "PASS"


def test_uncertain_keeps_the_higher_severity():
    r = verdict.decide(
        NORMAL, p1(finding("critical")), p2([assess(0, "uncertain", "low")]), []
    )
    assert r["findings"][0]["severity"] == "critical"
    assert r["verdict"] == "FAIL"


def test_unassessed_finding_is_unverified_and_blocks():
    r = verdict.decide(NORMAL, p1(finding("high")), p2(), [])
    assert r["findings"][0]["status"] == "unverified"
    assert r["verdict"] == "FAIL"


def test_out_of_range_assessment_is_ignored():
    r = verdict.decide(NORMAL, p1(), p2([assess(5, "confirmed", "critical")]), [])
    assert r["verdict"] == "PASS"


def test_duplicate_assessments_take_the_most_severe():
    a = [assess(0, "refuted", "info"), assess(0, "confirmed", "high")]
    r = verdict.decide(NORMAL, p1(finding("high")), p2(a), [])
    assert r["verdict"] == "FAIL"


def test_verifier_new_finding_blocks():
    r = verdict.decide(NORMAL, p1(), p2(new=[finding("high", "prompt_injection")]), [])
    assert r["verdict"] == "FAIL"
    assert r["findings"][0]["source"] == "verifier"


def test_precheck_failure_fails_without_ai():
    pre = policy.PrecheckResult(failures=[policy.Issue("a", 1, "trojan_source", "x")])
    r = verdict.decide(pre, None, None, [])
    assert r["verdict"] == "FAIL"
    assert r["prechecks"][0]["category"] == "trojan_source"


def test_errors_fail_even_with_clean_review():
    assert verdict.decide(NORMAL, p1(), p2(), ["timeout"])["verdict"] == "FAIL"


def test_missing_ai_result_fails():
    r = verdict.decide(NORMAL, None, None, [])
    assert r["verdict"] == "FAIL"
    assert "did not complete" in r["reasons"][0]


def test_confirmation_beats_higher_severity_refutation():
    a = [assess(0, "confirmed", "high"), assess(0, "refuted", "critical")]
    r = verdict.decide(NORMAL, p1(finding("high")), p2(a), [])
    assert r["findings"][0]["status"] == "confirmed"
    assert r["verdict"] == "FAIL"
