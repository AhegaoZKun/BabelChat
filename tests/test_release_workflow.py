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


def test_the_packager_is_pointed_at_the_directory_holding_the_toc(workflow):
    """`-t` is the top-level directory of the checkout, and the packager expects
    the TOC at its root. This repository keeps the addon in a subdirectory, so
    the default — the repository root — finds nothing."""
    args = publish_step(workflow)["with"]["args"]

    assert "-t addon/BabelChat" in args, args
    assert (TOC.parent / TOC.name).exists()


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
