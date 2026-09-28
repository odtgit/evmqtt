"""Security review gate entry point. Exit 0 on PASS, 1 on FAIL or any error."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import llm  # noqa: E402
import policy  # noqa: E402
import sources  # noqa: E402
import verdict  # noqa: E402


def load(args: argparse.Namespace) -> sources.PullRequest:
    if args.fixture:
        return sources.from_dir(Path(args.fixture))
    if args.git_range:
        base, _, head = args.git_range.partition("...")
        return sources.from_git(Path(args.repo_dir), base, head or "HEAD", args.title)
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    number = int(os.environ.get("PR_NUMBER") or 0)
    if not (token and repo and number):
        raise sources.SourceError(
            "GITHUB_TOKEN, GITHUB_REPOSITORY and PR_NUMBER are required"
        )
    return sources.from_github(repo, number, token, os.environ.get("PR_HEAD_SHA", ""))


def ai_review(
    pr: sources.PullRequest, pre: policy.PrecheckResult, binary: str, token: str
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    nonce = secrets.token_hex(8)
    context = llm.build_context(pr, pre, nonce)
    p1, m1 = llm.run_claude(
        llm.system_prompt("reviewer", nonce),
        llm.pass1_prompt(context, pre),
        llm.PASS1_SCHEMA,
        binary=binary,
        token=token,
    )
    p2, m2 = llm.run_claude(
        llm.system_prompt("verifier", nonce),
        llm.pass2_prompt(context, pre, p1, nonce),
        llm.PASS2_SCHEMA,
        binary=binary,
        token=token,
    )
    return p1, p2, [m1, m2]


def review(args: argparse.Namespace) -> dict[str, Any]:
    errors: list[str] = []
    pre = policy.PrecheckResult()
    p1 = p2 = None
    meta: list[dict[str, Any]] = []
    pr = None
    try:
        pr = load(args)
    except Exception as err:
        errors.append(f"cannot load PR: {type(err).__name__}: {err}")
    if pr is not None and pr.draft and not args.include_drafts:
        result = verdict.decide(
            pre, None, None, ["draft PR, review runs when marked ready"]
        )
        result["deferred"] = True
        return result
    if pr is not None:
        pre = policy.run(pr)
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if args.skip_ai:
            errors.append("AI review skipped")
        elif pre.failures and not args.force_ai:
            pass
        elif not token:
            errors.append("CLAUDE_CODE_OAUTH_TOKEN secret is missing")
        else:
            try:
                p1, p2, meta = ai_review(pr, pre, args.claude, token)
            except llm.LLMError as err:
                errors.append(f"AI review failed: {err}")
            except Exception as err:
                traceback.print_exc()
                errors.append(f"AI review failed: {type(err).__name__}")
    result = verdict.decide(pre, p1, p2, errors)
    result.update(
        model=llm.MODEL,
        head_sha=getattr(pr, "head_sha", ""),
        base_sha=getattr(pr, "base_sha", ""),
        files=len(getattr(pr, "files", [])),
        ai=meta,
    )
    return result


def trim(value: Any, limit: int = 2000) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [trim(v, limit) for v in value[:200]]
    if isinstance(value, dict):
        return {k: trim(v, limit) for k, v in value.items()}
    return value


def emit(result: dict[str, Any], out: str | None) -> None:
    result = trim(result)
    text = json.dumps(result, indent=1)
    if out:
        Path(out).write_text(text)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"result={base64.b64encode(text.encode()).decode()}\n")
            fh.write(f"verdict={result['verdict']}\n")
    print(f"security-review: {result['verdict']}")
    for r in result["reasons"]:
        print(f"  - {r}")
    for i in result["prechecks"]:
        print(
            f"  pre-check: {i['file']}:{i['line']} [{i['category']}] {i['title']} {i['evidence']}"
        )
    for f in result["findings"]:
        print(
            f"  {f['status']:<10} {f['severity']:<8} {f['file']}:{f['line']} "
            f"[{f['category']}] {f['title']}"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="fixture directory")
    ap.add_argument("--git-range", help="BASE...HEAD in --repo-dir")
    ap.add_argument("--repo-dir", default=".")
    ap.add_argument("--title", default="")
    ap.add_argument("--claude", default=os.environ.get("CLAUDE_BIN", "claude"))
    ap.add_argument("--out")
    ap.add_argument("--skip-ai", action="store_true")
    ap.add_argument(
        "--force-ai", action="store_true", help="run AI even if pre-checks fail"
    )
    ap.add_argument("--include-drafts", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = review(args)
    except Exception as err:
        traceback.print_exc()
        result = verdict.decide(
            policy.PrecheckResult(), None, None, [type(err).__name__]
        )
    emit(result, args.out)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
