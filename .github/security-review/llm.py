"""Two-pass review through the Claude Code CLI in print mode with every tool disabled."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import policy

if TYPE_CHECKING:
    from sources import PullRequest

MODEL = "claude-opus-5-5"
EFFORT = "high"
TIMEOUT = 1500
PROMPTS = Path(__file__).parent / "prompts"
ALLOWED_TOOLS = {"StructuredOutput"}

SEVERITIES = ["critical", "high", "medium", "low", "info"]
CATEGORIES = [
    "network_exfiltration",
    "keystroke_logging",
    "code_execution",
    "obfuscation",
    "secret_or_file_access",
    "dependency",
    "ci_workflow",
    "container_image",
    "build_hook",
    "release_process",
    "trojan_source",
    "prompt_injection",
    "privilege",
    "other",
]

FINDING = {
    "type": "object",
    "properties": {
        "file": {"type": "string"},
        "line": {"type": "integer"},
        "category": {"type": "string", "enum": CATEGORIES},
        "severity": {"type": "string", "enum": SEVERITIES},
        "title": {"type": "string"},
        "evidence": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": [
        "file",
        "line",
        "category",
        "severity",
        "title",
        "evidence",
        "rationale",
    ],
    "additionalProperties": False,
}
PASS1_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": FINDING},
    },
    "required": ["summary", "findings"],
    "additionalProperties": False,
}
PASS2_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": ["confirmed", "refuted", "uncertain"],
                    },
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "reasoning": {"type": "string"},
                },
                "required": ["index", "verdict", "severity", "reasoning"],
                "additionalProperties": False,
            },
        },
        "new_findings": {"type": "array", "items": FINDING},
    },
    "required": ["summary", "assessments", "new_findings"],
    "additionalProperties": False,
}


class LLMError(Exception):
    pass


def validate(value: Any, schema: dict[str, Any], where: str = "$") -> None:
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise LLMError(f"{where}: expected object")
        props = schema["properties"]
        missing = set(schema.get("required", [])) - set(value)
        extra = set(value) - set(props)
        if missing or extra:
            raise LLMError(f"{where}: missing {sorted(missing)} extra {sorted(extra)}")
        for key, sub in props.items():
            if key in value:
                validate(value[key], sub, f"{where}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise LLMError(f"{where}: expected array")
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{where}[{i}]")
    elif kind == "string":
        if not isinstance(value, str):
            raise LLMError(f"{where}: expected string")
        if "enum" in schema and value not in schema["enum"]:
            raise LLMError(f"{where}: {value!r} not allowed")
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise LLMError(f"{where}: expected integer")


def numbered(text: str) -> str:
    return "\n".join(f"{i:>5}| {line}" for i, line in enumerate(policy.lines(text), 1))


def annotated_patch(patch: str) -> str:
    out, new = [], 0
    for line in policy.lines(patch):
        if line.startswith("@@"):
            parts = line.split("+", 1)[1].split(" ", 1)[0].split(",")
            new = int(parts[0] or 0)
            out.append(line)
        elif line.startswith("+"):
            out.append(f"{new:>5} +{line[1:]}")
            new += 1
        elif line.startswith("-"):
            out.append(f"{'':>5} -{line[1:]}")
        elif line.startswith("\\"):
            out.append(line)
        else:
            out.append(f"{new:>5}  {line[1:]}")
            new += 1
    return "\n".join(out)


def build_context(pr: PullRequest, pre: policy.PrecheckResult, nonce: str) -> str:
    tag = f"pr_data_{nonce}"
    parts = [
        f"<{tag}>",
        "## PR metadata",
        f"repository: {pr.repo}",
        f"number: {pr.number}",
        f"author: {pr.author} ({pr.author_association})",
        f"head repository: {pr.head_repo}",
        f"base branch: {pr.base_ref}",
        f"title: {pr.title}",
        "body:",
        pr.body,
        "commit messages:",
        *[f"- {c}" for c in pr.commits],
        "",
        "## Changed files",
    ]
    for f in pr.files:
        mark = " [SENSITIVE]" if f.path in pre.sensitive else ""
        prev = f" (from {f.previous_path})" if f.previous_path else ""
        parts.append(f"- {f.status} {f.path}{prev}{mark}")
    parts.append("\n## Diff (post-change line numbers on the left)")
    for f in pr.files:
        parts.append(f"\n### {f.path} ({f.status})")
        parts.append(annotated_patch(f.patch) if f.patch else "[no textual diff]")
    parts.append("\n## Full post-change contents of changed files")
    for f in pr.files:
        if f.status == "removed":
            continue
        text = policy.decode(f.head)
        parts.append(f"\n### {f.path}")
        parts.append(numbered(text) if text is not None else "[binary, not shown]")
    if pre.sensitive:
        parts.append("\n## Pre-change contents of sensitive files")
        for f in pr.files:
            if f.path in pre.sensitive and f.base is not None:
                text = policy.decode(f.base)
                parts.append(f"\n### {f.previous_path or f.path} (before)")
                parts.append(numbered(text) if text is not None else "[binary]")
    parts.append(f"</{tag}>")
    return "\n".join(parts)


def mode_line(pre: policy.PrecheckResult) -> str:
    if pre.strict:
        return "MODE: STRICT. Sensitive files changed: " + ", ".join(pre.sensitive)
    return "MODE: NORMAL."


def system_prompt(name: str, nonce: str) -> str:
    return (PROMPTS / f"{name}.md").read_text().replace("NONCE", nonce)


def pass1_prompt(context: str, pre: policy.PrecheckResult) -> str:
    return (
        f"{context}\n\n{mode_line(pre)}\n"
        "Task: review the pull request above as instructed in your system prompt "
        "and return the structured result."
    )


def pass2_prompt(
    context: str, pre: policy.PrecheckResult, p1: dict[str, Any], nonce: str
) -> str:
    indexed = [{"index": i, **f} for i, f in enumerate(p1["findings"])]
    return (
        f"{context}\n\n<pass1_findings_{nonce}>\n{json.dumps(indexed, indent=1)}\n"
        f"</pass1_findings_{nonce}>\n\n{mode_line(pre)}\n"
        "Task: verify every candidate finding by index, then hunt for missed issues, "
        "as instructed in your system prompt, and return the structured result."
    )


def claude_cmd(binary: str, system: str, schema: dict[str, Any]) -> list[str]:
    return [
        binary,
        "-p",
        "--model",
        MODEL,
        "--effort",
        EFFORT,
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--max-turns",
        "1",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(schema),
        "--system-prompt",
        system,
    ]


def parse_stream(
    stdout: str, schema: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    init_seen = False
    result: dict[str, Any] | None = None
    for raw in stdout.split("\n"):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            raise LLMError("non-JSON line in CLI output") from None
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            tools = set(event.get("tools") or [])
            if not tools <= ALLOWED_TOOLS or event.get("mcp_servers"):
                raise LLMError(f"tool isolation failed: tools={sorted(tools)}")
            init_seen = True
        elif kind == "assistant":
            for block in event.get("message", {}).get("content", []):
                if (
                    block.get("type") == "tool_use"
                    and block.get("name") not in ALLOWED_TOOLS
                ):
                    raise LLMError(f"model invoked tool {block.get('name')!r}")
        elif kind == "result":
            result = event
    if not init_seen:
        raise LLMError("CLI did not report its tool set")
    if result is None:
        raise LLMError("CLI produced no result")
    if result.get("is_error") or result.get("subtype") != "success":
        raise LLMError(
            f"CLI error: {result.get('subtype')} api_status={result.get('api_error_status')}"
        )
    models = set((result.get("modelUsage") or {}).keys())
    if models != {MODEL}:
        raise LLMError(f"unexpected model(s) {sorted(models)}")
    out = result.get("structured_output")
    if out is None:
        raise LLMError("no structured output (refusal or malformed)")
    validate(out, schema)
    meta = {
        "cost_usd": result.get("total_cost_usd"),
        "duration_ms": result.get("duration_ms"),
        "usage": {
            k: (result.get("usage") or {}).get(k)
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens")
        },
    }
    return out, meta


def run_claude(
    system: str, prompt: str, schema: dict[str, Any], *, binary: str, token: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="secreview-") as home:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": home,
            "LANG": "C.UTF-8",
            "CLAUDE_CODE_OAUTH_TOKEN": token,
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
        }
        try:
            proc = subprocess.run(
                claude_cmd(binary, system, schema),
                input=prompt,
                capture_output=True,
                text=True,
                env=env,
                cwd=home,
                timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise LLMError(f"CLI timed out after {TIMEOUT}s") from None
        except OSError as err:
            raise LLMError(f"cannot run CLI: {err.strerror}") from None
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-300:]
        try:
            parse_stream(proc.stdout, schema)
        except LLMError as err:
            raise LLMError(f"CLI exit {proc.returncode}: {err}; {tail}") from None
        raise LLMError(f"CLI exit {proc.returncode}: {tail}")
    return parse_stream(proc.stdout, schema)
