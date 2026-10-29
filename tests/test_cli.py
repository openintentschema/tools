import json
from importlib.metadata import version
from pathlib import Path

import pytest

from ois_tools.cli import main as ois
from ois_tools.validate_cli import EXIT_CONFORMS, EXIT_USAGE, EXIT_VIOLATION, main

EXAMPLES = Path(__file__).parent / "examples"
FIXTURES = Path(__file__).parent / "fixtures" / "valid"


def test_conforming_files(capsys: pytest.CaptureFixture[str]) -> None:
    names = ("level-1-bundle.json", "intent.json", "finding.json")
    files = [str(FIXTURES / name) for name in names]
    assert main(files) == EXIT_CONFORMS
    assert capsys.readouterr().out.splitlines() == [f"{name}: conforms" for name in files]


def test_violation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "record.json"
    path.write_text('{"type": "requirement"}', encoding="utf-8")
    assert main([str(path)]) == EXIT_VIOLATION
    assert capsys.readouterr().out.startswith(f"{path}: violation [schema] $.type: ")


def test_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    assert main([str(path), "--format", "json"]) == EXIT_VIOLATION
    [entry] = json.loads(capsys.readouterr().out)
    assert entry["conforms"] is False
    assert entry["reports"][0]["rule"] == "json"


def test_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "missing.json")]) == EXIT_USAGE
    assert "missing.json" in capsys.readouterr().err


def test_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    text = (EXAMPLES / "finding.json").read_text(encoding="utf-8")
    monkeypatch.setattr("sys.stdin.read", lambda: text)
    assert main(["-"]) == EXIT_CONFORMS
    assert capsys.readouterr().out == "-: conforms\n"


# --- the ois command --------------------------------------------------------------------------

COMMANDS = (
    "validate",
    "junit",
    "cucumber",
    "intent",
    "record",
    "passport",
    "coverage",
    "sign",
    "key",
    "trust",
)


def test_ois_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert ois(["--version"]) == 0
    assert capsys.readouterr().out.strip() == version("ois-tools")


def test_ois_lists_its_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert ois(["--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: ois <command>")
    for name in COMMANDS:
        assert f"\n  {name} " in out


def test_ois_without_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert ois([]) == EXIT_USAGE
    assert "usage: ois <command>" in capsys.readouterr().err


def test_ois_runs_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert ois(["validate", str(FIXTURES / "intent.json")]) == EXIT_CONFORMS
    assert capsys.readouterr().out.strip().endswith("intent.json: conforms")


def test_ois_refuses_an_unknown_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert ois(["validator", "x.json"]) == EXIT_USAGE
    assert "unknown command 'validator'" in capsys.readouterr().err


@pytest.mark.parametrize("name", COMMANDS)
def test_each_command_is_named_ois_command(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        ois([name, "--help"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith(f"usage: ois {name}")
