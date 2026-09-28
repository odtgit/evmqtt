"""Run fixtures through the real gate. Needs CLAUDE_CODE_OAUTH_TOKEN and a claude binary.

python evaluate.py [--claude PATH] [--out results.json] [fixture ...]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import review  # noqa: E402

FIXTURES = Path(__file__).parent / "tests" / "fixtures"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", default="claude")
    ap.add_argument("--out")
    ap.add_argument("names", nargs="*")
    args = ap.parse_args()
    dirs = [FIXTURES / n for n in args.names] or sorted(
        p for p in FIXTURES.iterdir() if p.is_dir()
    )
    rows = []
    for d in dirs:
        expect = json.loads((d / "meta.json").read_text())["expect"]
        ns = argparse.Namespace(
            fixture=str(d),
            git_range=None,
            repo_dir=".",
            title="",
            claude=args.claude,
            skip_ai=False,
            force_ai=True,
            include_drafts=True,
        )
        t0 = time.monotonic()
        res = review.review(ns)
        took = time.monotonic() - t0
        cost = sum((m.get("cost_usd") or 0) for m in res.get("ai", []))
        rows.append(
            {
                "fixture": d.name,
                "expect": expect,
                "elapsed_s": round(took),
                "cost_usd": round(cost, 4),
                **res,
            }
        )
        ok = "ok" if res["verdict"] == expect else "MISMATCH"
        print(
            f"{d.name:<32} expect {expect} got {res['verdict']} {ok} strict={res['strict']} {took:.0f}s ${cost:.2f}"
        )
        for r in res["reasons"]:
            print(f"    - {r}")
        for f in res["findings"]:
            print(
                f"    {f['status']:<10} {f['severity']:<8} {f['file']}:{f['line']} [{f['category']}] {f['title']}"
            )
        sys.stdout.flush()
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))
    return 0 if all(r["verdict"] == r["expect"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
