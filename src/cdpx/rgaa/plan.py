"""Fail-closed execution planning and global RGAA budgets."""

from __future__ import annotations

import time
from dataclasses import dataclass

from cdpx.client import CDPTimeout
from cdpx.policy import Authority
from cdpx.rgaa import provider

PASSIVE_TESTS = frozenset(
    {
        "2.1.1",
        "3.2.1",
        "3.2.2",
        "3.2.3",
        "3.2.4",
        "6.1.1",
        "8.1.1",
        "8.3.1",
        "8.4.1",
        "8.5.1",
        "8.6.1",
        "11.1.1",
        "11.9.1",
        "13.1.1",
    }
)
ACCESSIBILITY_TESTS = frozenset({"6.1.1", "11.1.1", "11.9.1"})
FOCUS_TESTS = frozenset({"10.7.1", "12.8.1"})
SPACING_TESTS = frozenset({"10.12.1"})
AXE_TESTS = frozenset(test_id for test_ids in provider.AXE_TO_RGAA.values() for test_id in test_ids)
FOCUS_STEP_LIMIT = 20


@dataclass(frozen=True)
class ScanPlan:
    selected: frozenset[str]
    passive: bool
    accessibility: bool
    focus: bool
    spacing: bool
    axe: bool
    maximum_actions: int

    @property
    def environment(self) -> bool:
        return any((self.passive, self.accessibility, self.focus, self.spacing, self.axe))

    @property
    def required_authority(self) -> Authority:
        if self.spacing or self.axe:
            return Authority.PRIVILEGED
        if self.focus:
            return Authority.INTERACTION
        return Authority.OBSERVATION

    def public(self, *, navigations: int = 0) -> dict[str, object]:
        total_actions = navigations + self.maximum_actions
        return {
            "collectors": [
                name
                for name, enabled in (
                    ("passive-dom-css", self.passive),
                    ("accessibility", self.accessibility),
                    ("focus", self.focus),
                    ("text-spacing", self.spacing),
                    ("axe-core", self.axe),
                )
                if enabled
            ],
            "environment": self.environment,
            "planned_actions": {
                "navigations": navigations,
                "interactions": self.maximum_actions,
                "total": total_actions,
            },
            "maximum_actions": total_actions,
            "required_authority": self.required_authority.value,
        }


def build_scan_plan(selected: set[str] | frozenset[str], *, scope: str, engine: str) -> ScanPlan:
    wanted = frozenset(selected)
    focus = scope in {"interactive", "privileged"} and bool(wanted & FOCUS_TESTS)
    spacing = scope == "privileged" and bool(wanted & SPACING_TESTS)
    return ScanPlan(
        selected=wanted,
        passive=bool(wanted & PASSIVE_TESTS),
        accessibility=bool(wanted & ACCESSIBILITY_TESTS),
        focus=focus,
        spacing=spacing,
        axe=engine == "hybrid" and bool(wanted & AXE_TESTS),
        maximum_actions=FOCUS_STEP_LIMIT if focus else 0,
    )


@dataclass
class ExecutionBudget:
    deadline: float
    maximum_actions: int | None = None
    actions_used: int = 0

    @classmethod
    def start(cls, timeout: float, maximum_actions: int | None = None) -> ExecutionBudget:
        if timeout <= 0:
            raise ValueError("RGAA timeout must be positive")
        if maximum_actions is not None and maximum_actions < 0:
            raise ValueError("--max-actions must be non-negative")
        return cls(time.monotonic() + timeout, maximum_actions)

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CDPTimeout("RGAA global deadline exceeded")
        return remaining

    def consume(self, label: str) -> None:
        if self.maximum_actions is not None and self.actions_used >= self.maximum_actions:
            raise ValueError(f"--max-actions budget exceeded before {label}")
        self.actions_used += 1
