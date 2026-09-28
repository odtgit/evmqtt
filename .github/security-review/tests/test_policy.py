import policy
from sources import ChangedFile, PullRequest

RLO = chr(0x202E)
LRI = chr(0x2066)
ZWSP = chr(0x200B)
BOM = chr(0xFEFF)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def patch_for(lines, start=1):
    body = "\n".join("+" + line for line in lines)
    return f"@@ -0,0 +{start},{len(lines)} @@\n{body}"


def added(path, lines, **kw):
    text = "\n".join(lines) + "\n"
    return ChangedFile(
        path=path, status="added", patch=patch_for(lines), head=text.encode(), **kw
    )


def pr_with(*files, title="t", body="b"):
    return PullRequest(title=title, body=body, files=list(files))


def categories(res):
    return [(i.file, i.line, i.category) for i in res.failures]


def test_added_lines_track_new_numbers():
    patch = "@@ -3,4 +10,5 @@\n ctx\n-old\n+new1\n+new2\n ctx2\n\\ No newline at end of file"
    assert policy.added_lines(patch) == [(11, "new1"), (12, "new2")]


def test_added_lines_multiple_hunks():
    patch = "@@ -1,1 +1,2 @@\n a\n+b\n@@ -10,1 +20,2 @@\n c\n+d"
    assert policy.added_lines(patch) == [(2, "b"), (21, "d")]


def test_clean_change_passes_and_is_normal_mode():
    res = policy.run(pr_with(added("src/evmqtt/x.py", ["a = 1", "b = 2"])))
    assert res.failures == []
    assert not res.strict


def test_bidi_in_added_line_fails_with_location():
    res = policy.run(pr_with(added("src/a.py", ["ok = 1", f"# note {RLO}{LRI} x"])))
    assert categories(res) == [("src/a.py", 2, "trojan_source")]
    assert "U+202E" in res.failures[0].evidence and "U+2066" in res.failures[0].evidence
    assert RLO not in res.failures[0].evidence


def test_zero_width_and_bom_fail():
    res = policy.run(pr_with(added("a.py", [f"x{ZWSP}y = 1", f"{BOM}z = 2"])))
    assert [i.line for i in res.failures] == [1, 2]


def test_line_separator_fails():
    res = policy.run(pr_with(added("a.md", [f"a{chr(0x2028)}b"])))
    assert categories(res) == [("a.md", 1, "trojan_source")]


def test_hidden_char_in_removed_line_is_ignored():
    f = ChangedFile("a.py", "modified", f"@@ -1,1 +1,1 @@\n-x{RLO}\n+x", head=b"x\n")
    assert policy.run(pr_with(f)).failures == []


def test_hidden_char_in_title_or_path_fails():
    assert policy.run(pr_with(title=f"fix {RLO}")).failures
    assert policy.run(pr_with(added(f"a{ZWSP}.py", ["x"]))).failures


def test_binary_added_fails():
    f = ChangedFile("tools/helper.bin", "added", None, head=b"\x7fELF\x00\x01\x02")
    assert categories(policy.run(pr_with(f))) == [("tools/helper.bin", 0, "binary")]


def test_non_utf8_counts_as_binary():
    f = ChangedFile("a.txt", "modified", None, head=b"\xff\xfe\xfa")
    assert categories(policy.run(pr_with(f)))[0][2] == "binary"


def test_real_image_is_allowed_with_note():
    f = ChangedFile("custom_components/evmqtt/brand/icon.png", "added", None, head=PNG)
    res = policy.run(pr_with(f))
    assert res.failures == []
    assert res.notes


def test_image_with_wrong_magic_or_too_big_fails():
    fake = ChangedFile("icon.png", "added", None, head=b"MZ\x00\x00")
    big = ChangedFile(
        "big.png", "added", None, head=PNG + b"\x00" * policy.MAX_IMAGE_BYTES
    )
    assert len(policy.run(pr_with(fake, big)).failures) == 2


def test_missing_patch_on_text_file_fails_as_too_large():
    f = ChangedFile("src/huge.py", "modified", None, head=b"x = 1\n", changes=5000)
    assert categories(policy.run(pr_with(f))) == [("src/huge.py", 0, "size")]


def test_pure_rename_without_patch_is_fine():
    f = ChangedFile(
        "b.py", "renamed", None, head=b"x\n", previous_path="a.py", changes=0
    )
    assert policy.run(pr_with(f)).failures == []


def test_too_many_files_fails():
    files = [added(f"f{i}.txt", ["x"]) for i in range(policy.MAX_FILES + 1)]
    assert categories(policy.run(pr_with(*files))) == [("", 0, "size")]


def test_oversize_flag_fails():
    pr = pr_with()
    pr.oversize = True
    assert categories(policy.run(pr)) == [("", 0, "size")]


def test_diff_too_large_fails():
    lines = ["x" * 1000] * (policy.MAX_DIFF_CHARS // 1000 + 5)
    res = policy.run(pr_with(added("data.txt", lines)))
    assert ("", 0, "size") in categories(res)


def test_sensitive_paths_switch_to_strict():
    for path in (
        ".github/workflows/ci.yml",
        ".github/security-review/review.py",
        "Dockerfile",
        "pyproject.toml",
        "custom_components/evmqtt/manifest.json",
        "run.sh",
        "scripts/release.py",
        "compose.yaml",
        "config.yaml",
        "requirements.txt",
        "tests_ha/requirements.txt",
    ):
        res = policy.run(pr_with(added(path, ["x"])))
        assert res.strict, path
        assert res.failures == [], path


def test_ordinary_paths_are_not_sensitive():
    for path in (
        "src/evmqtt/gateway.py",
        "README.md",
        "tests/test_x.py",
        "custom_components/evmqtt/hub.py",
    ):
        assert not policy.is_sensitive(path), path


def test_rename_out_of_sensitive_path_is_sensitive():
    f = ChangedFile(
        "docs/old.txt",
        "renamed",
        "@@ -1 +1 @@\n-a\n+b",
        head=b"b\n",
        previous_path="run.sh",
    )
    assert policy.run(pr_with(f)).strict


def test_workflow_defining_gate_check_name_fails():
    wf = added(
        ".github/workflows/other.yml",
        ["jobs:", "  fake:", "    name: security-review", "    runs-on: x"],
    )
    job = added(
        ".github/workflows/o2.yml", ["jobs:", "  security-review:", "    runs-on: x"]
    )
    res = policy.run(pr_with(wf, job))
    assert {(i.file, i.line) for i in res.failures} == {
        (".github/workflows/other.yml", 3),
        (".github/workflows/o2.yml", 2),
    }


def test_gate_workflow_itself_may_name_the_check():
    wf = added(
        policy.GATE_WORKFLOW,
        [
            "on:",
            "  pull_request_target:",
            "jobs:",
            "  security-review:",
            "    name: security-review",
        ],
    )
    res = policy.run(pr_with(wf))
    assert res.failures == []
    assert res.strict


def test_pull_request_target_added_elsewhere_fails():
    wf = added(".github/workflows/ci.yml", ["on:", "  pull_request_target:"])
    assert categories(policy.run(pr_with(wf))) == [
        (".github/workflows/ci.yml", 2, "ci_workflow")
    ]


def test_pipe_to_shell_in_sensitive_file_fails_but_not_in_docs():
    line = " ".join(["RUN", "curl", "-fsSL", "https://example.invalid/i", "|", "sh"])
    assert policy.run(pr_with(added("Dockerfile", [line]))).failures
    assert policy.run(pr_with(added("README.md", [line]))).failures == []


def test_fixture_dir_accepts_only_inert_files():
    base = policy.FIXTURE_DIR + "x/"
    ok = policy.run(
        pr_with(added(base + "meta.json", ["{}"]), added(base + "after/a.py.fx", ["x"]))
    )
    assert ok.failures == []
    bad = policy.run(pr_with(added(base + "conftest.py", ["x"])))
    assert categories(bad) == [(base + "conftest.py", 0, "build_hook")]


def test_pipe_to_shell_mentioned_in_prose_is_not_a_command():
    assert policy.PIPE_TO_SHELL.search("flag curl|sh and wget|bash patterns") is None


def test_tag_characters_and_word_joiner_fail():
    res = policy.run(pr_with(added("a.md", [f"ok{chr(0xE0041)}", f"a{chr(0x2060)}b"])))
    assert [i.line for i in res.failures] == [1, 2]


def test_hidden_char_in_commit_message_fails():
    pr = pr_with()
    pr.commits = ["fine", f"sneaky {chr(0x200B)}"]
    assert categories(policy.run(pr)) == [("commit 2", 0, "trojan_source")]


def test_workflow_run_added_elsewhere_fails():
    wf = added(".github/workflows/ci.yml", ["on:", "  workflow_run:"])
    assert categories(policy.run(pr_with(wf)))[0][2] == "ci_workflow"


def test_any_mention_of_gate_check_in_other_workflow_fails():
    wf = added(
        ".github/workflows/x.yml", ["jobs:", "  a:", "    name: 'Security-Review'"]
    )
    assert policy.run(pr_with(wf)).failures
