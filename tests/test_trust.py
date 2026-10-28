"""ois trust update fetches a Sigstore trusted root through TUF. The tests replace the network
fetch with the frozen public-good root; one real fetch is checked by hand before releases."""

import json
from pathlib import Path
from typing import Any

import pytest

from ois_tools import trust_cli
from ois_tools.cli import main as ois
from ois_tools.keyless import load_trusted_roots

HERE = Path(__file__).parent
ROOT = HERE / "sigstore" / "public-good.trusted_root.json"
BUNDLE = HERE / "sigstore" / "ois-tools-0.6.0-image.sigstore.json"


class _Inner:
    def to_json(self) -> str:
        return ROOT.read_text(encoding="utf-8")


class _Config:
    class trusted_root:
        _inner = _Inner()


def test_update_writes_a_trusted_root_that_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []

    def fetch(url: str, offline: bool = False, bootstrap_root: Path | None = None) -> _Config:
        calls.append({"url": url, "offline": offline, "bootstrap_root": bootstrap_root})
        return _Config()

    monkeypatch.setattr(trust_cli, "_from_tuf", fetch)
    out = tmp_path / "trusted_root.json"
    assert ois(["trust", "update", "--output", str(out)]) == 0
    assert calls == [{"url": trust_cli.PUBLIC_GOOD, "offline": False, "bootstrap_root": None}]
    assert "certificate authorit" in capsys.readouterr().err
    assert json.loads(out.read_text())["mediaType"].startswith("application/vnd.dev.sigstore")
    assert len(load_trusted_roots([str(out)])) == 1
    assert ois(["validate", str(BUNDLE), "--trusted-root", str(out)]) == 1  # SLSA, not OIS
    assert "[signature]" not in capsys.readouterr().out


def test_an_own_instance_needs_its_root_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fetch(url: str, offline: bool = False, bootstrap_root: Path | None = None) -> _Config:
        calls.append((url, bootstrap_root))
        return _Config()

    monkeypatch.setattr(trust_cli, "_from_tuf", fetch)
    bootstrap = tmp_path / "root.json"
    bootstrap.write_text("{}", encoding="utf-8")
    out = tmp_path / "acme.trusted_root.json"
    args = ["trust", "update", "--tuf-url", "https://tuf.acme.example", "--tuf-root"]
    assert ois([*args, str(bootstrap), "--output", str(out)]) == 0
    assert calls == [("https://tuf.acme.example", bootstrap)]


def test_a_failed_fetch_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fetch(url: str, offline: bool = False, bootstrap_root: Path | None = None) -> _Config:
        raise OSError("network unreachable")

    monkeypatch.setattr(trust_cli, "_from_tuf", fetch)
    out = tmp_path / "trusted_root.json"
    assert ois(["trust", "update", "--output", str(out)]) == 2
    assert "network unreachable" in capsys.readouterr().err
    assert not out.exists()
