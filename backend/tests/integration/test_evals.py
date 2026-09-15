"""Runs the golden RAG evaluation against PostgreSQL in offline mode and enforces its thresholds."""

from __future__ import annotations

import json

import pytest

from app.core.container import Container
from app.evals.runner import DEFAULT_DATASET, check_thresholds, evaluate, render

pytestmark = pytest.mark.integration


async def test_golden_dataset_meets_quality_thresholds(container: Container, capsys):
    dataset = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    summary, results = await evaluate(container, dataset)
    with capsys.disabled():
        print(render(summary, results))
    violations = check_thresholds(summary, dataset["thresholds"])
    assert not violations, violations
    # Safety invariants hold for every single case, not just on average.
    for result in results:
        if result.actual == "ANSWER":
            assert result.validation_passed, f"{result.case_id} returned an unvalidated answer"
            assert result.cited, f"{result.case_id} answered without citations"
