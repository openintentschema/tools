"""Validation reports with the three levels of spec section 11.1."""

from dataclasses import dataclass, field
from enum import StrEnum


class Level(StrEnum):
    VIOLATION = "violation"  # a MUST is broken; the input does not conform
    WARNING = "warning"  # a rule asks for a report; the input still conforms
    INFO = "info"  # a SHOULD is not followed


@dataclass(frozen=True)
class Report:
    level: Level
    rule: str  # "schema" for Appendix B, otherwise R1 to R15
    message: str
    path: str  # JSONPath into the input document

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "rule": self.rule, "message": self.message, "path": self.path}


@dataclass
class Result:
    reports: list[Report] = field(default_factory=list)

    @property
    def conforms(self) -> bool:
        return not any(r.level is Level.VIOLATION for r in self.reports)
