"""Load test results as measures for non-functional criteria: k6 summaries (--summary-export or
handleSummary JSON) and JMeter results (JTL CSV). A metric is named METRIC{TAG:VALUE}:STAT, as k6
names submetrics. Evidence passes when its measure meets the criterion's target."""

import csv
import io
import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from ois_tools import records
from ois_tools.coverage import OPERATORS
from ois_tools.records import Record, Recorded, RecordError

SPEC = re.compile(r"(?P<name>[^{}:]+)(?:\{(?P<tag>[^}]*)\})?:(?P<stat>.+)")
K6_TIME = ("http_req_", "iteration_duration", "group_duration")  # built-in trends in ms
JMETER_TIME = ("elapsed", "Latency", "Connect", "IdleTime")  # columns in ms
JMETER_NUMBERS = (*JMETER_TIME, "bytes", "sentBytes")


class MeasureError(RecordError):
    """The results or the requested metric cannot be read."""


@dataclass(frozen=True)
class Metric:
    value: float
    unit: str | None


@dataclass(frozen=True)
class Results:
    format: str  # "k6" or "jmeter"
    observed_at: datetime
    k6: dict[str, Any] | None = None  # metric name -> statistics
    rows: tuple[dict[str, str], ...] = ()  # JMeter samples

    def metric(self, spec: str) -> Metric:
        match = SPEC.fullmatch(spec)
        if match is None:
            raise MeasureError(
                f"{spec!r}: expected METRIC:STAT, for example http_req_duration:p(95)"
            )
        name, tag, stat = match["name"], match["tag"], match["stat"]
        if self.k6 is not None:
            return _k6(self.k6, name + (f"{{{tag}}}" if tag is not None else ""), stat)
        return _jmeter(self.rows, name, tag, stat)


def read(content: bytes, written_at: datetime) -> Results:
    """k6 or JMeter results, recognized by content; `written_at` stands in for k6's missing
    timestamp."""
    text = content.decode("utf-8-sig", errors="replace").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("metrics"), dict) and data["metrics"]:
            return Results("k6", written_at, k6=data["metrics"])
    elif text.startswith("timeStamp,"):
        rows = tuple(csv.DictReader(io.StringIO(text)))
        if rows:
            try:
                end = max(int(r["timeStamp"]) + int(r["elapsed"]) for r in rows)
            except (KeyError, ValueError) as error:
                raise MeasureError(
                    "JMeter results need timeStamp and elapsed in milliseconds"
                ) from error
            return Results("jmeter", datetime.fromtimestamp(end / 1000, UTC), rows=rows)
    raise MeasureError(
        "not a load test result: expected a k6 summary (JSON) or JMeter results (JTL CSV)"
    )


def record_run(
    content: bytes,
    *,
    intent: Record,
    measures: dict[str, str],
    test: Path,
    test_producer: str,
    namespace: str,
    written_at: datetime,
    created_at: datetime,
    known: list[Record] | None = None,
    subject: str | None = None,
    run: str | None = None,
    location: str | None = None,
    written_from_intent: bool = False,
) -> Recorded:
    """Evidence per criterion in `measures` (key -> metric), with the metric as the measure its
    target names, and the test artifact of the load test script `test`."""
    if intent.get("status") != "approved":
        raise MeasureError("approve the intent first: evidence refers to an approved version")
    results = read(content, written_at)
    reference = records.reference(intent)
    criteria = {c["key"]: c for c in intent["criteria"]}
    adapter = records.actor(
        kind="tool",
        name="ois measure (load test adapter of ois-tools)",
        version=package_version("ois-tools"),
        created_at=created_at,
        id=f"{namespace.rstrip('/')}/actors/ois-measure",
    )
    recorded = Recorded([adapter], records.reference(adapter))

    found = []
    for key, spec in measures.items():
        criterion = criteria.get(key)
        if criterion is None:
            raise MeasureError(f"{key} is not a criterion of {reference}")
        target = criterion.get("target")
        if target is None:
            raise MeasureError(f"{key} has no target to measure against")
        metric = results.metric(spec)
        if target.get("unit") and metric.unit and target["unit"] != metric.unit:
            raise MeasureError(f"{key}: {spec} is in {metric.unit}, the target in {target['unit']}")
        if target.get("unit") and not metric.unit:
            recorded.notes.append(
                f"{key}: {spec} has no unit, the target is in {target['unit']}; "
                "check that it is the metric you mean"
            )
        measure: Record = {"name": target["metric"], "value": metric.value}
        if metric.unit:
            measure["unit"] = metric.unit
        met = OPERATORS[target["operator"]](metric.value, target["value"])
        found.append((key, measure, "pass" if met else "fail"))

    source = records.file_source(test, location)
    context = [f"{reference}#{key}" for key in criteria if key in measures]
    artifact = records.known_test(known or [], source) or records.artifact(
        source,
        kind="test",
        produced_by=test_producer,
        created_at=created_at,
        namespace=namespace,
        context=context if written_from_intent else (),
    )
    recorded.records.append(artifact)
    for key, measure, result in found:
        evidence = partial(
            records.evidence,
            criterion=f"{reference}#{key}",
            test=artifact["id"],
            result=result,
            recorded_by=recorded.adapter,
            observed_at=results.observed_at,
            created_at=created_at,
            namespace=namespace,
            subject=subject,
            measures=[measure],
            run=run,
        )
        made, problems = records.derived(evidence, [intent, *(known or []), artifact])
        if problems and "context" in artifact:
            recorded.notes.append(f"{key}: {problems[0]}")
        recorded.records.append(made)
    return recorded


def _k6(metrics: dict[str, Any], name: str, stat: str) -> Metric:
    entry = metrics.get(name)
    if not isinstance(entry, dict):
        raise MeasureError(
            f"no metric {name} in the k6 summary; it has {', '.join(sorted(metrics))}"
        )
    values = entry.get("values", entry)  # handleSummary nests them, --summary-export does not
    if stat not in values and stat in ("rate", "value") and {"passes", "fails"} <= set(values):
        stat = "value" if stat == "rate" else "rate"  # a Rate's fraction, named either way
    value = values.get(stat)
    if not isinstance(value, int | float) or isinstance(value, bool):
        stats = ", ".join(k for k, v in values.items() if isinstance(v, int | float))
        raise MeasureError(f"{name} has no {stat}; it has {stats}")
    trend = stat in ("avg", "min", "med", "max") or stat.startswith("p(")
    timed = entry.get("contains") == "time" if "contains" in entry else _k6_time(name)
    return Metric(value, "ms" if timed and trend else None)


def _k6_time(name: str) -> bool:
    base = name.partition("{")[0]
    return base != "http_req_failed" and base.startswith(K6_TIME)


def _jmeter(rows: tuple[dict[str, str], ...], name: str, tag: str | None, stat: str) -> Metric:
    selected = list(rows)
    if tag is not None:
        column, _, wanted = tag.partition(":")
        selected = [r for r in rows if r.get(column) == wanted]
        if not selected:
            raise MeasureError(f"no samples with {column} {wanted!r} in the JMeter results")
    if name == "samples" and stat in ("count", "rate"):
        if stat == "count":
            return Metric(len(selected), None)
        start = min(int(r["timeStamp"]) for r in selected)
        end = max(int(r["timeStamp"]) + int(r["elapsed"]) for r in selected)
        return Metric(len(selected) / max((end - start) / 1000, 0.001), None)
    if name == "errors" and stat in ("count", "rate"):
        failed = sum(1 for r in selected if r.get("success") != "true")
        return Metric(failed if stat == "count" else failed / len(selected), None)
    if name not in JMETER_NUMBERS or name not in selected[0]:
        raise MeasureError(
            f"no metric {name} in the JMeter results; they have samples, errors, "
            + ", ".join(c for c in JMETER_NUMBERS if c in selected[0])
        )
    numbers = sorted(float(r[name]) for r in selected)
    return Metric(_statistic(numbers, name, stat), "ms" if name in JMETER_TIME else None)


def _statistic(numbers: list[float], name: str, stat: str) -> float:
    """avg, min, med, max or p(N), the percentile interpolated between closest ranks as k6
    computes it."""
    if stat == "avg":
        return statistics.mean(numbers)
    if stat in ("min", "max", "med"):
        return {"min": numbers[0], "max": numbers[-1], "med": statistics.median(numbers)}[stat]
    if match := re.fullmatch(r"p\((\d+(?:\.\d+)?)\)", stat):
        position = (len(numbers) - 1) * float(match[1]) / 100
        low, high = math.floor(position), math.ceil(position)
        return numbers[low] + (numbers[high] - numbers[low]) * (position - low)
    raise MeasureError(f"{name} has no {stat}; use avg, min, med, max or p(N)")
