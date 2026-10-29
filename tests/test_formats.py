"""Reading test reports: every format ois junit accepts, from the real runs in tests/junit/."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ois_tools.formats import JUnitReport, cases
from ois_tools.formats import TestCase as Case

HERE = Path(__file__).parent / "junit"
AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def read(name: str) -> list[Case]:
    return cases(JUnitReport((HERE / name).read_bytes(), written_at=AT), AT)


def by_name(found: list[Case]) -> dict[str, Case]:
    return {case.name: case for case in found}


def test_playwright_keeps_tags_annotations_and_the_final_attempt() -> None:
    found = by_name(read("playwright.json"))
    assert set(found) == {"rounds down", "rejects negative amounts", "without a key"}
    assert found["rounds down"].tags == ("AC-1",)
    assert found["rejects negative amounts"].properties == (("criterion", "AC-2"),)
    assert found["rejects negative amounts"].result == "fail"  # failed twice, retried once
    assert found["rounds down"].classname == "tests/rounding.spec.ts"
    assert found["rounds down"].file == "/home/runner/work/billing/billing/tests/rounding.spec.ts"
    assert found["rounds down"].observed_at.year == 2026


def test_jest_and_vitest_json() -> None:
    jest = by_name(read("jest.json"))
    assert jest["Invoices AC-1 rounds down"].result == "pass"
    assert jest["Invoices rejects negative amounts @AC-2"].result == "fail"
    assert str(jest["Invoices AC-1 rounds down"].file).endswith("/jest/rounding.test.cjs")
    vitest = by_name(read("vitest.json"))
    assert vitest["Invoices rounds down"].tags == ("AC-1",)
    assert str(vitest["Invoices rounds down"].file).endswith("/tests/rounding.test.js")


def test_go_test_json() -> None:
    found = by_name(read("go.json"))
    assert {name: case.result for name, case in found.items()} == {
        "TestRounding": "fail",
        "TestRounding/AC-1_rounds_down": "pass",
        "TestRounding/AC-2_rejects_negative_amounts": "fail",
        "TestWithoutKey": "pass",
        "TestSkipped": "skipped",
    }
    assert found["TestWithoutKey"].classname == "git.acme.example/billing/rounding"
    assert found["TestWithoutKey"].observed_at == datetime(
        2026, 10, 29, 9, 38, 3, 879800, tzinfo=UTC
    )


def test_trx_from_xunit_and_mstest() -> None:
    xunit = by_name(read("xunit.trx"))
    assert xunit["AC-1 rounds down"].classname == "Billing.Tests.RoundingTests"
    assert xunit["Billing.Tests.RoundingTests.RejectsNegativeAmounts"].result == "fail"
    mstest = by_name(read("mstest.trx"))
    assert mstest["RejectsNegativeAmounts"].tags == ("AC-2",)
    assert mstest["RoundsDown"].result == "pass"


def test_junit_xml_is_still_read() -> None:
    assert by_name(read("pytest.xml"))["test_rounds_down"].properties == (("criterion", "AC-1"),)


@pytest.mark.parametrize("content", [b"{}", b"[1]", b"<html/>", b"plain text", b""])
def test_an_unknown_format_is_refused(content: bytes) -> None:
    with pytest.raises(ValueError, match="not a test report"):
        cases(JUnitReport(content), AT)
