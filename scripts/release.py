#!/usr/bin/env python3
"""Compute and apply semantic version bumps from conventional commit history.

Commands:
  next-version        Print the next version, or nothing if no release is due.
  apply X.Y.Z          Update version fields, prepend CHANGELOG.md, write
                        release notes, and verify consistency.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONVENTIONAL_RE = re.compile(
    r"^(?P<type>\w+)(?:\([^)]*\))?(?P<breaking>!)?:\s*(?P<desc>.*)$"
)
BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+) from")
TRAILING_PR_RE = re.compile(r"\s*\(#(\d+)\)\s*$")

BUMP_ORDER = {"none": 0, "patch": 1, "minor": 2, "major": 3}
PATCH_TYPES = {"fix", "perf"}

CHANGELOG_HEADERS = (("major", "Breaking"), ("minor", "Features"), ("patch", "Fixes"))


class GitError(RuntimeError):
    pass


def run_git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


@dataclass
class ChangeEntry:
    subject: str
    pr_number: int | None
    level: str


def last_tag(cwd: Path) -> str | None:
    try:
        return run_git("describe", "--tags", "--abbrev=0", "--match", "v*", cwd=cwd)
    except GitError:
        return None


def commit_subject(sha: str, cwd: Path) -> str:
    return run_git("log", "-1", "--format=%s", sha, cwd=cwd)


def commit_body(sha: str, cwd: Path) -> str:
    return run_git("log", "-1", "--format=%b", sha, cwd=cwd)


def commit_parents(sha: str, cwd: Path) -> list[str]:
    parents = run_git("log", "-1", "--format=%P", sha, cwd=cwd)
    return parents.split() if parents else []


def classify(subject: str, body: str) -> str:
    match = CONVENTIONAL_RE.match(subject)
    if not match:
        return "none"
    if match.group("breaking") or BREAKING_FOOTER_RE.search(body):
        return "major"
    ctype = match.group("type")
    if ctype == "feat":
        return "minor"
    if ctype in PATCH_TYPES:
        return "patch"
    return "none"


def pr_number_from_subject(subject: str) -> int | None:
    match = TRAILING_PR_RE.search(subject)
    return int(match.group(1)) if match else None


def relevant_commits(tag: str | None, cwd: Path) -> list[ChangeEntry]:
    """Commits to consider for bump/changelog analysis, tag..HEAD.

    Walks --first-parent so each PR (merge commit or squash commit) is
    visited once. A merge commit's own subject is never classified;
    instead its merged branch's non-merge commits are analysed and
    tagged with that PR's number.
    """
    rev_range = f"{tag}..HEAD" if tag else "HEAD"
    shas = [
        s
        for s in run_git(
            "log", "--first-parent", "--pretty=%H", rev_range, cwd=cwd
        ).splitlines()
        if s
    ]
    out: list[ChangeEntry] = []
    for sha in shas:
        subject = commit_subject(sha, cwd=cwd)
        if subject.startswith("release:"):
            continue
        parents = commit_parents(sha, cwd=cwd)
        if len(parents) > 1:
            merge_match = MERGE_PR_RE.match(subject)
            merge_pr = int(merge_match.group(1)) if merge_match else None
            merge_range = f"{parents[0]}..{parents[1]}"
            sub_shas = [
                s
                for s in run_git(
                    "log", "--no-merges", "--pretty=%H", merge_range, cwd=cwd
                ).splitlines()
                if s
            ]
            for sub_sha in sub_shas:
                sub_subject = commit_subject(sub_sha, cwd=cwd)
                if sub_subject.startswith("release:"):
                    continue
                sub_body = commit_body(sub_sha, cwd=cwd)
                level = classify(sub_subject, sub_body)
                pr = pr_number_from_subject(sub_subject) or merge_pr
                out.append(ChangeEntry(sub_subject, pr, level))
        else:
            body = commit_body(sha, cwd=cwd)
            level = classify(subject, body)
            pr = pr_number_from_subject(subject)
            out.append(ChangeEntry(subject, pr, level))
    return out


def next_bump(entries: list[ChangeEntry]) -> str:
    best = "none"
    for entry in entries:
        if BUMP_ORDER[entry.level] > BUMP_ORDER[best]:
            best = entry.level
    return best


def bump_version(version: str, level: str) -> str:
    major, minor, patch = (int(p) for p in version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"no release for level={level}")


def cmd_next_version(cwd: Path) -> str | None:
    tag = last_tag(cwd)
    current = tag.removeprefix("v") if tag else "0.0.0"
    entries = relevant_commits(tag, cwd)
    level = next_bump(entries)
    if level == "none":
        return None
    return bump_version(current, level)


def cmd_forced_version(level: str, cwd: Path) -> str:
    tag = last_tag(cwd)
    current = tag.removeprefix("v") if tag else "0.0.0"
    return bump_version(current, level)


def update_pyproject(version: str, path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text()
    new_text, n = re.subn(
        r'(?m)^version = ".*"$', f'version = "{version}"', text, count=1
    )
    if n == 0:
        raise ValueError(f"no version field found in {path}")
    path.write_text(new_text)
    return True


def update_config_yaml(version: str, path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text()
    new_text, n = re.subn(
        r'(?m)^version:\s*"?[^"\n]+"?\s*$', f'version: "{version}"', text, count=1
    )
    if n == 0:
        raise ValueError(f"no version field found in {path}")
    path.write_text(new_text)
    return True


def update_manifest_json(version: str, path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text()
    text, n = re.subn(
        r'("version":\s*")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1
    )
    if n == 0:
        raise ValueError(f"no version field found in {path}")
    text = re.sub(r"(evmqtt==)[^\"']*", rf"\g<1>{version}", text)
    path.write_text(text)
    return True


def format_entry(entry: ChangeEntry) -> str:
    match = CONVENTIONAL_RE.match(entry.subject)
    desc = match.group("desc") if match else entry.subject
    desc = TRAILING_PR_RE.sub("", desc).strip()
    if entry.pr_number:
        return f"- {desc} (#{entry.pr_number})"
    return f"- {desc}"


def build_changelog_section(version: str, entries: list[ChangeEntry]) -> str:
    groups: dict[str, list[ChangeEntry]] = {"major": [], "minor": [], "patch": []}
    for entry in entries:
        if entry.level in groups:
            groups[entry.level].append(entry)
    lines = [f"## {version}", ""]
    for level, title in CHANGELOG_HEADERS:
        if not groups[level]:
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.extend(format_entry(e) for e in groups[level])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def prepend_changelog(version: str, entries: list[ChangeEntry], path: Path) -> str:
    section = build_changelog_section(version, entries)
    if path.exists():
        text = path.read_text()
        if text.startswith("# Changelog"):
            _, _, remainder = text.partition("\n")
            remainder = remainder.lstrip("\n")
        else:
            remainder = text
    else:
        remainder = ""
    body = "# Changelog\n\n" + section
    if remainder:
        body += "\n" + remainder
    path.write_text(body)
    return section


def run_check_version(cwd: Path) -> None:
    env = os.environ.copy()
    env["EVMQTT_CHECK_VERSION_ROOT"] = str(cwd)
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "check_version.py")],
        check=True,
        env=env,
    )


def apply_version(version: str, cwd: Path) -> list[Path]:
    changed: list[Path] = []
    if update_pyproject(version, cwd / "pyproject.toml"):
        changed.append(cwd / "pyproject.toml")
    if update_config_yaml(version, cwd / "config.yaml"):
        changed.append(cwd / "config.yaml")
    manifest = cwd / "custom_components" / "evmqtt" / "manifest.json"
    if update_manifest_json(version, manifest):
        changed.append(manifest)
    tag = last_tag(cwd)
    entries = relevant_commits(tag, cwd)
    changelog_path = cwd / "CHANGELOG.md"
    section = prepend_changelog(version, entries, changelog_path)
    changed.append(changelog_path)
    (cwd / "RELEASE_NOTES.md").write_text(section)
    run_check_version(cwd)
    return changed


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: release.py <next-version|forced-version LEVEL|apply X.Y.Z>",
            file=sys.stderr,
        )
        return 2
    cmd = argv[1]
    if cmd == "next-version":
        version = cmd_next_version(ROOT)
        if version:
            print(version)
        return 0
    if cmd == "forced-version":
        if len(argv) < 3 or argv[2] not in ("major", "minor", "patch"):
            print(
                "usage: release.py forced-version <major|minor|patch>", file=sys.stderr
            )
            return 2
        print(cmd_forced_version(argv[2], ROOT))
        return 0
    if cmd == "apply":
        if len(argv) < 3:
            print("usage: release.py apply X.Y.Z", file=sys.stderr)
            return 2
        apply_version(argv[2], ROOT)
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
