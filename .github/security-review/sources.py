"""Load a pull request as data: GitHub API, a local git range, or a fixture dir."""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import policy

API = "https://api.github.com"
FIXTURE_SUFFIX = ".fx"
_TOKEN_RE = re.compile(r"\{\{U\+([0-9A-Fa-f]{4,6})\}\}")


class SourceError(Exception):
    pass


class StaleHead(SourceError):
    pass


@dataclass
class ChangedFile:
    path: str
    status: str
    patch: str | None
    head: bytes | None = None
    base: bytes | None = None
    previous_path: str | None = None
    changes: int = -1


@dataclass
class PullRequest:
    title: str
    body: str
    author: str = ""
    author_association: str = ""
    draft: bool = False
    base_ref: str = ""
    base_sha: str = ""
    head_sha: str = ""
    head_repo: str = ""
    number: int = 0
    repo: str = ""
    commits: list[str] = field(default_factory=list)
    files: list[ChangedFile] = field(default_factory=list)
    oversize: bool = False


def hunks_only(patch: str) -> str:
    lines = policy.lines(patch)
    for i, line in enumerate(lines):
        if line.startswith("@@"):
            return "\n".join(lines[i:])
    return ""


# GitHub


def _get(url: str, token: str, accept: str = "application/vnd.github+json") -> bytes:
    if not url.startswith("http"):
        url = API + url
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "evmqtt-security-review",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return bytes(resp.read())
    except urllib.error.HTTPError as err:
        raise SourceError(f"GitHub API {err.code} for {url}") from None
    except urllib.error.URLError as err:
        raise SourceError(f"GitHub API unreachable: {err.reason}") from None


def _json(url: str, token: str) -> object:
    return json.loads(_get(url, token))


def _paged(url: str, token: str, cap: int) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        batch = _json(f"{url}{sep}per_page=100&page={page}", token)
        assert isinstance(batch, list)
        out += batch
        if len(batch) < 100 or len(out) > cap:
            return out
        page += 1


def _raw(url: str, token: str) -> bytes:
    return _get(url, token, "application/vnd.github.raw+json")


def from_github(repo: str, number: int, token: str, expected_head: str) -> PullRequest:
    pr = _json(f"/repos/{repo}/pulls/{number}", token)
    assert isinstance(pr, dict)
    head_sha = pr["head"]["sha"]
    if expected_head and head_sha != expected_head:
        raise StaleHead(
            f"PR head moved to {head_sha[:12]} since this run started; "
            "the newer run reviews it"
        )
    base_sha = pr["base"]["sha"]
    cmp = _json(f"/repos/{repo}/compare/{base_sha}...{head_sha}", token)
    assert isinstance(cmp, dict)
    merge_base = cmp["merge_base_commit"]["sha"]
    commits = _paged(f"/repos/{repo}/pulls/{number}/commits", token, 250)
    raw_files = _paged(f"/repos/{repo}/pulls/{number}/files", token, policy.MAX_FILES)
    out = PullRequest(
        title=pr.get("title") or "",
        body=pr.get("body") or "",
        author=(pr.get("user") or {}).get("login", ""),
        author_association=pr.get("author_association", ""),
        draft=bool(pr.get("draft")),
        base_ref=pr["base"]["ref"],
        base_sha=merge_base,
        head_sha=head_sha,
        head_repo=((pr["head"].get("repo") or {}).get("full_name") or ""),
        number=number,
        repo=repo,
        commits=[c["commit"]["message"] for c in commits],
    )
    if len(raw_files) > policy.MAX_FILES:
        out.oversize = True
        return out
    for f in raw_files:
        cf = ChangedFile(
            path=f["filename"],
            status=f["status"],
            patch=f.get("patch"),
            previous_path=f.get("previous_filename"),
            changes=int(f.get("changes", -1)),
        )
        if cf.status != "removed":
            cf.head = _raw(f"/repos/{repo}/git/blobs/{f['sha']}", token)
        if cf.status != "added" and policy.is_sensitive(cf.previous_path or cf.path):
            src = urllib.parse.quote(cf.previous_path or cf.path)
            cf.base = _raw(f"/repos/{repo}/contents/{src}?ref={merge_base}", token)
        out.files.append(cf)
    again = _json(f"/repos/{repo}/pulls/{number}", token)
    assert isinstance(again, dict)
    if again["head"]["sha"] != head_sha:
        raise StaleHead("PR head moved during review; the newer run reviews it")
    return out


# local git range


def _git(repo_dir: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args], check=True, capture_output=True
    ).stdout


def from_git(repo_dir: Path, base: str, head: str, title: str = "") -> PullRequest:
    merge_base = _git(repo_dir, "merge-base", base, head).decode().strip()
    status_map = {"A": "added", "M": "modified", "D": "removed", "R": "renamed"}
    raw = _git(repo_dir, "diff", "--name-status", "-z", "-M", merge_base, head)
    parts = raw.decode().split("\0")
    msgs = _git(repo_dir, "log", "--format=%B%x00", f"{merge_base}..{head}")
    pr = PullRequest(
        title=title or f"local {base}...{head}",
        body="",
        base_sha=merge_base,
        head_sha=_git(repo_dir, "rev-parse", head).decode().strip(),
        commits=[m.strip() for m in msgs.decode().split("\0") if m.strip()],
    )
    i = 0
    while i < len(parts) - 1:
        code = parts[i]
        if code.startswith("R") or code.startswith("C"):
            prev, path = parts[i + 1], parts[i + 2]
            i += 3
        else:
            prev, path = None, parts[i + 1]
            i += 2
        status = status_map.get(code[0], "modified")
        paths = [p for p in (prev, path) if p]
        patch = _git(repo_dir, "diff", "-M", merge_base, head, "--", *paths)
        numstat = _git(
            repo_dir, "diff", "--numstat", "-M", merge_base, head, "--", *paths
        )
        cf = ChangedFile(
            path=path,
            status=status,
            patch=None if numstat.startswith(b"-\t-\t") else hunks_only(patch.decode()),
            previous_path=prev,
        )
        if status != "removed":
            cf.head = _git(repo_dir, "show", f"{head}:{path}")
        if status != "added":
            cf.base = _git(repo_dir, "show", f"{merge_base}:{prev or path}")
        pr.files.append(cf)
    return pr


# fixture dir: meta.json, before/, after/; files carry a .fx suffix


def untoken(data: bytes) -> bytes:
    text = data.decode("utf-8")
    return _TOKEN_RE.sub(lambda m: chr(int(m.group(1), 16)), text).encode("utf-8")


def _tree(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name.endswith(FIXTURE_SUFFIX):
            rel = p.relative_to(root).as_posix()[: -len(FIXTURE_SUFFIX)]
            out[rel] = untoken(p.read_bytes())
    return out


def unified(before: bytes | None, after: bytes | None) -> str:
    a = policy.lines((before or b"").decode("utf-8"))
    b = policy.lines((after or b"").decode("utf-8"))
    return "\n".join(list(difflib.unified_diff(a, b, lineterm="", n=3))[2:])


def from_dir(root: Path) -> PullRequest:
    meta = json.loads((root / "meta.json").read_text())
    before, after = _tree(root / "before"), _tree(root / "after")
    pr = PullRequest(
        title=meta.get("title", ""),
        body=meta.get("body", ""),
        author=meta.get("author", "contributor"),
        author_association=meta.get("author_association", "FIRST_TIME_CONTRIBUTOR"),
        draft=bool(meta.get("draft")),
        base_ref="master",
        head_repo=meta.get("head_repo", "contributor/evmqtt"),
        commits=meta.get("commits", [meta.get("title", "")]),
    )
    for path in sorted(set(before) | set(after)):
        b, a = before.get(path), after.get(path)
        if a == b:
            continue
        status = "added" if b is None else "removed" if a is None else "modified"
        pr.files.append(
            ChangedFile(path=path, status=status, patch=unified(b, a), head=a, base=b)
        )
    return pr
