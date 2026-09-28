"""Snapshot a real commit from this repo's history as an inert fixture directory.

python fixture_from_git.py NAME COMMIT --expect FAIL [--paths a b] [--added-only]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "tests" / "fixtures"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("commit")
    ap.add_argument("--expect", choices=["PASS", "FAIL"], required=True)
    ap.add_argument("--paths", nargs="*")
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    sha = git("rev-parse", args.commit).strip()
    specs = [f":(top){p}" for p in args.paths or []]
    names = git("diff", "--name-only", f"{sha}^", sha, "--", *specs).split()
    root = FIXTURES / args.name
    for side, rev in (("before", f"{sha}^"), ("after", sha)):
        for path in names:
            try:
                text = git("show", f"{rev}:{path}")
            except subprocess.CalledProcessError:
                continue
            out = root / side / f"{path}.fx"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text)
    subject, _, body = git("log", "-1", "--format=%s%n%b", sha).partition("\n")
    meta = {
        "title": subject,
        "body": body.strip(),
        "commits": [subject],
        "author": "historical",
        "author_association": "CONTRIBUTOR",
        "source_commit": sha,
        "paths": names,
        "expect": args.expect,
        "note": args.note,
    }
    (root / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(root, len(names), "files")


if __name__ == "__main__":
    main()
