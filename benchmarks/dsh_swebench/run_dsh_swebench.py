from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib import request
from urllib.error import URLError

from soul.services.shared.state_types import StateDoc
from soul.services.state_core.state_store import save_state


BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parents[1]
DEFAULT_RESULTS_DIR = BENCH_DIR / "results" / "latest"
DEFAULT_NODE22_BIN = Path("/Users/bytedance/.nvm/versions/node/v22.23.1/bin")
SUPPORTED_ARMS = ("baseline", "capture_only", "soul_active")
REQUIRED_MANIFEST_FIELDS = (
    "instance_id",
    "dataset",
    "repo",
    "problem_statement",
    "group_id",
    "sequence_index",
    "evaluation",
)


@dataclass(frozen=True)
class ArmConfig:
    name: str
    inject_accepted_state: bool
    capture_evidence: bool


@dataclass(frozen=True)
class PlannedRun:
    instance: dict[str, Any]
    arm: ArmConfig
    instance_dir: Path
    arm_dir: Path
    patch_path: Path
    cordis_patch_path: Path
    soul_project_dir: Path
    workspace_dir: Path
    dsh_command: list[str]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    arms = parse_arms(args.arms)
    manifest_path = args.manifest.resolve()
    rows = load_manifest(manifest_path)
    if args.instance_ids:
        wanted_ids = {item.strip() for item in args.instance_ids.split(",") if item.strip()}
        rows = [row for row in rows if str(row["instance_id"]) in wanted_ids]
        missing_ids = sorted(wanted_ids - {str(row["instance_id"]) for row in rows})
        if missing_ids:
            raise SystemExit(f"Requested instance_id(s) not found in manifest: {', '.join(missing_ids)}")
    if args.max_instances is not None:
        rows = rows[: args.max_instances]
    if not rows:
        raise SystemExit("No instances selected for this run.")

    output_dir = args.output_dir.resolve()
    if output_dir.exists() and not args.resume and not args.dry_run:
        raise SystemExit(f"Output directory already exists. Use --resume or choose another path: {output_dir}")
    if args.workspace_root is None:
        args.workspace_root = output_dir / "workspaces"
    if args.repo_cache is None:
        args.repo_cache = output_dir / "repo_cache"
    run_metadata = build_run_metadata(args, manifest_path, rows, arms)
    planned_runs = [
        plan_run(row, arm, output_dir, args)
        for row in rows
        for arm in arms
    ]

    if args.dry_run:
        print_dry_run(run_metadata, planned_runs)
        return 0

    write_run_scaffold(output_dir, manifest_path, run_metadata, planned_runs, seed_accepted_state=args.seed_accepted_state)
    if args.prepare_workspaces:
        prepare_workspaces(planned_runs, args)
    for planned in planned_runs:
        if args.resume and (planned.arm_dir / "metrics.json").exists():
            continue
        run_one(planned, args)
    write_predictions(output_dir, rows, arms)
    if args.evaluate:
        check_evaluator_available()
        run_evaluation(output_dir, rows, arms, args)
    write_report(output_dir, run_metadata, rows, arms)
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run external coding benchmark instances through DSH experiment arms.",
    )
    parser.add_argument("--manifest", type=Path, required=True, help="JSONL manifest of external benchmark instances.")
    parser.add_argument("--arms", default="baseline,soul_active", help="Comma-separated arms to run.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS_DIR, help="Artifact output directory.")
    parser.add_argument("--model", default=None, help="DSH model/profile reference recorded in metadata.")
    parser.add_argument("--timeout-seconds", type=int, default=1800, help="Per-task DSH timeout.")
    parser.add_argument("--max-instances", type=int, default=None, help="Limit instances for development runs.")
    parser.add_argument("--instance-ids", default="", help="Optional comma-separated allowlist of instance IDs from the manifest.")
    parser.add_argument("--resume", action="store_true", help="Skip arm runs that already have metrics.json.")
    parser.add_argument("--dry-run", action="store_true", help="Validate manifest and print planned runs without executing DSH.")
    parser.add_argument("--dsh-bin", default="dsh", help="DSH executable name or path.")
    parser.add_argument("--dsh-profile", default="headless", help="DSH profile used for non-interactive task execution.")
    parser.add_argument("--workspace-root", type=Path, default=None, help="Prepared benchmark workspaces root.")
    parser.add_argument("--prepare-workspaces", action="store_true", help="Clone and checkout per-arm workspaces before running DSH.")
    parser.add_argument("--force-prepare", action="store_true", help="Delete and recreate existing prepared workspaces.")
    parser.add_argument("--repo-cache", type=Path, default=None, help="Git clone cache root. Defaults to <output-dir>/repo_cache.")
    parser.add_argument("--dsh-home", type=Path, default=None, help="Optional DSH_HOME override. Defaults to DSH's normal user home so configured credentials and installed plugins are reused.")
    parser.add_argument("--soul-api-host", default="127.0.0.1", help="Host for runner-managed Soul API processes.")
    parser.add_argument("--soul-api-port", type=int, default=0, help="Port for runner-managed Soul API. Defaults to an available local port per run.")
    parser.add_argument("--evaluate", action="store_true", help="Run the official SWE-bench evaluator for generated predictions.")
    parser.add_argument("--swebench-dataset-name", default=None, help="Official SWE-bench dataset name for evaluator.")
    parser.add_argument("--eval-max-workers", type=int, default=2, help="SWE-bench evaluator max_workers.")
    parser.add_argument("--eval-timeout", type=int, default=1800, help="SWE-bench evaluator timeout per instance.")
    parser.add_argument("--seed-accepted-state", type=Path, default=None, help="Optional state.json template copied into inject-enabled Soul projects before DSH runs.")
    return parser.parse_args(argv)


def parse_arms(raw: str) -> list[ArmConfig]:
    selected = [item.strip() for item in raw.split(",") if item.strip()]
    if not selected:
        raise SystemExit("At least one arm is required.")
    unknown = [arm for arm in selected if arm not in SUPPORTED_ARMS]
    if unknown:
        raise SystemExit(f"Unknown arm(s): {', '.join(unknown)}. Supported: {', '.join(SUPPORTED_ARMS)}")
    configs = {
        "baseline": ArmConfig("baseline", inject_accepted_state=False, capture_evidence=False),
        "capture_only": ArmConfig("capture_only", inject_accepted_state=False, capture_evidence=True),
        "soul_active": ArmConfig("soul_active", inject_accepted_state=True, capture_evidence=True),
    }
    return [configs[name] for name in selected]


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Manifest does not exist: {path}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
        validate_manifest_row(row, path, line_number)
        instance_id = str(row["instance_id"])
        if instance_id in seen:
            raise SystemExit(f"Duplicate instance_id at {path}:{line_number}: {instance_id}")
        seen.add(instance_id)
        rows.append(row)
    if not rows:
        raise SystemExit(f"Manifest contains no runnable instances: {path}")
    return rows


def validate_manifest_row(row: dict[str, Any], path: Path, line_number: int) -> None:
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in row]
    if missing:
        raise SystemExit(f"Missing field(s) at {path}:{line_number}: {', '.join(missing)}")
    if not isinstance(row["evaluation"], dict) or not row["evaluation"].get("type"):
        raise SystemExit(f"Invalid evaluation object at {path}:{line_number}")
    if not isinstance(row["sequence_index"], int):
        raise SystemExit(f"sequence_index must be an integer at {path}:{line_number}")
    if "base_commit" not in row and "bug_id" not in row:
        raise SystemExit(f"Manifest row needs base_commit or bug_id at {path}:{line_number}")


def build_run_metadata(
    args: argparse.Namespace,
    manifest_path: Path,
    rows: list[dict[str, Any]],
    arms: list[ArmConfig],
) -> dict[str, Any]:
    return {
        "benchmark": "dsh_swebench",
        "started_at": utc_now(),
        "repo_root": str(REPO_ROOT),
        "soulkit_commit": git_value(["git", "rev-parse", "HEAD"]),
        "dsh_version": command_value(dsh_env(), [args.dsh_bin, "--version"]),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "instance_count": len(rows),
        "arms": [arm.name for arm in arms],
        "model": args.model,
        "timeout_seconds": args.timeout_seconds,
        "dsh_profile": args.dsh_profile,
        "workspace_root": str(args.workspace_root.resolve()) if args.workspace_root else None,
        "repo_cache": str(args.repo_cache.resolve()) if args.repo_cache else None,
        "dsh_home": str(args.dsh_home.resolve()) if args.dsh_home else None,
        "prepare_workspaces": args.prepare_workspaces,
        "evaluate": args.evaluate,
        "swebench_dataset_name": args.swebench_dataset_name,
        "seed_accepted_state": str(args.seed_accepted_state.resolve()) if args.seed_accepted_state else None,
    }


def plan_run(
    row: dict[str, Any],
    arm: ArmConfig,
    output_dir: Path,
    args: argparse.Namespace,
) -> PlannedRun:
    instance_id = safe_path_name(str(row["instance_id"]))
    instance_dir = output_dir / "instances" / instance_id
    arm_dir = instance_dir / arm.name
    workspace = resolve_workspace(row, arm, args)
    soul_project_dir = arm_dir / "soul_project"
    cordis_patch_path = arm_dir / "soul.patch.yml"
    patch_path = arm_dir / "patch.diff"
    task = build_task_prompt(row)
    command = [
        args.dsh_bin,
        "--profile",
        args.dsh_profile,
        "--patch",
        str(cordis_patch_path),
        task,
    ]
    return PlannedRun(
        instance=row,
        arm=arm,
        instance_dir=instance_dir,
        arm_dir=arm_dir,
        patch_path=patch_path,
        cordis_patch_path=cordis_patch_path,
        soul_project_dir=soul_project_dir,
        workspace_dir=workspace,
        dsh_command=command,
    )


def resolve_workspace(row: dict[str, Any], arm: ArmConfig, args: argparse.Namespace) -> Path:
    if row.get("workspace"):
        return (Path(str(row["workspace"])).resolve() / arm.name)
    return (args.workspace_root / safe_path_name(str(row["instance_id"])) / arm.name).resolve()


def build_task_prompt(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"External benchmark instance: {row['instance_id']}",
            f"Dataset: {row['dataset']}",
            f"Repository: {row['repo']}",
            "",
            "Solve the issue in the current workspace. Make code changes only as needed.",
            "When finished, leave the final patch in the git working tree and summarize what changed.",
            "",
            "Problem statement:",
            str(row["problem_statement"]).strip(),
        ]
    )


def write_run_scaffold(
    output_dir: Path,
    manifest_path: Path,
    metadata: dict[str, Any],
    planned_runs: list[PlannedRun],
    *,
    seed_accepted_state: Path | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "predictions").mkdir(exist_ok=True)
    (output_dir / "evaluation").mkdir(exist_ok=True)
    shutil.copyfile(manifest_path, output_dir / "manifest.jsonl")
    (output_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for planned in planned_runs:
        planned.arm_dir.mkdir(parents=True, exist_ok=True)
        planned.soul_project_dir.mkdir(parents=True, exist_ok=True)
        (planned.arm_dir / "soul_state_before").mkdir(exist_ok=True)
        (planned.arm_dir / "soul_state_after").mkdir(exist_ok=True)
        write_cordis_patch(planned)
        if seed_accepted_state is not None and planned.arm.inject_accepted_state:
            seed_soul_state(planned.soul_project_dir, seed_accepted_state)


def seed_soul_state(project_dir: Path, seed_path: Path) -> None:
    if not seed_path.exists():
        raise SystemExit(f"Seed accepted state file does not exist: {seed_path}")
    state = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise SystemExit(f"Seed accepted state file must contain a JSON object: {seed_path}")
    save_state(cast(StateDoc, state), project_dir)


def write_cordis_patch(planned: PlannedRun, soul_api_url: str | None = None) -> None:
    if planned.arm.name == "baseline" and not planned.arm.capture_evidence:
        text = "\n".join(
            [
                "- id: soul",
                "  disabled: true",
                "",
            ]
        )
    else:
        text = "\n".join(
            [
                "- id: soul",
                '  name: "@soulkit/soul/dsh"',
                "  disabled: false",
                "  config:",
                f'    baseUrl: "{soul_api_url or "http://127.0.0.1:8765"}"',
                "    autoStart: false",
                f'    projectDir: "{planned.soul_project_dir}"',
                f"    injectAcceptedState: {str(planned.arm.inject_accepted_state).lower()}",
                "    stateLimit: 6",
                "    maxStateChars: 6000",
                "",
            ]
        )
    planned.cordis_patch_path.write_text(text, encoding="utf-8")


def prepare_workspaces(planned_runs: list[PlannedRun], args: argparse.Namespace) -> None:
    args.workspace_root.mkdir(parents=True, exist_ok=True)
    args.repo_cache.mkdir(parents=True, exist_ok=True)
    prepared: set[Path] = set()
    for planned in planned_runs:
        if planned.workspace_dir in prepared:
            continue
        prepare_workspace(planned, args)
        prepared.add(planned.workspace_dir)


def prepare_workspace(planned: PlannedRun, args: argparse.Namespace) -> None:
    repo = str(planned.instance["repo"])
    base_commit = str(planned.instance.get("base_commit") or "")
    if not base_commit:
        raise SystemExit(f"Cannot prepare workspace without base_commit: {planned.instance['instance_id']}")
    cache_dir = args.repo_cache / f"{safe_path_name(repo)}.git"
    ensure_repo_cache(planned.instance, cache_dir)
    if planned.workspace_dir.exists():
        if args.force_prepare:
            shutil.rmtree(planned.workspace_dir)
        else:
            return
    planned.workspace_dir.parent.mkdir(parents=True, exist_ok=True)
    run_checked(["git", "clone", str(cache_dir), str(planned.workspace_dir)], cwd=REPO_ROOT)
    run_checked(["git", "checkout", base_commit], cwd=planned.workspace_dir)
    run_checked(["git", "submodule", "update", "--init", "--recursive"], cwd=planned.workspace_dir, allow_failure=True)


def ensure_repo_cache(instance: dict[str, Any], cache_dir: Path) -> None:
    repo = str(instance["repo"])
    repo_url = str(instance.get("repo_url") or f"https://github.com/{repo}.git")
    if cache_dir.exists():
        run_checked(["git", "fetch", "--all", "--tags", "--prune"], cwd=cache_dir)
        return
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    run_checked(["git", "clone", "--mirror", repo_url, str(cache_dir)], cwd=REPO_ROOT)


def run_one(planned: PlannedRun, args: argparse.Namespace) -> None:
    workspace = planned.workspace_dir
    if not workspace.exists():
        write_metrics(
            planned,
            {
                "status": "workspace_missing",
                "workspace": str(workspace),
                "message": "Workspace preparation is not implemented in the runner skeleton.",
            },
        )
        return

    env = dsh_env(args.dsh_home)
    soul_api_process: subprocess.Popen[str] | None = None
    if planned.arm.capture_evidence:
        soul_api_url, soul_api_process = start_soul_api(planned, args, env)
        write_cordis_patch(planned, soul_api_url)
    else:
        write_cordis_patch(planned)
    started = utc_now()
    stdout_path = planned.arm_dir / "stdout.log"
    stderr_path = planned.arm_dir / "stderr.log"
    try:
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            try:
                completed = subprocess.run(
                    planned.dsh_command,
                    cwd=workspace,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    timeout=args.timeout_seconds,
                    check=False,
                )
                status = "ok" if completed.returncode == 0 else "failed"
                returncode = completed.returncode
            except subprocess.TimeoutExpired:
                status = "timeout"
                returncode = None
    finally:
        if soul_api_process is not None:
            stop_soul_api(soul_api_process, soul_api_url)

    capture_patch(workspace, planned.patch_path, env)
    write_metrics(
        planned,
        {
            "status": status,
            "returncode": returncode,
            "started_at": started,
            "finished_at": utc_now(),
            "workspace": str(workspace),
            "command": planned.dsh_command,
            "patch_path": str(planned.patch_path),
        },
    )


def capture_patch(workspace: Path, patch_path: Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=workspace,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    patch_path.write_text(result.stdout, encoding="utf-8")


def start_soul_api(
    planned: PlannedRun,
    args: argparse.Namespace,
    env: dict[str, str],
) -> tuple[str, subprocess.Popen[str]]:
    port = args.soul_api_port or find_free_port(args.soul_api_host)
    base_url = f"http://{args.soul_api_host}:{port}"
    stdout_path = planned.arm_dir / "soul_api.stdout.log"
    stderr_path = planned.arm_dir / "soul_api.stderr.log"
    stdout = stdout_path.open("w", encoding="utf-8")
    stderr = stderr_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "soul.api", "--project-dir", str(planned.soul_project_dir), "--host", args.soul_api_host, "--port", str(port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
    finally:
        stdout.close()
        stderr.close()
    wait_for_soul_api(base_url, process)
    return base_url, process


def stop_soul_api(process: subprocess.Popen[str], base_url: str) -> None:
    try:
        request.urlopen(request.Request(f"{base_url}/shutdown", method="POST"), timeout=2).close()
    except URLError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def wait_for_soul_api(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"Soul API exited before becoming healthy for {base_url}.")
        try:
            with request.urlopen(f"{base_url}/health", timeout=0.75) as response:
                if response.status == 200:
                    return
        except URLError:
            time.sleep(0.25)
    process.terminate()
    raise SystemExit(f"Soul API did not become healthy within 10s: {base_url}")


def find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def write_metrics(planned: PlannedRun, payload: dict[str, Any]) -> None:
    planned.arm_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "instance_id": planned.instance["instance_id"],
        "arm": planned.arm.name,
        **payload,
    }
    (planned.arm_dir / "metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_predictions(output_dir: Path, rows: list[dict[str, Any]], arms: list[ArmConfig]) -> None:
    predictions_dir = output_dir / "predictions"
    predictions_dir.mkdir(exist_ok=True)
    for arm in arms:
        predictions: list[dict[str, Any]] = []
        for row in rows:
            patch_path = output_dir / "instances" / safe_path_name(str(row["instance_id"])) / arm.name / "patch.diff"
            patch = patch_path.read_text(encoding="utf-8") if patch_path.exists() else ""
            predictions.append(
                {
                    "instance_id": row["instance_id"],
                    "model_name_or_path": f"dsh-{arm.name}",
                    "model_patch": patch,
                }
            )
        (predictions_dir / f"{arm.name}.json").write_text(
            json.dumps(predictions, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (predictions_dir / f"{arm.name}.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in predictions) + "\n",
            encoding="utf-8",
        )


def run_evaluation(
    output_dir: Path,
    rows: list[dict[str, Any]],
    arms: list[ArmConfig],
    args: argparse.Namespace,
) -> None:
    dataset_name = args.swebench_dataset_name or infer_swebench_dataset_name(rows)
    instance_ids = [str(row["instance_id"]) for row in rows]
    env = evaluator_env(output_dir)
    for arm in arms:
        arm_eval_dir = output_dir / "evaluation" / arm.name
        arm_eval_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            dataset_name,
            "--split",
            "test",
            "--predictions_path",
            str(output_dir / "predictions" / f"{arm.name}.json"),
            "--max_workers",
            str(args.eval_max_workers),
            "--timeout",
            str(args.eval_timeout),
            "--run_id",
            f"{output_dir.name}-{arm.name}",
            "--report_dir",
            str(arm_eval_dir),
            "--instance_ids",
            *instance_ids,
        ]
        started = utc_now()
        stdout_path = arm_eval_dir / "stdout.log"
        stderr_path = arm_eval_dir / "stderr.log"
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
                check=False,
            )
        (arm_eval_dir / "metrics.json").write_text(
            json.dumps(
                {
                    "arm": arm.name,
                    "status": "ok" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "started_at": started,
                    "finished_at": utc_now(),
                    "dataset_name": dataset_name,
                    "instance_ids": instance_ids,
                    "command": command,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def check_evaluator_available() -> None:
    swebench = subprocess.run(
        [sys.executable, "-c", "import swebench"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if swebench.returncode != 0:
        raise SystemExit(
            "SWE-bench evaluator is not installed for this Python. "
            "Install it before using --evaluate, for example: python3 -m pip install swebench"
        )
    docker = shutil.which("docker")
    if not docker:
        raise SystemExit("Docker is not available on PATH. Official SWE-bench local evaluation requires Docker.")
    docker_info = subprocess.run(
        [docker, "info"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if docker_info.returncode != 0:
        raise SystemExit(
            "Docker is installed but the daemon is not reachable. "
            "Start Docker/Colima before using --evaluate.\n"
            f"STDERR:\n{docker_info.stderr}"
        )


def evaluator_env(output_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    cache_root = output_dir / "evaluator_cache"
    env.setdefault("HF_HOME", str(cache_root / "huggingface"))
    env.setdefault("HF_DATASETS_CACHE", str(cache_root / "datasets"))
    env.setdefault("XDG_CACHE_HOME", str(cache_root / "xdg"))
    return env


def infer_swebench_dataset_name(rows: list[dict[str, Any]]) -> str:
    datasets = {str(row["dataset"]) for row in rows}
    if len(datasets) != 1:
        raise SystemExit("Cannot infer one SWE-bench dataset name from mixed manifest rows.")
    dataset = next(iter(datasets))
    if dataset == "swe-bench-lite":
        return "SWE-bench/SWE-bench_Lite"
    if dataset == "swe-bench-verified":
        return "SWE-bench/SWE-bench_Verified"
    raise SystemExit(f"Unknown SWE-bench dataset mapping: {dataset}")


def run_checked(command: list[str], cwd: Path, allow_failure: bool = False) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 and not allow_failure:
        raise SystemExit(
            "Command failed: "
            + shell_join(command)
            + f"\nCWD: {cwd}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def write_report(
    output_dir: Path,
    metadata: dict[str, Any],
    rows: list[dict[str, Any]],
    arms: list[ArmConfig],
) -> None:
    lines = [
        "# DSH SWE-bench Run Report",
        "",
        f"- Started: `{metadata['started_at']}`",
        f"- DSH version: `{metadata.get('dsh_version') or 'unknown'}`",
        f"- SoulKit commit: `{metadata.get('soulkit_commit') or 'unknown'}`",
        f"- Manifest: `{metadata['manifest']}`",
        f"- Instances: `{len(rows)}`",
        f"- Arms: `{', '.join(arm.name for arm in arms)}`",
        "",
        "## Instance Matrix",
        "",
        "| Instance | Dataset | Repo | Group | Arms |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["instance_id"]),
                    str(row["dataset"]),
                    str(row["repo"]),
                    str(row["group_id"]),
                    ", ".join(arm.name for arm in arms),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "This runner writes SWE-bench-style prediction JSONL files. Correctness must still be evaluated by the official external benchmark harness.",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_dry_run(metadata: dict[str, Any], planned_runs: list[PlannedRun]) -> None:
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print("\nPlanned runs:")
    for planned in planned_runs:
        print(f"- {planned.instance['instance_id']} / {planned.arm.name}")
        print(f"  output: {planned.arm_dir}")
        print(f"  soul_project_dir: {planned.soul_project_dir}")
        print(f"  command: {shell_join(planned.dsh_command)}")


def dsh_env(dsh_home: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if DEFAULT_NODE22_BIN.exists():
        env["PATH"] = f"{DEFAULT_NODE22_BIN}:{env.get('PATH', '')}"
    if dsh_home is not None:
        env["DSH_HOME"] = str(dsh_home.resolve())
    for key in list(env):
        if key == "GIT_CONFIG_PARAMETERS" or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_") or key == "GIT_CONFIG_COUNT":
            env.pop(key, None)
    return env


def command_value(env: dict[str, str], command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_value(command: list[str]) -> str | None:
    return command_value(os.environ.copy(), command)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def shell_join(command: list[str]) -> str:
    return " ".join(json.dumps(part) if any(char.isspace() for char in part) else part for part in command)


if __name__ == "__main__":
    raise SystemExit(main())
