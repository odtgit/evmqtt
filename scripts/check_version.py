#!/usr/bin/env python3
"""Check that pyproject.toml, config.yaml and (optionally) a git tag agree on version."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

_root_override = os.environ.get("EVMQTT_CHECK_VERSION_ROOT", "")
ROOT = (
    Path(_root_override) if _root_override else Path(__file__).resolve().parent.parent
)


def pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def config_yaml_version() -> str:
    text = (ROOT / "config.yaml").read_text()
    match = re.search(r'^version:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
    if not match:
        raise ValueError("no version field found in config.yaml")
    return match.group(1).strip()


def ref_tag_version() -> str | None:
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/"):
        return ref.removeprefix("refs/tags/")
    return None


def main(argv: list[str]) -> int:
    py_version = pyproject_version()
    yaml_version = config_yaml_version()

    if py_version != yaml_version:
        print(
            f"version mismatch: pyproject.toml={py_version} config.yaml={yaml_version}",
            file=sys.stderr,
        )
        return 1

    tag = argv[1] if len(argv) > 1 else ref_tag_version()
    if tag is not None:
        tag_version = tag.removeprefix("v")
        if tag_version != py_version:
            print(
                f"version mismatch: pyproject.toml={py_version} tag={tag_version}",
                file=sys.stderr,
            )
            return 1

    print(f"version check OK: {py_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
