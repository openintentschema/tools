"""The GitHub Action and the GitLab CI template install the same version as the package."""

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
ACTION = ROOT / ".github" / "actions" / "setup" / "action.yml"
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_action_installs_this_version() -> None:
    action = load(ACTION)
    assert action["runs"]["using"] == "composite"
    assert action["inputs"]["version"]["default"] == VERSION
    assert "ois-version" in action["outputs"]


def test_the_action_pins_what_it_uses() -> None:
    action = load(ACTION)
    used = [step["uses"] for step in action["runs"]["steps"] if "uses" in step]
    assert used and all(PINNED.match(ref) for ref in used)
    text = ACTION.read_text(encoding="utf-8")
    assert all(re.search(rf"{re.escape(ref)} # v\d", text) for ref in used)


def test_the_gitlab_template_uses_the_image_of_this_version() -> None:
    template = load(ROOT / "ci" / "gitlab" / "ois.gitlab-ci.yml")
    image = template[".ois"]["image"]
    assert image["name"] == f"ghcr.io/openintentschema/ois-tools:{VERSION}"
    assert image["entrypoint"] == [""]
    junit = template[".ois-junit"]
    assert junit["extends"] == ".ois"
    assert any("ois junit" in line for line in junit["script"])
    assert junit["artifacts"]["when"] == "always"
