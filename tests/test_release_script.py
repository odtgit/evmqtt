"""Unit tests for scripts/release.py, against a throwaway git repo fixture."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "release.py"
spec = importlib.util.spec_from_file_location("release", SCRIPT_PATH)
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
sys.modules["release"] = release
spec.loader.exec_module(release)


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(cwd: Path, message: str, *, body: str = "", allow_empty: bool = True) -> str:
    full = message if not body else f"{message}\n\n{body}"
    args = ["commit", "-m", full]
    if allow_empty:
        args.append("--allow-empty")
    git(cwd, *args)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    git(cwd, "init", "-q", "-b", "master")
    git(cwd, "config", "user.email", "test@example.com")
    git(cwd, "config", "user.name", "Test")
    (cwd / "pyproject.toml").write_text(
        '[project]\nname = "evmqtt"\nversion = "1.0.0"\n'
    )
    (cwd / "config.yaml").write_text('name: "evmqtt"\nversion: "1.0.0"\n')
    git(cwd, "add", "-A")
    commit(cwd, "chore: initial", allow_empty=False)
    git(cwd, "tag", "v1.0.0")
    return cwd


def test_no_release_when_only_chore_commits(repo: Path) -> None:
    commit(repo, "ci: tweak workflow")
    commit(repo, "docs: update readme")
    assert release.cmd_next_version(repo) is None


def test_feat_commit_gives_minor_bump(repo: Path) -> None:
    commit(repo, "feat: add new widget")
    assert release.cmd_next_version(repo) == "1.1.0"


def test_fix_commit_gives_patch_bump(repo: Path) -> None:
    commit(repo, "fix: correct off-by-one")
    assert release.cmd_next_version(repo) == "1.0.1"


def test_perf_commit_gives_patch_bump(repo: Path) -> None:
    commit(repo, "perf: speed up loop")
    assert release.cmd_next_version(repo) == "1.0.1"


def test_bang_marks_breaking_major_bump(repo: Path) -> None:
    commit(repo, "feat!: remove legacy option")
    assert release.cmd_next_version(repo) == "2.0.0"


def test_breaking_change_footer_is_major(repo: Path) -> None:
    commit(repo, "fix: change default", body="BREAKING CHANGE: defaults now differ")
    assert release.cmd_next_version(repo) == "2.0.0"


def test_highest_bump_wins_across_commits(repo: Path) -> None:
    commit(repo, "fix: small bug")
    commit(repo, "feat: new capability")
    commit(repo, "docs: mention it")
    assert release.cmd_next_version(repo) == "1.1.0"


def test_release_commits_are_skipped(repo: Path) -> None:
    commit(repo, "release: 1.0.1 [skip ci]")
    assert release.cmd_next_version(repo) is None


def test_no_commits_since_tag_gives_no_release(repo: Path) -> None:
    assert release.cmd_next_version(repo) is None


def test_merge_commit_subject_itself_is_not_classified(repo: Path) -> None:
    # A "Merge pull request" subject looks like nothing conventional; ensure
    # the merge is resolved via its branch commits, not misclassified itself.
    git(repo, "checkout", "-b", "feature/x")
    commit(repo, "feat: branch feature")
    git(repo, "checkout", "master")
    git(
        repo,
        "merge",
        "--no-ff",
        "-m",
        "Merge pull request #7 from odtgit/feature/x",
        "feature/x",
    )
    assert release.cmd_next_version(repo) == "1.1.0"


def test_merge_commit_analyses_non_merge_commits_of_branch(repo: Path) -> None:
    git(repo, "checkout", "-b", "feature/y")
    commit(repo, "fix: branch fix one")
    commit(repo, "fix: branch fix two")
    git(repo, "checkout", "master")
    git(
        repo,
        "merge",
        "--no-ff",
        "-m",
        "Merge pull request #8 from odtgit/feature/y",
        "feature/y",
    )
    assert release.cmd_next_version(repo) == "1.0.1"


def test_merge_commit_breaking_change_in_branch_is_major(repo: Path) -> None:
    git(repo, "checkout", "-b", "feature/z")
    commit(repo, "feat!: break the api")
    git(repo, "checkout", "master")
    git(
        repo,
        "merge",
        "--no-ff",
        "-m",
        "Merge pull request #9 from odtgit/feature/z",
        "feature/z",
    )
    assert release.cmd_next_version(repo) == "2.0.0"


def test_apply_updates_pyproject_and_config_yaml(repo: Path) -> None:
    commit(repo, "feat: add new widget")
    release.apply_version("1.1.0", repo)
    assert 'version = "1.1.0"' in (repo / "pyproject.toml").read_text()
    assert 'version: "1.1.0"' in (repo / "config.yaml").read_text()


def test_apply_skips_manifest_when_absent(repo: Path) -> None:
    commit(repo, "feat: add new widget")
    changed = release.apply_version("1.1.0", repo)
    assert not (repo / "custom_components").exists()
    assert all("manifest.json" not in str(p) for p in changed)


def test_apply_updates_manifest_when_present(repo: Path) -> None:
    manifest_dir = repo / "custom_components" / "evmqtt"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        '{\n  "domain": "evmqtt",\n  "version": "1.0.0",\n'
        '  "requirements": ["evmqtt==1.0.0"]\n}\n'
    )
    commit(repo, "feat: add new widget")
    release.apply_version("1.1.0", repo)
    text = (manifest_dir / "manifest.json").read_text()
    assert '"version": "1.1.0"' in text
    assert '"evmqtt==1.1.0"' in text
    assert '"domain": "evmqtt"' in text


def test_apply_creates_changelog_grouped_by_type(repo: Path) -> None:
    commit(repo, "feat!: breaking change here")
    commit(repo, "feat: a feature")
    commit(repo, "fix: a fix")
    commit(repo, "docs: irrelevant to changelog")
    release.apply_version("2.0.0", repo)
    text = (repo / "CHANGELOG.md").read_text()
    assert text.startswith("# Changelog")
    assert "## 2.0.0" in text
    assert "### Breaking" in text
    assert "### Features" in text
    assert "### Fixes" in text
    assert "breaking change here" in text
    assert "a feature" in text
    assert "a fix" in text
    assert "irrelevant to changelog" not in text


def test_apply_prepends_to_existing_changelog(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 1.0.0\n\n### Features\n- initial\n"
    )
    commit(repo, "fix: a fix")
    release.apply_version("1.0.1", repo)
    text = (repo / "CHANGELOG.md").read_text()
    assert text.index("## 1.0.1") < text.index("## 1.0.0")


def test_apply_writes_release_notes_file(repo: Path) -> None:
    commit(repo, "feat: a feature")
    release.apply_version("1.1.0", repo)
    notes = (repo / "RELEASE_NOTES.md").read_text()
    assert "## 1.1.0" in notes
    assert "a feature" in notes


def test_apply_pr_number_from_squash_commit_subject(repo: Path) -> None:
    commit(repo, "feat: add thing (#42)")
    release.apply_version("1.1.0", repo)
    text = (repo / "CHANGELOG.md").read_text()
    assert "(#42)" in text
    assert "add thing (#42) (#42)" not in text


def test_apply_pr_number_from_merge_commit_attributed_to_branch_commits(
    repo: Path,
) -> None:
    git(repo, "checkout", "-b", "feature/w")
    commit(repo, "feat: branch feature")
    git(repo, "checkout", "master")
    git(
        repo,
        "merge",
        "--no-ff",
        "-m",
        "Merge pull request #11 from odtgit/feature/w",
        "feature/w",
    )
    release.apply_version("1.1.0", repo)
    text = (repo / "CHANGELOG.md").read_text()
    assert "branch feature (#11)" in text


def test_apply_raises_when_check_version_fails(repo: Path) -> None:
    # config.yaml goes missing, so pyproject.toml and config.yaml can no
    # longer agree once apply updates only the file that still exists.
    (repo / "config.yaml").unlink()
    commit(repo, "chore: drop config yaml")
    with pytest.raises(subprocess.CalledProcessError):
        release.apply_version("1.1.0", repo)


def test_forced_version_ignores_commit_history(repo: Path) -> None:
    commit(repo, "docs: irrelevant")
    assert release.cmd_forced_version("minor", repo) == "1.1.0"
    assert release.cmd_forced_version("major", repo) == "2.0.0"
    assert release.cmd_forced_version("patch", repo) == "1.0.1"


def test_bump_version_arithmetic() -> None:
    assert release.bump_version("1.2.3", "patch") == "1.2.4"
    assert release.bump_version("1.2.3", "minor") == "1.3.0"
    assert release.bump_version("1.2.3", "major") == "2.0.0"


def test_classify_non_conventional_subject_is_none() -> None:
    assert release.classify("update stuff", "") == "none"


def test_classify_chore_and_ci_and_build_are_none() -> None:
    for prefix in ("chore", "ci", "build", "refactor", "style", "test", "release"):
        assert release.classify(f"{prefix}: something", "") == "none"


def test_apply_rejects_non_numeric_version(repo: Path) -> None:
    with pytest.raises(ValueError):
        release.apply_version("1.2.3-rc1", repo)
    with pytest.raises(ValueError):
        release.apply_version("not-a-version", repo)


def test_last_tag_skips_non_numeric_tags(repo: Path) -> None:
    commit(repo, "feat: prerelease work")
    git(repo, "tag", "v1.1.0-rc1")
    assert release.last_tag(repo) == "v1.0.0"
    assert release.cmd_next_version(repo) == "1.1.0"


def test_last_tag_picks_highest_semver_not_nearest(repo: Path) -> None:
    git(repo, "tag", "v0.9.0")
    assert release.last_tag(repo) == "v1.0.0"


def test_sanitize_strips_images_and_html_and_escapes_mentions() -> None:
    text = release.sanitize_text(
        "see ![screenshot](http://x/y.png) <script>alert(1)</script> cc @someone"
    )
    assert "![" not in text
    assert "<script>" not in text
    assert "`@someone`" in text
    assert " cc @someone" not in text


def test_changelog_entry_sanitized(repo: Path) -> None:
    commit(repo, "feat: fix rendering for @someone <b>bold</b>")
    release.apply_version("1.1.0", repo)
    text = (repo / "CHANGELOG.md").read_text()
    assert "`@someone`" in text
    assert "<b>" not in text


def test_pending_tag_version_none_when_head_is_not_release_commit(repo: Path) -> None:
    commit(repo, "feat: something")
    assert release.pending_tag_version(repo) is None


def test_pending_tag_version_detects_untagged_release_commit(repo: Path) -> None:
    git(repo, "config", "user.email", release.RELEASE_IDENTITY_EMAIL)
    git(repo, "config", "user.name", release.RELEASE_IDENTITY_NAME)
    commit(repo, "release: 1.1.0 [skip ci]")
    assert release.pending_tag_version(repo) == "1.1.0"


def test_pending_tag_version_none_once_tagged(repo: Path) -> None:
    git(repo, "config", "user.email", release.RELEASE_IDENTITY_EMAIL)
    git(repo, "config", "user.name", release.RELEASE_IDENTITY_NAME)
    commit(repo, "release: 1.1.0 [skip ci]")
    git(repo, "tag", "v1.1.0")
    assert release.pending_tag_version(repo) is None


def test_pending_tag_version_ignores_release_commit_from_other_author(
    repo: Path,
) -> None:
    commit(repo, "release: 1.1.0 [skip ci]")
    assert release.pending_tag_version(repo) is None


def test_is_release_commit_true_for_matching_identity_and_version(repo: Path) -> None:
    git(repo, "config", "user.email", release.RELEASE_IDENTITY_EMAIL)
    git(repo, "config", "user.name", release.RELEASE_IDENTITY_NAME)
    sha = commit(repo, "release: 1.1.0 [skip ci]")
    assert release.is_release_commit(sha, "1.1.0", repo) is True
    assert release.is_release_commit(sha, "1.2.0", repo) is False


def test_is_release_commit_false_for_wrong_author(repo: Path) -> None:
    sha = commit(repo, "release: 1.1.0 [skip ci]")
    assert release.is_release_commit(sha, "1.1.0", repo) is False


def test_is_release_commit_false_for_non_release_subject(repo: Path) -> None:
    git(repo, "config", "user.email", release.RELEASE_IDENTITY_EMAIL)
    git(repo, "config", "user.name", release.RELEASE_IDENTITY_NAME)
    sha = commit(repo, "feat: not a release commit")
    assert release.is_release_commit(sha, "1.1.0", repo) is False
