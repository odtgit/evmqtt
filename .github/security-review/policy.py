"""Deterministic pre-checks. No AI, run first, hard fail."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sources import PullRequest

CHECK_NAME = "security-review"
GATE_WORKFLOW = ".github/workflows/security-review.yml"
GATE_DIR = ".github/security-review/"
FIXTURE_DIR = ".github/security-review/tests/fixtures/"

MAX_FILES = 150
MAX_DIFF_CHARS = 200_000
MAX_CONTEXT_CHARS = 800_000
MAX_FILE_CHARS = 250_000
MAX_IMAGE_BYTES = 1_000_000

SENSITIVE = (
    ".github/*",
    "Dockerfile*",
    "*/Dockerfile*",
    "*.dockerfile",
    ".dockerignore",
    "compose.y*ml",
    "docker-compose*.y*ml",
    "config.yaml",
    "build.yaml",
    "repository.yaml",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "MANIFEST.in",
    "requirements*.txt",
    "*/requirements*.txt",
    "custom_components/*/manifest.json",
    "hacs.json",
    "run.sh",
    "scripts/*",
    "*.service",
    ".gitattributes",
    ".gitmodules",
    ".pre-commit-config.yaml",
    "SECURITY.md",
    "Makefile",
    "tox.ini",
    "noxfile.py",
    "conftest.py",
    "*/conftest.py",
    "sitecustomize.py",
    "*/sitecustomize.py",
    "*.pth",
    "apparmor.txt",
    "build.json",
)

HIDDEN_CHARS = (
    set(range(0x202A, 0x202F))
    | set(range(0x2066, 0x2070))
    | set(range(0x200B, 0x2010))
    | set(range(0x2060, 0x2065))
    | set(range(0xE0000, 0xE0080))
    | {0xFEFF, 0x2028, 0x2029, 0x061C, 0x00AD, 0x034F, 0x115F, 0x1160}
    | {0x17B4, 0x17B5, 0x180E, 0x3164, 0xFFA0}
)

IMAGE_MAGIC = {
    (".png",): (b"\x89PNG\r\n\x1a\n",),
    (".jpg", ".jpeg"): (b"\xff\xd8\xff",),
    (".gif",): (b"GIF87a", b"GIF89a"),
    (".ico",): (b"\x00\x00\x01\x00",),
}

PIPE_TO_SHELL = re.compile(
    r"\b(curl|wget)\s[^\n|]*\|\s*(sudo\s+)?(\S*/)?(ba|z|da|k)?sh\b|"
    r"\b(curl|wget)\s[^\n|]*\|\s*(sudo\s+)?(\S*/)?python[0-9.]*\b"
)
SPOOF_NAME = re.compile(
    rf"^\s*(-\s*)?name:\s*['\"]?{re.escape(CHECK_NAME)}['\"]?\s*(#.*)?$"
)
SPOOF_JOB = re.compile(rf"^\s+['\"]?{re.escape(CHECK_NAME)}['\"]?:\s*(#.*)?$")


@dataclass
class Issue:
    file: str
    line: int
    category: str
    title: str
    evidence: str = ""
    severity: str = "critical"


@dataclass
class PrecheckResult:
    failures: list[Issue] = field(default_factory=list)
    sensitive: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def strict(self) -> bool:
        return bool(self.sensitive)


def is_sensitive(path: str) -> bool:
    return any(fnmatchcase(path, pat) for pat in SENSITIVE)


def names_check(text: str) -> bool:
    """Mentions the check name other than as the gate's own directory path."""
    return CHECK_NAME in text.lower().replace(GATE_DIR.rstrip("/"), "")


def is_workflow(path: str) -> bool:
    return fnmatchcase(path, ".github/workflows/*.y*ml")


def lines(text: str) -> list[str]:
    """Split on newline only; str.splitlines also splits on U+2028/2029 and hides them."""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def added_lines(patch: str | None) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    new = 0
    for line in lines(patch or ""):
        if line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            new = int(m.group(1)) if m else 0
        elif line.startswith("+"):
            out.append((new, line[1:]))
            new += 1
        elif line.startswith("-") or line.startswith("\\"):
            continue
        else:
            new += 1
    return out


def hidden_chars(text: str) -> list[str]:
    return sorted({f"U+{ord(c):04X}" for c in text if ord(c) in HIDDEN_CHARS})


def decode(data: bytes | None) -> str | None:
    if data is None:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def image_ok(path: str, data: bytes) -> bool:
    lower = path.lower()
    for exts, magics in IMAGE_MAGIC.items():
        if lower.endswith(exts):
            return len(data) <= MAX_IMAGE_BYTES and data.startswith(magics)
    return False


def context_size(pr: PullRequest) -> int:
    total = len(pr.title) + len(pr.body) + sum(len(c) for c in pr.commits)
    for f in pr.files:
        total += len(f.patch or "")
        for blob in (f.head, f.base):
            text = decode(blob)
            if text is not None:
                total += len(text)
    return total


def run(pr: PullRequest) -> PrecheckResult:
    res = PrecheckResult()
    fail = res.failures.append

    if pr.oversize or len(pr.files) > MAX_FILES:
        fail(Issue("", 0, "size", f"more than {MAX_FILES} files changed; split the PR"))
        return res

    meta = [("PR title", pr.title), ("PR body", pr.body)]
    meta += [(f"commit {i + 1}", c) for i, c in enumerate(pr.commits)]
    for label, text in meta:
        found = hidden_chars(text)
        if found:
            fail(Issue(label, 0, "trojan_source", "hidden unicode", ", ".join(found)))

    diff_chars = 0
    for f in pr.files:
        path = f.path
        if is_sensitive(path) or (f.previous_path and is_sensitive(f.previous_path)):
            res.sensitive.append(path)

        found = hidden_chars(path + (f.previous_path or ""))
        if found:
            fail(
                Issue(
                    path, 0, "trojan_source", "hidden unicode in path", ", ".join(found)
                )
            )

        if path.startswith(FIXTURE_DIR) and not (
            path.endswith(".fx") or path.endswith("/meta.json")
        ):
            fail(
                Issue(
                    path,
                    0,
                    "build_hook",
                    "gate fixtures must be inert *.fx or meta.json",
                )
            )

        if f.status == "removed":
            diff_chars += len(f.patch or "")
            continue

        head_text = decode(f.head)
        if f.head is not None and head_text is None:
            if image_ok(path, f.head):
                res.notes.append(
                    f"{path}: image, {len(f.head)} bytes, not AI-reviewable"
                )
            else:
                fail(Issue(path, 0, "binary", "binary file added or changed"))
            continue

        if (f.patch is None and f.changes != 0) or (
            f.patch == "" and f.status == "added" and f.head
        ):
            fail(
                Issue(
                    path, 0, "size", "GitHub returned no diff (too large); split the PR"
                )
            )
            continue

        diff_chars += len(f.patch or "")
        if head_text is not None and len(head_text) > MAX_FILE_CHARS:
            fail(Issue(path, 0, "size", f"file larger than {MAX_FILE_CHARS} chars"))

        added = added_lines(f.patch)
        for lineno, text in added:
            found = hidden_chars(text)
            if found:
                fail(
                    Issue(
                        path,
                        lineno,
                        "trojan_source",
                        "bidi or zero-width character in added line",
                        ", ".join(found) + ": " + ascii(text)[:160],
                    )
                )

        if path in res.sensitive:
            for lineno, text in added:
                if PIPE_TO_SHELL.search(text):
                    fail(
                        Issue(
                            path,
                            lineno,
                            "ci_workflow",
                            "download piped to a shell",
                            text.strip()[:160],
                        )
                    )

        if is_workflow(path) and path != GATE_WORKFLOW:
            for lineno, text in added:
                if "pull_request_target" in text or "workflow_run" in text:
                    fail(
                        Issue(
                            path,
                            lineno,
                            "ci_workflow",
                            "pull_request_target or workflow_run added outside the gate",
                            text.strip()[:160],
                        )
                    )
            for lineno, text in enumerate(lines(head_text or ""), 1):
                if names_check(text) or SPOOF_NAME.match(text) or SPOOF_JOB.match(text):
                    fail(
                        Issue(
                            path,
                            lineno,
                            "ci_workflow",
                            f"defines a job or check named '{CHECK_NAME}' (status spoofing)",
                            text.strip()[:160],
                        )
                    )

    if diff_chars > MAX_DIFF_CHARS:
        fail(Issue("", 0, "size", f"diff over {MAX_DIFF_CHARS} chars; split the PR"))
    elif context_size(pr) > MAX_CONTEXT_CHARS:
        fail(
            Issue(
                "",
                0,
                "size",
                f"review context over {MAX_CONTEXT_CHARS} chars; split the PR",
            )
        )
    return res
