"""Tests for fair project scheduling."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.supervisor_scheduler import (
    FairProjectScheduler,
    ScheduleDecision,
)


class FairProjectSchedulerTest(TestCase):
    def test_round_robin_order(self) -> None:
        scheduler = FairProjectScheduler(["a", "b", "c"])
        decisions = [scheduler.next(lambda pid: True) for _ in range(6)]
        ids = [d.project_id for d in decisions if d is not None]
        self.assertEqual(ids, ["a", "b", "c", "a", "b", "c"])

    def test_skips_paused(self) -> None:
        paused = {"b"}
        scheduler = FairProjectScheduler(["a", "b", "c"])
        decisions = [
            scheduler.next(lambda pid: pid not in paused)
            for _ in range(4)
        ]
        ids = [d.project_id for d in decisions if d is not None]
        self.assertEqual(ids, ["a", "c", "a", "c"])

    def test_returns_none_when_no_runnable(self) -> None:
        scheduler = FairProjectScheduler(["a", "b"])
        result = scheduler.next(lambda pid: False)
        self.assertIsNone(result)

    def test_single_project(self) -> None:
        scheduler = FairProjectScheduler(["a"])
        decisions = [scheduler.next(lambda pid: True) for _ in range(3)]
        ids = [d.project_id for d in decisions if d is not None]
        self.assertEqual(ids, ["a", "a", "a"])

    def test_no_starvation_after_replenish(self) -> None:
        """A project that becomes runnable again should be scheduled."""
        runnable = {"a"}
        scheduler = FairProjectScheduler(["a", "b"])

        # a is runnable, b is not
        d1 = scheduler.next(lambda pid: pid in runnable)
        self.assertEqual(d1.project_id, "a")

        # Now b becomes runnable
        runnable.add("b")
        d2 = scheduler.next(lambda pid: pid in runnable)
        self.assertEqual(d2.project_id, "b")

        d3 = scheduler.next(lambda pid: pid in runnable)
        self.assertEqual(d3.project_id, "a")

    def test_decision_fields(self) -> None:
        scheduler = FairProjectScheduler(["a"])
        d = scheduler.next(lambda pid: True)
        self.assertIsNotNone(d)
        self.assertEqual(d.project_id, "a")
        self.assertEqual(d.reason, "ready")

    def test_empty_projects(self) -> None:
        scheduler = FairProjectScheduler([])
        result = scheduler.next(lambda pid: True)
        self.assertIsNone(result)
