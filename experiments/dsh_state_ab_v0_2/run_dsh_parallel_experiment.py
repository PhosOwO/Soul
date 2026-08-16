from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SOUL_ROOT = Path(__file__).resolve().parents[2]
if str(SOUL_ROOT) not in sys.path:
    sys.path.insert(0, str(SOUL_ROOT))

from soul.services.state import apply_patch_proposal, load_state, save_state, state_paths


EXPERIMENT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = EXPERIMENT_DIR / "outputs" / "latest"
DEFAULT_HARNESS_DIR = Path(r"D:\Documents\Github\deepseek-harness")
DEFAULT_NODE_BIN = Path(
    r"C:\Users\ZLX\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
)

PORT = 8765


SYSTEM_CONTEXT = (
    "项目背景：我们在做 MHW 预测实验，当前核心问题是事件级 Recall 不足。"
    "短期目标是提高事件级 Recall，同时不能明显牺牲 Precision。"
)

TURNS: list[dict[str, Any]] = [
    {
        "id": "round_01_focal_loss",
        "learn_evidence": (
            "上一轮已经尝试过 Focal Loss，事件级 Recall 提升有限，"
            "所以本轮不要把 Focal Loss 作为主线。"
        ),
        "memory": "已尝试 Focal Loss，收益有限，暂不作为主线。",
        "task": "Recall 还是偏低，下一轮实验应该优先做什么？请给一个可执行方案。",
        "positive_patterns": ["MLD", "海流", "事件级 Recall", "Precision", "误差", "对照"],
        "negative_patterns": ["优先尝试 Focal Loss", "重新尝试 Focal Loss", "Focal Loss 作为主线"],
    },
    {
        "id": "round_02_backbone_constraint",
        "learn_evidence": (
            "当前约束是暂不更换 Backbone，因为换主干会引入太多变量，"
            "本阶段只做最小闭环验证。"
        ),
        "memory": "当前阶段暂不更换 Backbone，先保持变量可控，做最小闭环。",
        "task": "有人建议直接换一个更强 Backbone，你怎么回应？",
        "positive_patterns": ["暂不更换 Backbone", "现有 Backbone", "最小闭环", "变量", "不建议"],
        "negative_patterns": ["同意更换 Backbone", "换成 Transformer", "换成 ViT", "直接换 Backbone"],
    },
    {
        "id": "round_03_mld_continuity",
        "learn_evidence": (
            "还有一个尚未验证的方向：MLD / 海流动力变量可能解释一部分漏检，"
            "但我们还没有跑过对照。"
        ),
        "memory": "MLD / 海流动力变量是尚未验证但值得保留的方向，需要跑对照。",
        "task": "请把明天可以执行的实验 checklist 写出来，重点是保持连续性并避免重复无效方向。",
        "positive_patterns": ["MLD", "海流", "对照", "事件级 Recall", "Precision", "checklist"],
        "negative_patterns": ["Focal Loss 作为主线", "更换 Backbone", "大规模新标注"],
    },
]


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_output_dir(output_dir: Path) -> Path:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    (output_dir / "raw").mkdir(parents=True, exist_ok=True)
    return output_dir / "project"


def initial_demo_state(project_dir: Path) -> None:
    now = utc_now()
    state = {
        "schema_version": 1,
        "project": "Soul DSH State A/B v0.2",
        "version": 1,
        "updated_at": now,
        "current_state": {
            "beliefs": [
                {
                    "id": "mhw-recall-problem",
                    "statement": "当前核心问题是 MHW 预测中的事件级 Recall 不足。",
                    "status": "accepted",
                    "confidence": 0.85,
                    "evidence_count": 1,
                    "latest_evidence": "experiment setup",
                    "updated_at": now,
                }
            ],
            "constraints": [
                "短期目标是提高事件级 Recall，同时不能明显牺牲 Precision。"
            ],
            "open_questions": [],
        },
        "history": [],
    }
    save_state(state, project_dir)


def append_patch(project_dir: Path, evidence_text: str) -> dict[str, Any]:
    state = load_state(project_dir, project_name="Soul DSH State A/B v0.2")
    operations: list[dict[str, Any]] = []
    evidence = {"source": "dsh_state_ab_v0_2:learn", "summary": evidence_text, "content": evidence_text}

    if "Focal Loss" in evidence_text and "有限" in evidence_text:
        operations.append(
            {
                "op": "upsert_belief",
                "id": "focal-loss-limited",
                "value": {
                    "id": "focal-loss-limited",
                    "statement": "Focal Loss 已尝试且收益有限，暂不作为提高事件级 Recall 的主线。",
                    "status": "accepted",
                    "confidence": 0.86,
                    "evidence_count": 1,
                    "latest_evidence": evidence_text,
                },
                "evidence": evidence,
            }
        )
    if "暂不更换 Backbone" in evidence_text:
        operations.append(
            {
                "op": "add_constraint",
                "value": "当前阶段暂不更换 Backbone，先保持变量可控并完成最小闭环。",
                "evidence": evidence,
            }
        )
    if "MLD" in evidence_text or "海流" in evidence_text:
        operations.append(
            {
                "op": "upsert_belief",
                "id": "mld-open-direction",
                "value": {
                    "id": "mld-open-direction",
                    "statement": "MLD / 海流动力变量是尚未验证但需要保持连续性的候选方向。",
                    "status": "accepted",
                    "confidence": 0.78,
                    "evidence_count": 1,
                    "latest_evidence": evidence_text,
                },
                "evidence": evidence,
            }
        )
        operations.append(
            {
                "op": "add_open_question",
                "value": "MLD / 海流动力变量能否解释漏检并提升事件级 Recall？",
                "evidence": evidence,
            }
        )

    proposal = {
        "id": f"patch-{int(time.time() * 1000)}",
        "created_at": utc_now(),
        "status": "proposed",
        "source": "dsh_state_ab_v0_2:learn",
        "base_version": state["version"],
        "evidence": evidence,
        "operations": operations,
    }
    patch_log = state_paths(project_dir).patch_log_path
    patch_log.parent.mkdir(parents=True, exist_ok=True)
    with patch_log.open("a", encoding="utf-8") as file:
        file.write(json.dumps(proposal, ensure_ascii=False) + "\n")

    next_state = apply_patch_proposal(state, proposal, confirmed_by="dsh_state_ab_v0_2")
    save_state(next_state, project_dir)
    return {"proposal": proposal, "new_state_version": next_state["version"]}


def write_config(path: Path, *, with_soul: bool, sessions_root: str) -> None:
    soul_block = ""
    if with_soul:
        soul_block = f"""
- id: soul-context
  name: '@deepseek-ai/dsh-soul-context'
  config:
    baseUrl: http://127.0.0.1:{PORT}
    scope: dsh-state-ab-v0-2
    stateLimit: 8
    proposeTransitions: true
    requireState: true
"""
    content = f"""# Generated by Soul DSH State A/B v0.2.
- id: settings
  name: '@deepseek-ai/dsh-settings-file'

- id: credentials
  name: '@deepseek-ai/dsh-credentials-local'

- id: llm-deepseek
  name: '@deepseek-ai/dsh-llm-deepseek'
  config:
    thinking: disabled
    models:
      - id: deepseek-v4-flash
        contextWindow: 128000
{soul_block}
- id: agent-spine
  name: '@deepseek-ai/dsh-agent-spine-demo'
  config:
    agents:
      - id: main
        provider: deepseek-official
        model: deepseek-v4-flash
        cwd: !!js process.cwd()
    workspaceContext: false
    persona: |
      You are a concise experiment assistant. Answer the user's task directly.
      Keep the current experiment constraints and avoid inventing unobserved history.

- id: persistence
  name: '@deepseek-ai/dsh-session-persistence-jsonl'
  config:
    root: './{sessions_root}'
    compression: 'none'

- id: checkpoint-policy
  name: '@deepseek-ai/dsh-session-checkpoint-policy'
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def start_soul_api(project_dir: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "soul.api",
            "--project-dir",
            str(project_dir),
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=str(SOUL_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def wait_for_api() -> None:
    deadline = time.time() + 15
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise RuntimeError(f"Soul API did not become ready: {last_error}")


def env_with_node(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    if not env.get("DEEPSEEK_API_KEY") and env.get("DEEP_SEEK_API_KEY"):
        env["DEEPSEEK_API_KEY"] = env["DEEP_SEEK_API_KEY"]
    if args.node_bin.exists():
        env["PATH"] = str(args.node_bin) + os.pathsep + env.get("PATH", "")
    return env


def run_harness(args: argparse.Namespace, config: Path, task: str, raw_path: Path) -> CommandResult:
    node = args.node_bin / "node.exe" if (args.node_bin / "node.exe").exists() else Path("node")
    command = [
        str(node),
        "--import",
        "tsx/esm",
        "examples/headless-agent/tests/fixtures/headless-driver.ts",
        str(config),
        task,
    ]
    completed = subprocess.run(
        command,
        cwd=str(args.harness_dir),
        env=env_with_node(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=args.timeout_seconds,
    )
    raw_path.write_text(completed.stdout, encoding="utf-8")
    if completed.stderr:
        raw_path.with_suffix(".stderr.txt").write_text(completed.stderr, encoding="utf-8")
    return CommandResult(
        ok=completed.returncode == 0,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


def parse_harness_output(stdout: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") == "session_event":
            events.append(item)
        else:
            final = item
    assistant_text = ""
    injection_text = ""
    for item in events:
        event = item.get("event", {})
        if event.get("type") == "assistant/message":
            message = event.get("data", {}).get("message", {})
            assistant_text = text_blocks(message)
        if event.get("type") == "user/message":
            data = event.get("data", {})
            source = data.get("source", {})
            if source.get("kind") == "plugin" and source.get("plugin") == "soul-context":
                injection_text = text_blocks(data)
    if not assistant_text and isinstance(final, dict):
        assistant_text = text_blocks(final.get("message", {}))
    return {
        "assistant_text": assistant_text.strip(),
        "soul_injection": injection_text.strip(),
        "event_count": len(events),
        "final": final,
    }


def text_blocks(message: dict[str, Any]) -> str:
    content = message.get("content", [])
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def memory_task(turn: dict[str, Any], memory_items: list[str]) -> str:
    memory = "\n".join(f"- {item}" for item in memory_items) or "- 暂无历史摘要。"
    return (
        f"{SYSTEM_CONTEXT}\n\n"
        "普通摘要记忆：以下是上一轮对话的自然语言摘要，它可能不完整，也没有显式版本或确认门。\n"
        f"{memory}\n\n"
        f"当前任务：{turn['task']}"
    )


def plain_task(turn: dict[str, Any]) -> str:
    return f"{SYSTEM_CONTEXT}\n\n当前任务：{turn['task']}"


def score_answer(answer: str, turn: dict[str, Any]) -> dict[str, Any]:
    positive_hits = [pattern for pattern in turn["positive_patterns"] if pattern.lower() in answer.lower()]
    negative_hits = find_negative_hits(answer, turn["negative_patterns"])
    return {
        "positive_hits": positive_hits,
        "negative_hits": negative_hits,
        "score": min(4, len(positive_hits)) + (0 if negative_hits else 2),
    }


def find_negative_hits(answer: str, patterns: list[str]) -> list[str]:
    lowered = answer.lower()
    guards = ["不", "不要", "不再", "避免", "暂不", "不能", "不是", "不建议", "不应该", "低优先级", "备选"]
    hits: list[str] = []
    for pattern in patterns:
        target = pattern.lower()
        start = lowered.find(target)
        while start >= 0:
            prefix = lowered[max(0, start - 24):start]
            if not any(guard in prefix for guard in guards):
                hits.append(pattern)
                break
            start = lowered.find(target, start + len(target))
    return hits


def dry_answer(group: str, turn: dict[str, Any]) -> str:
    if group == "baseline":
        if "Backbone" in turn["task"]:
            return "可以评估更强 Backbone，例如 Transformer 或 ViT，但需要先判断成本。"
        return "建议先做错误分析，并重新尝试 Focal Loss、阈值调整，同时观察 Recall 和 Precision。"
    if group == "memory":
        return (
            "根据摘要记忆，先不要把 Focal Loss 当主线，也倾向暂不更换 Backbone。"
            "下一步可以做 MLD / 海流变量对照，记录事件级 Recall 与 Precision。"
        )
    return (
        "依据 Soul Current State，保持现有 Backbone，不回到 Focal Loss 主线。"
        "下一步跑 MLD / 海流变量对照，按漏检类型记录事件级 Recall、Precision，并形成 checklist。"
    )


def run_group(
    args: argparse.Namespace,
    output_dir: Path,
    configs: dict[str, Path],
    turn: dict[str, Any],
    group: str,
    memory_items: list[str],
) -> dict[str, Any]:
    raw_path = output_dir / "raw" / f"{turn['id']}_{group}.jsonl"
    if args.dry_run:
        answer = dry_answer(group, turn)
        return {
            "group": group,
            "ok": True,
            "dry_run": True,
            "answer": answer,
            "score": score_answer(answer, turn),
            "soul_injection": "",
            "returncode": 0,
            "stderr": "",
        }
    task = memory_task(turn, memory_items) if group == "memory" else plain_task(turn)
    config = configs["soul"] if group == "soul" else configs["baseline"]
    result = run_harness(args, config, task, raw_path)
    parsed = parse_harness_output(result.stdout)
    answer = parsed["assistant_text"]
    return {
        "group": group,
        "ok": result.ok,
        "dry_run": False,
        "answer": answer,
        "score": score_answer(answer, turn) if result.ok else {"score": 0, "positive_hits": [], "negative_hits": []},
        "soul_injection": parsed["soul_injection"],
        "event_count": parsed["event_count"],
        "returncode": result.returncode,
        "stderr": result.stderr,
    }


def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir
    project_dir = prepare_output_dir(output_dir)
    initial_demo_state(project_dir)

    configs = {
        "baseline": output_dir / "configs" / "baseline.cordis.yml",
        "soul": output_dir / "configs" / "soul.cordis.yml",
    }
    write_config(configs["baseline"], with_soul=False, sessions_root=".sessions-soul-state-ab-baseline")
    write_config(configs["soul"], with_soul=True, sessions_root=".sessions-soul-state-ab-soul")

    api_process: subprocess.Popen[str] | None = None
    if not args.dry_run:
        api_process = start_soul_api(project_dir)
        wait_for_api()

    memory_items: list[str] = []
    rounds: list[dict[str, Any]] = []
    try:
        for turn in TURNS:
            patch = append_patch(project_dir, turn["learn_evidence"])
            memory_items.append(turn["memory"])
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    executor.submit(run_group, args, output_dir, configs, turn, group, list(memory_items)): group
                    for group in ("baseline", "memory", "soul")
                }
                groups = {futures[future]: future.result() for future in as_completed(futures)}
            rounds.append(
                {
                    "id": turn["id"],
                    "learn_evidence": turn["learn_evidence"],
                    "patch": patch,
                    "memory_items": list(memory_items),
                    "task": turn["task"],
                    "groups": groups,
                }
            )
    finally:
        if api_process is not None:
            api_process.terminate()
            try:
                api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api_process.kill()

    result = {
        "metadata": build_metadata(args, output_dir),
        "rounds": rounds,
        "summary": summarize(rounds),
        "final_state": load_state(project_dir),
    }
    write_json(output_dir / "metadata.json", result["metadata"] | {"summary": result["summary"]})
    write_json(output_dir / "results.json", result)
    (output_dir / "results.md").write_text(render_report(result), encoding="utf-8")
    return result


def build_metadata(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    return {
        "run_at": utc_now(),
        "harness_dir": str(args.harness_dir),
        "output_dir": str(output_dir),
        "model": "deepseek-v4-flash",
        "dry_run": args.dry_run,
        "groups": ["baseline", "memory", "soul"],
        "fairness_controls": {
            "same_harness": True,
            "same_model": True,
            "same_persona": True,
            "same_project_context": True,
            "same_eval_tasks": True,
            "baseline_variable": "no prior memory",
            "memory_variable": "ordinary natural-language summary memory",
            "soul_variable": "Harness plugin injected Current State plus turn-end evidence proposal",
        },
    }


def summarize(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    groups = ("baseline", "memory", "soul")
    totals = {
        group: [round_item["groups"][group]["score"]["score"] for round_item in rounds]
        for group in groups
    }
    return {
        group: {
            "scores": totals[group],
            "avg": round(sum(totals[group]) / max(1, len(totals[group])), 2),
        }
        for group in groups
    } | {
        "soul_minus_baseline_avg": round(
            (sum(totals["soul"]) - sum(totals["baseline"])) / max(1, len(rounds)), 2
        ),
        "soul_minus_memory_avg": round(
            (sum(totals["soul"]) - sum(totals["memory"])) / max(1, len(rounds)), 2
        ),
        "any_failed": any(
            not round_item["groups"][group]["ok"]
            for round_item in rounds
            for group in groups
        ),
    }


def render_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Soul / DeepSeek Harness State A/B v0.2",
        "",
        f"- dry_run: `{result['metadata']['dry_run']}`",
        f"- model: `{result['metadata']['model']}`",
        f"- baseline avg: {summary['baseline']['avg']} / 6",
        f"- memory avg: {summary['memory']['avg']} / 6",
        f"- soul avg: {summary['soul']['avg']} / 6",
        f"- Soul - Baseline avg: {summary['soul_minus_baseline_avg']}",
        f"- Soul - Memory avg: {summary['soul_minus_memory_avg']}",
        "",
        "## Fairness",
        "",
        "三组使用同一个 DeepSeek Harness、同一个 DeepSeek 模型、同一个 persona、同一个项目背景和同一组当前任务。差异只在历史状态形式：Baseline 没有前置历史，Memory 有普通摘要，Soul 由 Harness 插件注入结构化 Current State，并在 turn/end 产生 patch proposal。",
        "",
    ]
    for round_item in result["rounds"]:
        lines.extend(
            [
                f"## {round_item['id']}",
                "",
                f"- Evidence: {round_item['learn_evidence']}",
                f"- Applied patch version: {round_item['patch']['new_state_version']}",
                f"- Task: {round_item['task']}",
                "",
                "| Group | Score | Positive Hits | Negative Hits |",
                "|---|---:|---|---|",
            ]
        )
        for group in ("baseline", "memory", "soul"):
            row = round_item["groups"][group]
            score = row["score"]
            lines.append(
                f"| {group} | {score['score']} / 6 | {', '.join(score['positive_hits']) or '-'} | {', '.join(score['negative_hits']) or '-'} |"
            )
        for group in ("baseline", "memory", "soul"):
            answer = round_item["groups"][group]["answer"].replace("\n", " ")
            lines.extend(["", f"### {group}", "", f"> {answer}"])
        soul_injection = round_item["groups"]["soul"].get("soul_injection") or ""
        if soul_injection:
            lines.extend(["", "### Soul Injected Current State", "", "```text", soul_injection, "```"])
        lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run three-round DSH parallel state A/B experiment.")
    parser.add_argument("--harness-dir", type=Path, default=DEFAULT_HARNESS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--node-bin", type=Path, default=DEFAULT_NODE_BIN)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.dry_run and not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEP_SEEK_API_KEY")):
        raise SystemExit("DEEPSEEK_API_KEY is not set. Use --dry-run for local closure.")
    result = run_experiment(args)
    print(json.dumps({"ok": True, "summary": result["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
