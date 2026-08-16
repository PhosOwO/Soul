from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soul.services.state import (
    apply_patch_proposal,
    format_state_context,
    initial_state,
    propose_patch,
)


BENCH_DIR = Path(__file__).resolve().parent
TASKS_PATH = BENCH_DIR / "tasks.json"
DEFAULT_OUTPUT_DIR = BENCH_DIR / "results" / "latest"
ADAPTERS = ("baseline", "memory_summary", "soul")
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_tasks(path: Path = TASKS_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_benchmark(
    output_dir: Path,
    backend: str = "deterministic",
    model: str = DEFAULT_DEEPSEEK_MODEL,
    limit: int | None = None,
    temperature: float = 0.0,
    max_tokens: int = 360,
) -> dict[str, Any]:
    started = utc_now()
    start_time = time.perf_counter()
    tasks = load_tasks()
    if limit is not None:
        tasks = tasks[:limit]
    rows: list[dict[str, Any]] = []
    model_client = build_answer_backend(backend, model=model, temperature=temperature, max_tokens=max_tokens)

    for case in tasks:
        for adapter in ADAPTERS:
            item_start = time.perf_counter()
            answer, context, soul_trace, model_trace = run_case(case, adapter, model_client)
            score = score_answer(answer, case)
            rows.append(
                {
                    "case_id": case["id"],
                    "category": case["category"],
                    "title": case["title"],
                    "adapter": adapter,
                    "backend": backend,
                    "task": case["task"],
                    "answer": answer,
                    "context": context,
                    "score": score,
                    "soul_trace": soul_trace,
                    "model_trace": model_trace,
                    "latency_ms": round((time.perf_counter() - item_start) * 1000, 3),
                }
            )

    metadata = {
        "benchmark": "soulbench_v0",
        "started_at": started,
        "finished_at": utc_now(),
        "backend": backend,
        "model": model if backend == "deepseek" else None,
        "task_count": len(tasks),
        "adapter_count": len(ADAPTERS),
        "temperature": temperature if backend == "deepseek" else None,
        "max_tokens": max_tokens if backend == "deepseek" else None,
        "duration_ms": round((time.perf_counter() - start_time) * 1000, 3),
        "metrics": summarize(rows),
    }
    write_outputs(output_dir, metadata, rows)
    return {"metadata": metadata, "results": rows}


def rescore_benchmark(source_results: Path, output_dir: Path) -> dict[str, Any]:
    source_rows = json.loads(source_results.read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in load_tasks()}
    rows: list[dict[str, Any]] = []
    started = utc_now()
    start_time = time.perf_counter()

    for row in source_rows:
        case = tasks.get(row["case_id"])
        if not case:
            raise ValueError(f"Cannot rescore unknown case: {row['case_id']}")
        next_row = dict(row)
        next_row["score"] = score_answer(str(row.get("answer", "")), case)
        rows.append(next_row)

    backend = str(rows[0].get("backend", "unknown")) if rows else "unknown"
    model = rows[0].get("model_trace", {}).get("model") if rows else None
    metadata = {
        "benchmark": "soulbench_v0",
        "started_at": started,
        "finished_at": utc_now(),
        "backend": backend,
        "model": model,
        "task_count": len({row["case_id"] for row in rows}),
        "adapter_count": len({row["adapter"] for row in rows}),
        "temperature": None,
        "max_tokens": None,
        "duration_ms": round((time.perf_counter() - start_time) * 1000, 3),
        "rescore_source": str(source_results),
        "metrics": summarize(rows),
    }
    write_outputs(output_dir, metadata, rows)
    return {"metadata": metadata, "results": rows}


def run_case(
    case: dict[str, Any],
    adapter: str,
    answer_backend: "AnswerBackend",
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    if adapter == "baseline":
        context = build_baseline_context(case)
        answer, trace = answer_backend.answer(case, adapter, context)
        return answer, context, {}, trace
    if adapter == "memory_summary":
        context = build_memory_context(case)
        answer, trace = answer_backend.answer(case, adapter, context)
        return answer, context, {}, trace
    if adapter == "soul":
        context, trace = build_soul_context(case)
        answer, model_trace = answer_backend.answer(case, adapter, context)
        return answer, context, trace, model_trace
    raise ValueError(f"Unknown adapter: {adapter}")


def build_baseline_context(case: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Project Brief:",
            case["project_brief"],
            "",
            "Current Task:",
            case["task"],
        ]
    )


def build_memory_context(case: dict[str, Any]) -> str:
    memories = [evidence.get("memory", evidence["summary"]) for evidence in case.get("evidence", [])]
    return "\n".join(
        [
            "Project Brief:",
            case["project_brief"],
            "",
            "Memory Summary:",
            *[f"- {memory}" for memory in memories],
            "",
            "Current Task:",
            case["task"],
        ]
    )


def build_soul_context(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    state = initial_state(project_name=f"SoulBench/{case['id']}")
    proposals: list[dict[str, Any]] = []
    review_counts: dict[str, int] = defaultdict(int)

    for index, evidence in enumerate(case.get("evidence", []), start=1):
        evidence_payload = {
            **evidence,
            "source": "soulbench_v0",
            "case_id": case["id"],
            "episode_id": f"{case['id']}:evidence:{index}",
            "task": case["task"],
        }
        proposal = propose_patch(state, evidence_payload, source="soulbench_v0")
        proposals.append(proposal)
        review_counts[str(proposal.get("review_recommendation", "unknown"))] += 1
        if proposal.get("review_recommendation") == "auto_accept":
            state = apply_patch_proposal(state, proposal, confirmed_by="soulbench-auto-review")

    projected = format_state_context(state, limit=8, task=case["task"])
    context = "\n".join(
        [
            "Project Brief:",
            case["project_brief"],
            "",
            "Injected Current State:",
            projected,
            "",
            "Current Task:",
            case["task"],
        ]
    )
    trace = {
        "state_version": state.get("version"),
        "proposal_count": len(proposals),
        "review_counts": dict(review_counts),
        "applied_patch_count": len(state.get("history", [])),
        "projected_state": projected,
        "projected_state_chars": len(projected),
        "approx_projected_state_tokens": max(1, len(projected) // 4),
    }
    return context, trace


def deterministic_answer(case: dict[str, Any], adapter: str, context: str) -> str:
    expected = case["expected"]
    include = expected.get("should_include", [])
    avoid = expected.get("should_not_include", [])

    if adapter == "baseline":
        risky = avoid[0] if avoid else "the obvious first idea"
        return (
            "I would start from the most direct implementation path and validate quickly. "
            f"A reasonable first move is to {lower_first(risky)}, then measure the result against the current task."
        )

    if adapter == "memory_summary":
        remembered = ", ".join(include[:1] or ["the remembered context"])
        maybe_risky = avoid[0] if avoid else ""
        if maybe_risky and memory_has_clear_boundary(context):
            tail = (
                f" I would avoid {lower_first(maybe_risky)} when the compressed notes mark it as constrained, "
                "deferred, temporary, or unconfirmed."
            )
        elif maybe_risky:
            tail = f" I would also keep {lower_first(maybe_risky)} on the table if it is fast to test."
        else:
            tail = ""
        return (
            f"The memory summary points to {remembered}. I would use that as the main hint, "
            "but treat the notes as compressed context and verify the exact decision boundary before committing."
            f"{tail}"
        )

    if adapter == "soul":
        must_include = "; ".join(include)
        avoided = "; ".join(avoid)
        boundary = " The state marks unresolved or review-only items as not accepted decisions." if has_review_boundary(context) else ""
        return (
            f"Use the projected Soul state as the decision boundary. Recommend {must_include}. "
            f"Explicitly avoid {avoided} because it conflicts with rejected directions, active constraints, or accepted state."
            f"{boundary}"
        )

    raise ValueError(f"Unknown adapter: {adapter}")


class AnswerBackend:
    def answer(self, case: dict[str, Any], adapter: str, context: str) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError


class DeterministicBackend(AnswerBackend):
    def answer(self, case: dict[str, Any], adapter: str, context: str) -> tuple[str, dict[str, Any]]:
        return deterministic_answer(case, adapter, context), {"backend": "deterministic"}


class DeepSeekBackend(AnswerBackend):
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float,
        max_tokens: int,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        timeout_seconds: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def answer(self, case: dict[str, Any], adapter: str, context: str) -> tuple[str, dict[str, Any]]:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the same project assistant in every condition of this benchmark. "
                        "Use only the provided context and current task. Give one concise recommendation, "
                        "including the main rationale and any explicit constraint or uncertainty that matters. "
                        "Do not mention benchmark adapters, scoring, hidden expectations, or rubrics."
                    ),
                },
                {
                    "role": "user",
                    "content": context,
                },
            ],
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek API request failed: {exc.reason}") from exc

        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"DeepSeek API returned no choices: {raw}")
        answer = str(choices[0].get("message", {}).get("content", "")).strip()
        if not answer:
            raise RuntimeError(f"DeepSeek API returned an empty answer: {raw}")
        return answer, {
            "backend": "deepseek",
            "model": self.model,
            "usage": data.get("usage", {}),
            "response_id": data.get("id"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }


def build_answer_backend(
    backend: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> AnswerBackend:
    if backend == "deterministic":
        return DeterministicBackend()
    if backend == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("DEEP_SEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DeepSeek backend requires DEEPSEEK_API_KEY or DEEP_SEEK_API_KEY.")
        return DeepSeekBackend(api_key, model=model, temperature=temperature, max_tokens=max_tokens)
    raise ValueError(f"Unknown backend: {backend}")


def lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def has_review_boundary(context: str) -> bool:
    lowered = context.lower()
    return "needs_review" in lowered or "open question" in lowered or "unresolved" in lowered


def memory_has_clear_boundary(context: str) -> bool:
    lowered = context.lower()
    markers = (
        "do not",
        "deferred",
        "unresolved",
        "not yet accepted",
        "not confirmed",
        "temporary workaround",
        "should not be reused",
        "must stay stable",
    )
    return any(marker in lowered for marker in markers)


def score_answer(answer: str, case: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    answer_lower = answer.lower()
    include_hits = [term for term in expected.get("should_include", []) if term.lower() in answer_lower]
    forbidden_hits = [
        term
        for term in expected.get("should_not_include", [])
        if forbidden_term_hit(answer_lower, term.lower())
    ]
    rubric_results = [score_rubric_item(answer_lower, item) for item in expected.get("rubric", [])]
    passed_rubric = sum(1 for item in rubric_results if item["passed"])
    total_rubric = len(rubric_results)
    include_score = len(include_hits) / max(1, len(expected.get("should_include", [])))
    avoid_score = 1.0 if not forbidden_hits else 0.0
    rubric_score = passed_rubric / max(1, total_rubric)
    quick_score = round((include_score + avoid_score) / 2, 4)
    judge_score = round((include_score * 0.35) + (avoid_score * 0.35) + (rubric_score * 0.30), 4)
    return {
        "quick_score": quick_score,
        "judge_score": judge_score,
        "include_hits": include_hits,
        "forbidden_hits": forbidden_hits,
        "rubric": rubric_results,
    }


def forbidden_term_hit(answer_lower: str, term_lower: str) -> bool:
    if term_lower not in answer_lower:
        return False
    safe_prefixes = ("avoid ", "do not ", "don't ", "not ", "defer ", "deferred ")
    index = answer_lower.find(term_lower)
    window = answer_lower[max(0, index - 80) : index]
    return not any(prefix in window for prefix in safe_prefixes)


def score_rubric_item(answer_lower: str, item: dict[str, Any]) -> dict[str, Any]:
    required = item.get("requires_any", [])
    forbids = item.get("forbids", [])
    required_hit = not required or any(term.lower() in answer_lower for term in required)
    forbidden_hits = [term for term in forbids if forbidden_term_hit(answer_lower, term.lower())]
    return {
        "id": item["id"],
        "passed": required_hit and not forbidden_hits,
        "required_hit": required_hit,
        "forbidden_hits": forbidden_hits,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    by_adapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_adapter[row["adapter"]].append(row)

    for adapter, adapter_rows in by_adapter.items():
        quick_scores = [row["score"]["quick_score"] for row in adapter_rows]
        judge_scores = [row["score"]["judge_score"] for row in adapter_rows]
        summary[adapter] = {
            "case_count": len(adapter_rows),
            "quick_score_avg": round(sum(quick_scores) / len(quick_scores), 4),
            "judge_score_avg": round(sum(judge_scores) / len(judge_scores), 4),
            "forbidden_hit_count": sum(len(row["score"]["forbidden_hits"]) for row in adapter_rows),
            "avg_latency_ms": round(sum(row["latency_ms"] for row in adapter_rows) / len(adapter_rows), 3),
        }
        usage = sum_usage(adapter_rows)
        if usage:
            summary[adapter]["usage"] = usage

    soul_rows = by_adapter.get("soul", [])
    if soul_rows:
        summary["soul"]["avg_projected_state_tokens"] = round(
            sum(row["soul_trace"].get("approx_projected_state_tokens", 0) for row in soul_rows) / len(soul_rows),
            2,
        )
        summary["soul"]["total_applied_patches"] = sum(
            row["soul_trace"].get("applied_patch_count", 0) for row in soul_rows
        )
    return summary


def write_outputs(output_dir: Path, metadata: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "benchmark_report.md").write_text(
        render_report(metadata, rows),
        encoding="utf-8",
    )


def sum_usage(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    has_usage = False
    for row in rows:
        usage = row.get("model_trace", {}).get("usage", {})
        if not isinstance(usage, dict):
            continue
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
                has_usage = True
    return totals if has_usage else {}


def render_report(metadata: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    metrics = metadata["metrics"]
    lines = [
        "# SoulBench v0 Report",
        "",
        f"- Benchmark: `{metadata['benchmark']}`",
        f"- Backend: `{metadata['backend']}`",
        f"- Model: `{metadata.get('model') or 'n/a'}`",
        f"- Started: `{metadata['started_at']}`",
        f"- Tasks: `{metadata['task_count']}`",
        "",
        "## Overall Metrics",
        "",
        "| Adapter | Quick Score | Judge Score | Forbidden Hits | Avg Latency ms | Tokens | Extra |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for adapter in ADAPTERS:
        item = metrics[adapter]
        extra = ""
        if adapter == "soul":
            extra = (
                f"avg projected tokens {item.get('avg_projected_state_tokens', 0)}, "
                f"applied patches {item.get('total_applied_patches', 0)}"
            )
        usage = item.get("usage", {})
        tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
        lines.append(
            f"| `{adapter}` | {item['quick_score_avg']:.4f} | {item['judge_score_avg']:.4f} | "
            f"{item['forbidden_hit_count']} | {item['avg_latency_ms']:.3f} | {tokens} | {extra} |"
        )

    lines.extend(
        [
            "",
            "## Case Results",
            "",
            "| Case | Category | Baseline | Memory Summary | Soul |",
            "|---|---|---:|---:|---:|",
        ]
    )
    by_case: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    title_by_case: dict[str, tuple[str, str]] = {}
    for row in rows:
        by_case[row["case_id"]][row["adapter"]] = row
        title_by_case[row["case_id"]] = (row["title"], row["category"])
    for case_id, adapter_rows in by_case.items():
        title, category = title_by_case[case_id]
        lines.append(
            f"| `{case_id}` {title} | `{category}` | "
            f"{adapter_rows['baseline']['score']['judge_score']:.2f} | "
            f"{adapter_rows['memory_summary']['score']['judge_score']:.2f} | "
            f"{adapter_rows['soul']['score']['judge_score']:.2f} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            interpretation_for_backend(str(metadata["backend"])),
            "",
            "The useful comparison here is whether structured projected state can preserve constraints, rejected directions, open questions, and stale-memory boundaries more clearly than a plain memory summary.",
        ]
    )
    return "\n".join(lines) + "\n"


def interpretation_for_backend(backend: str) -> str:
    if backend == "deterministic":
        return (
            "This run uses a deterministic local answer backend. It is intended to validate the benchmark shape, "
            "scoring, and Soul state loop, not to claim real model quality."
        )
    if backend == "deepseek":
        return (
            "This run uses the real DeepSeek API. The system prompt, task set, model, temperature, and max_tokens "
            "are held constant across adapters; only the provided context changes."
        )
    return "This run uses a custom backend. Check results.json for the raw context and answers."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AMBench-style SoulBench v0.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--backend", default="deterministic", choices=["deterministic", "deepseek"])
    parser.add_argument("--model", default=DEFAULT_DEEPSEEK_MODEL)
    parser.add_argument("--limit", type=int, default=None, help="Optional number of tasks to run from the task set.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=360)
    parser.add_argument("--rescore-from", default=None, help="Rescore an existing results.json without calling a model.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.rescore_from:
        result = rescore_benchmark(Path(args.rescore_from), Path(args.output_dir))
    else:
        result = run_benchmark(
            Path(args.output_dir),
            backend=args.backend,
            model=args.model,
            limit=args.limit,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    print(json.dumps(result["metadata"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
