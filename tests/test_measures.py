"""ois measure. tests/measures/: real runs of k6 2.3.0 (--summary-export, and handleSummary with
custom metrics) and JMeter 5.6.3 (JTL CSV; half of the samples fail with 404)."""

import csv
import json
import shutil
import statistics
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.cli import main as ois
from ois_tools.intents import approve, seal
from ois_tools.measures import MeasureError, Metric, read, record_run

HERE = Path(__file__).parent / "measures"
NS = "https://ois.acme.example"
INTENT = f"{NS}/intents/INT-9"
PO = f"{NS}/actors/po-7f3a"
QE = f"{NS}/actors/qe-2c9d"
AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "billing"
    (root / "load").mkdir(parents=True)
    for name in ("checkout.js", "checkout.jmx"):
        shutil.copy(HERE / name, root / "load" / name)
    for args in (
        ["init", "-q"],
        ["remote", "add", "origin", "https://git.acme.example/billing.git"],
        ["add", "."],
        ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def approved(target: dict[str, Any] | None = None, with_decision: bool = False) -> Any:
    target = target or {"metric": "responseTimeP95", "operator": "lte", "value": 500, "unit": "ms"}
    draft = {
        "ois": "0.1",
        "type": "intent",
        "id": INTENT,
        "createdAt": "2026-10-29T08:00:00Z",
        "version": 1,
        "title": "Checkout stays fast",
        "status": "draft",
        "criteria": [
            {
                "key": "AC-3",
                "statement": "95 % of checkout requests answer within 500 ms",
                "notation": "text",
                "kind": "non-functional",
                "target": target,
            },
            {
                "key": "AC-4",
                "statement": "Checkout works",
                "notation": "text",
                "kind": "functional",
            },
        ],
    }
    intent, decision = approve(seal(draft), decided_by=PO, decided_at=AT)
    return (intent, decision) if with_decision else intent


def metric(name: str, spec: str) -> Metric:
    return read((HERE / name).read_bytes(), AT).metric(spec)


def run(repo: Path, name: str, measures: dict[str, str], **options: Any) -> Any:
    options.setdefault("intent", approved())
    return record_run(
        (HERE / name).read_bytes(),
        measures=measures,
        test=repo / "load" / ("checkout.jmx" if name.endswith(".jtl") else "checkout.js"),
        test_producer=QE,
        namespace=NS,
        written_at=AT,
        created_at=AT,
        **options,
    )


def evidence(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["type"] == "evidence"]


# --- Reading ------------------------------------------------------------------------------------


def test_k6_summary_export() -> None:
    p95 = metric("k6-summary-export.json", "http_req_duration:p(95)")
    assert p95 == Metric(5.975712799999998, "ms")
    assert metric("k6-summary-export.json", "http_req_failed:rate").value == 0.15
    assert metric("k6-summary-export.json", "http_reqs:count") == Metric(40, None)
    submetric = metric("k6-summary-export.json", "http_req_duration{expected_response:true}:p(95)")
    assert submetric.unit == "ms"


def test_k6_handle_summary() -> None:
    assert metric("k6-handle-summary.json", "checkout_duration:p(95)").unit == "ms"
    assert metric("k6-handle-summary.json", "checkout_failed:rate").value == 0.1
    assert metric("k6-handle-summary.json", "checkout_failed:value").value == 0.1


def test_jmeter_results() -> None:
    rows = list(csv.DictReader((HERE / "jmeter.jtl").open(encoding="utf-8")))
    elapsed = [int(r["elapsed"]) for r in rows]
    expected = statistics.quantiles(elapsed, n=100, method="inclusive")[94]
    assert metric("jmeter.jtl", "elapsed:p(95)") == Metric(expected, "ms")
    checkout = [int(r["elapsed"]) for r in rows if r["label"] == "Checkout"]
    assert metric("jmeter.jtl", "elapsed{label:Checkout}:avg").value == statistics.mean(checkout)
    assert metric("jmeter.jtl", "errors:rate").value == 0.5
    assert metric("jmeter.jtl", "errors{label:Checkout}:rate").value == 0.0
    assert metric("jmeter.jtl", "samples:count").value == 80


@pytest.mark.parametrize(
    ("name", "spec", "message"),
    [
        ("k6-summary-export.json", "nothing:p(95)", "no metric nothing"),
        ("k6-summary-export.json", "http_req_duration:p(99)", r"no p\(99\)"),
        ("k6-summary-export.json", "http_req_duration", "METRIC:STAT"),
        ("jmeter.jtl", "elapsed{label:Nope}:avg", "no samples"),
        ("jmeter.jtl", "colour:avg", "no metric colour"),
    ],
)
def test_an_unknown_metric_is_refused(name: str, spec: str, message: str) -> None:
    with pytest.raises(MeasureError, match=message):
        metric(name, spec)


@pytest.mark.parametrize("content", [b"{}", b"[]", b"not a report", b"a,b\n1,2\n"])
def test_an_unknown_format_is_refused(content: bytes) -> None:
    with pytest.raises(MeasureError, match="not a load test result"):
        read(content, AT)


# --- Recording ----------------------------------------------------------------------------------


def test_a_met_target_passes(repo: Path) -> None:
    recorded = run(repo, "k6-summary-export.json", {"AC-3": "http_req_duration:p(95)"})
    [made] = evidence(recorded.records)
    assert made["criterion"] == f"{INTENT}@1#AC-3"
    assert made["measures"] == [
        {"name": "responseTimeP95", "value": 5.975712799999998, "unit": "ms"}
    ]
    assert made["result"] == "pass"
    [test] = [r for r in recorded.records if r["type"] == "artifact"]
    assert test["path"] == "load/checkout.js"
    assert made["test"] == test["id"]


def test_a_missed_target_fails(repo: Path) -> None:
    target = {"metric": "errorRate", "operator": "lt", "value": 0.01}
    recorded = run(repo, "jmeter.jtl", {"AC-3": "errors:rate"}, intent=approved(target))
    [made] = evidence(recorded.records)
    assert made["measures"] == [{"name": "errorRate", "value": 0.5}]
    assert made["result"] == "fail"
    assert made["observedAt"] == "2026-10-29T09:54:30Z"  # the last sample


def test_a_criterion_without_target_is_refused(repo: Path) -> None:
    with pytest.raises(MeasureError, match="AC-4 has no target"):
        run(repo, "k6-summary-export.json", {"AC-4": "http_req_duration:p(95)"})
    with pytest.raises(MeasureError, match="AC-9 is not a criterion"):
        run(repo, "k6-summary-export.json", {"AC-9": "http_req_duration:p(95)"})


def test_units_must_agree(repo: Path) -> None:
    target = {"metric": "responseTimeP95", "operator": "lte", "value": 0.5, "unit": "s"}
    with pytest.raises(MeasureError, match="in ms, the target in s"):
        run(repo, "k6-summary-export.json", {"AC-3": "http_req_duration:p(95)"},
            intent=approved(target))  # fmt: skip


def test_written_from_the_intent_counts_for_coverage(repo: Path) -> None:
    from ois_tools import records
    from ois_tools.coverage import coverage

    build = records.artifact(
        records.commit_source(repo), kind="build", produced_by=f"{NS}/actors/ci@7",
        created_at=datetime(2026, 10, 29, 9, 0, tzinfo=UTC), namespace=NS,
    )  # fmt: skip
    recorded = run(
        repo,
        "k6-summary-export.json",
        {"AC-3": "http_req_duration:p(95)"},
        subject=build["id"],
        written_from_intent=True,
    )
    [made] = evidence(recorded.records)
    assert made["derivedFrom"] == "intent"
    intent, decision = approved(with_decision=True)
    found = coverage([intent, decision, build, *recorded.records], subject=build["id"],
                     intents=[f"{INTENT}@1"]).measurements()  # fmt: skip
    assert found[0] == {"name": "intentCoverage", "value": 0.5, "method": found[0]["method"]}
    actors = [records.actor(kind="human", name=n, created_at=AT, id=i) for n, i in
              [("PO", PO), ("QE", QE)]]  # fmt: skip
    ci = records.actor(kind="tool", name="CI", version="7", created_at=AT, id=f"{NS}/actors/ci")
    bundle = {"ois": "0.1", "type": "bundle", "id": f"{NS}/bundles/b", "createdAt": AT.isoformat(),
              "records": [intent, decision, build, *actors, ci, *recorded.records]}  # fmt: skip
    assert validate(bundle).conforms


# --- Command line -------------------------------------------------------------------------------


def test_cli_records_measures(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "ois"
    (out / "intents").mkdir(parents=True)
    (out / "intents" / "INT-9-v1.ois.json").write_text(json.dumps(approved()), encoding="utf-8")
    args = ["measure", str(HERE / "k6-summary-export.json"), "--intent", f"{INTENT}@1"]
    args += [
        "--criterion",
        "AC-3=http_req_duration:p(95)",
        "--test",
        str(repo / "load/checkout.js"),
    ]
    args += ["--test-producer", QE, "--namespace", NS, "--output-dir", str(out)]
    assert ois(args) == 0
    err = capsys.readouterr().err
    assert "AC-3: responseTimeP95 5.98 ms, target lte 500 ms: pass" in err
    assert len(list((out / "evidence").iterdir())) == 1
    with pytest.raises(SystemExit) as exit_info:
        ois([*args, "--criterion", "AC-3"])
    assert exit_info.value.code == 2
    assert "expected KEY=METRIC:STAT" in capsys.readouterr().err


def test_a_value_without_unit_against_a_target_with_one_is_noted(repo: Path) -> None:
    recorded = run(repo, "k6-summary-export.json", {"AC-3": "http_reqs:count"})
    [made] = evidence(recorded.records)
    assert made["measures"] == [{"name": "responseTimeP95", "value": 40}]
    assert any("http_reqs:count has no unit, the target is in ms" in n for n in recorded.notes)
    timed = run(repo, "k6-summary-export.json", {"AC-3": "http_req_duration:p(95)"})
    assert timed.notes == []
