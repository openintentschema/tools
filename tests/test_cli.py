import json
from importlib.metadata import version
from pathlib import Path

import pytest

from ois_validator.cli import EXIT_CONFORMS, EXIT_USAGE, EXIT_VIOLATION, main

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


@pytest.mark.parametrize("entry", ["ois_validator.cli", "ois_validator.junit_cli"])
def test_version(entry: str, capsys: pytest.CaptureFixture[str]) -> None:
    module = __import__(entry, fromlist=["main"])
    with pytest.raises(SystemExit) as exit_info:
        module.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == version("ois-validator")
