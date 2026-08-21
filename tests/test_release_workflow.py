"""The release workflow, checked for the things that go stale silently.

A release job runs once per release, on a tag, in CI — the worst possible place
to discover a wrong flag or a version number nobody updated. The job this
replaced carried "Retail,12.0.5" hardcoded and was two patches out of date
before it was ever switched on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML parses the workflow")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TOC = ROOT / "addon" / "BabelChat" / "BabelChat.toc"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def publish_step(workflow: dict) -> dict:
    for step in workflow["jobs"]["publish-addon"]["steps"]:
        if str(step.get("uses", "")).startswith("BigWigsMods/packager"):
            return step
    raise AssertionError("the publishing step is gone")


# ── nothing that duplicates the TOC ──────────────────────────────────────────


def test_the_workflow_states_no_game_version_of_its_own():
    """The interface versions live in the TOC, which has to be right for the
    addon to load at all. A second copy in the workflow is a copy that goes
    stale without anything failing."""
    text = WORKFLOW.read_text(encoding="utf-8")

    versions = re.findall(r"\b1[12]\.\d+\.\d+\b|\b\d{6}\b", text)

    assert versions == [], f"the workflow hardcodes a game version: {versions}"


def test_the_workflow_states_no_project_id_of_its_own():
    """Same reason, and a placeholder id is worse than none: it looks filled in.
    The job this replaced said `project_id: YOUR_PROJECT_ID_HERE`."""
    text = WORKFLOW.read_text(encoding="utf-8").lower()

    assert "your_project_id" not in text
    assert "project_id:" not in text


def test_the_toc_is_where_the_project_ids_are_written_down():
    """Whether or not they are filled in yet, the TOC has to say that this is
    where they go — otherwise the next person hunts through the workflow."""
    text = TOC.read_text(encoding="utf-8")

    assert "X-Curse-Project-ID" in text
    assert "X-Wago-ID" in text


# ── the flags, which are only exercised on a tag ─────────────────────────────


def test_the_packager_runs_from_the_repository_root(workflow):
    """`-t addon/BabelChat` looks reasonable and fails every time: the packager
    checks for a git checkout at topdir before anything else, and the addon
    subdirectory has no .git. Topdir has to be the root, which is also where
    .pkgmeta lives — and .pkgmeta is what tells it where the TOC is.

    Verified by running the real release.sh with both settings.
    """
    args = publish_step(workflow)["with"]["args"]

    assert "-t " not in args, f"topdir must stay at the repository root: {args}"
    assert (ROOT / ".git").exists()
    assert (ROOT / ".pkgmeta").is_file()


def test_the_addon_is_staged_rather_than_copied_by_the_packager(workflow):
    """The packager's own copy step works from an ignore list, and this is not
    an addon-only repository: the list was twelve entries out of date and would
    have uploaded the developer's config.json — API key included — logs, a
    video and the translation cache to both stores, publicly and permanently.

    -c packages what the workflow staged. -o is not optional alongside it:
    without it the packager deletes the package directory first, staged contents
    and all, and produces an empty zip that uploads without complaint.
    """
    steps = workflow["jobs"]["publish-addon"]["steps"]
    staging = next((s for s in steps if "Stage" in str(s.get("name", ""))), None)
    args = publish_step(workflow)["with"]["args"]

    assert staging is not None, "nothing stages the addon"
    assert "addon/BabelChat/." in staging["run"], staging["run"]
    assert "-c" in args.split(), args
    assert "-o" in args.split(), "-c without -o empties the package directory"


def test_the_pkgmeta_points_the_packager_at_the_toc():
    """The packager finds the TOC at the repository root or at the path named in
    move-folders — and that key is prefixed with the package name. Written as
    `addon/BabelChat` it strips the wrong component and reports "Could not find
    an addon TOC file"."""
    import yaml as yaml_module

    meta = yaml_module.safe_load((ROOT / ".pkgmeta").read_text(encoding="utf-8"))

    assert meta["package-as"] == "BabelChat"
    assert meta["move-folders"] == {"BabelChat/addon/BabelChat": "BabelChat"}
    assert "ignore" not in meta, "an ignore list that -c never reads reads as authoritative"


def test_the_store_notes_are_one_release_not_the_whole_changelog(workflow):
    """`manual-changelog: CHANGELOG.md` posts every version ever released as the
    release notes for this one."""
    import yaml as yaml_module

    meta = yaml_module.safe_load((ROOT / ".pkgmeta").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish-addon"]["steps"]
    notes = next((s for s in steps if "notes" in str(s.get("name", "")).lower()), None)

    assert meta["manual-changelog"]["filename"] == "RELEASE_NOTES.md"
    assert notes is not None, "nothing writes the release notes"
    assert "CHANGELOG.md" in notes["run"] and "awk" in notes["run"]


def test_the_generated_release_artefacts_are_not_committed():
    """Both are built during the release and one of them is a copy of the addon;
    neither belongs in the repository."""
    import subprocess

    for name in (".release", "RELEASE_NOTES.md"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", name], cwd=ROOT, capture_output=True, check=False
        )
        assert result.returncode == 0, f"{name} is not ignored"


def test_the_upload_is_skipped_when_no_token_is_configured(workflow):
    """A fork, or this repository before the projects exist, still runs the job
    — it proves the addon packages cleanly and uploads nothing."""
    args = publish_step(workflow)["with"]["args"]

    assert "-d" in args, "nothing switches the upload off"
    assert "CF_API_TOKEN == ''" in args and "WAGO_API_TOKEN == ''" in args, args


def test_the_environment_uses_the_variable_names_the_packager_reads(workflow):
    """`CF_API_KEY` is the name of a different tool's variable. Getting this
    wrong means an upload that silently does not happen."""
    env = publish_step(workflow)["env"]

    assert set(env) == {"CF_API_TOKEN", "WAGO_API_TOKEN"}, env
    assert "GITHUB_API_TOKEN" not in env, "the release job already makes the GitHub release"


def test_the_checkout_is_deep_enough_for_a_changelog(workflow):
    """The packager builds the changelog from the commits since the previous
    tag, and a shallow clone has neither."""
    checkout = workflow["jobs"]["publish-addon"]["steps"][0]

    assert checkout["with"]["fetch-depth"] == 0


# ── and the parts that were not asked to change ──────────────────────────────


def test_the_companion_still_builds_for_both_platforms(workflow):
    """Addon publishing was the change; the companion app builds are not part of
    it and must not have been disturbed."""
    jobs = workflow["jobs"]

    assert jobs["build"]["runs-on"] == "windows-latest"
    assert jobs["build-linux"]["runs-on"].startswith("ubuntu")
    assert set(jobs["release"]["needs"]) == {"build", "build-linux"}


def test_publishing_the_addon_does_not_wait_on_the_linux_build(workflow):
    """The addon is platform-independent; making its upload wait on an AppImage
    means a Linux build failure blocks the addon release for everyone."""
    assert workflow["jobs"]["publish-addon"]["needs"] == "build"


# ── the tokens must not be able to reach the repository ──────────────────────


@pytest.mark.parametrize("name", [".cf", ".wago", "secret.token"])
def test_a_file_holding_a_token_is_ignored_by_git(name):
    """These are written to disk so a token never passes through a terminal or a
    chat log on the way to `gh secret set`. This repository is public."""
    import subprocess

    result = subprocess.run(
        ["git", "check-ignore", "-q", name], cwd=ROOT, capture_output=True, check=False
    )

    assert result.returncode == 0, f"{name} would be committed"


# ── one store page, not several ──────────────────────────────────────────────


def test_there_is_exactly_one_store_description():
    """A BBCode copy of the store page sat beside the Markdown one and drifted
    for a whole release: it still advertised the removed gloss format, "380+"
    terms and DeepL as the only provider. It escaped every check because the
    document tests look at Markdown.

    Both stores accept Markdown. One file, or the next copy rots the same way.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    descriptions = [
        name
        for name in tracked
        if "description" in name.lower() and not name.startswith(("docs/", "app/", "tests/"))
    ]

    assert descriptions == ["store-description.md"], f"more than one store page: {descriptions}"


# ── main only ────────────────────────────────────────────────────────────────


def test_publishing_refuses_a_tag_that_is_not_on_main(workflow):
    """A tag can be created on any commit, including one never merged.
    Publishing that puts a build in front of players that no review saw, and
    neither store lets you unpublish a file once uploaded."""
    steps = workflow["jobs"]["publish-addon"]["steps"]
    guard = next((s for s in steps if "main" in str(s.get("name", "")).lower()), None)

    assert guard is not None, "nothing checks which branch the tag is on"

    # Non-comment lines only: a substring check passes on a shell script that
    # merely mentions the command it no longer runs.
    executable = [line for line in guard["run"].splitlines() if not line.lstrip().startswith("#")]
    script = chr(10).join(executable)

    assert "merge-base --is-ancestor" in script, script
    assert "exit 1" in script, "the check does not fail the job"
    assert "if true" not in script, "the check has been short-circuited"
    # `git fetch --depth=0` is rejected by git, and the default shell is
    # `bash -e`, so a step containing it dies before the guard is reached.
    assert "--depth=0" not in script, "this fetch aborts the step it guards"

    order = [s.get("name") or s.get("uses") for s in steps]
    assert order.index(guard["name"]) < len(order) - 1, "the guard must run before the upload"


def test_the_branch_check_does_not_rely_on_base_ref(workflow):
    """`github.event.base_ref` reports the branch the tag was pushed alongside,
    which is whatever the pusher had checked out — not where the commit lives."""
    guard = next(
        s for s in workflow["jobs"]["publish-addon"]["steps"] if "main" in str(s.get("name", "")).lower()
    )

    assert "base_ref" not in guard["run"], "the guard executes base_ref"

    # It may be named in a comment — explaining why it is the wrong answer is
    # how the next person avoids reaching for it — but not in anything that runs.
    executable = [
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if "base_ref" in line and not line.lstrip().startswith("#")
    ]
    assert executable == [], f"base_ref is used, not just mentioned: {executable}"


def test_the_curseforge_project_id_is_in_the_toc_and_only_there():
    """It is read from the TOC, so a second copy anywhere else is a copy that
    can disagree."""
    toc = TOC.read_text(encoding="utf-8")
    project_ids = re.findall(r"^## X-Curse-Project-ID:\s*(\d+)\s*$", toc, re.M)

    assert project_ids == ["1491616"], f"expected one project id in the TOC, found {project_ids}"
    assert "1491616" not in WORKFLOW.read_text(encoding="utf-8")


def test_the_wago_project_id_is_in_the_toc_and_only_there():
    toc = TOC.read_text(encoding="utf-8")
    wago_ids = re.findall(r"^## X-Wago-ID:\s*(\S+)\s*$", toc, re.M)

    assert wago_ids == ["96d2BEGO"], f"expected one Wago id in the TOC, found {wago_ids}"
    assert "96d2BEGO" not in WORKFLOW.read_text(encoding="utf-8")


def test_both_store_ids_are_uncommented():
    """A `# ## X-Wago-ID:` line looks filled in at a glance and is invisible to
    the packager, which reads directives, not comments."""
    for directive in ("X-Curse-Project-ID", "X-Wago-ID"):
        live = [
            line
            for line in TOC.read_text(encoding="utf-8").splitlines()
            if directive in line and line.startswith("## ")
        ]
        assert len(live) == 1, f"{directive} is not a live directive: {live}"
