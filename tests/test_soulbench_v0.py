from __future__ import annotations

import json
from pathlib import Path

from benchmarks.soulbench_v0.run_soulbench import ADAPTERS, load_tasks, run_benchmark


def test_soulbench_task_set_has_required_controls() -> None:
    tasks = load_tasks()
    assert len(tasks) >= 10
    assert {task["category"] for task in tasks} >= {
        "coding_project_memory",
        "research_experiment_continuity",
        "product_decision_constraints",
        "stale_or_conflicting_memory",
    }
    assert any("Focal Loss" in evidence["summary"] for task in tasks for evidence in task["evidence"])
    assert any("backbone" in evidence["summary"].lower() for task in tasks for evidence in task["evidence"])
    assert any("MLD" in evidence["summary"] for task in tasks for evidence in task["evidence"])


def test_soulbench_run_writes_report(tmp_path: Path) -> None:
    output_dir = tmp_path / "soulbench"
    result = run_benchmark(output_dir)

    metadata = result["metadata"]
    rows = result["results"]
    assert metadata["task_count"] == len(load_tasks())
    assert len(rows) == metadata["task_count"] * len(ADAPTERS)
    assert (output_dir / "run_metadata.json").exists()
    assert (output_dir / "results.json").exists()
    assert (output_dir / "benchmark_report.md").exists()

    persisted = json.loads((output_dir / "run_metadata.json").read_text(encoding="utf-8"))
    assert persisted["metrics"]["soul"]["judge_score_avg"] >= persisted["metrics"]["memory_summary"]["judge_score_avg"]
    assert persisted["metrics"]["soul"]["total_applied_patches"] > 0
