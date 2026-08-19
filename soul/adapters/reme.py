from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from soul.services.reme.runtime_config import build_reme_subprocess_env


@dataclass(frozen=True)
class ReMeJobResult:
    job: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    answer: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ReMePreflightResult:
    ok: bool
    cli_path: str | None
    workspace_dir: Path
    message: str
    service_ok: bool | None = None
    service_stdout: str = ""
    service_stderr: str = ""


class ReMeCliAdapter:
    """Thin adapter over ReMe's public CLI job interface."""

    def __init__(self, project_dir: Path, workspace_dir: Path | None = None) -> None:
        self.project_dir = project_dir
        self.workspace_dir = workspace_dir or project_dir / ".soul" / "reme"

    def daily_write(
        self,
        *,
        name: str,
        description: str,
        session_id: str,
        content: str,
        date: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ReMeJobResult:
        return self._run_job(
            "daily_write",
            name=name,
            description=description,
            session_id=session_id,
            content=content,
            date=date,
            metadata=metadata or {},
        )

    def auto_memory(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        memory_hint: str = "",
        date: str = "",
    ) -> ReMeJobResult:
        return self._run_command(
            "auto_memory",
            session_id=session_id,
            messages=messages,
            memory_hint=memory_hint,
            date=date,
        )

    def search(self, *, query: str, limit: int = 5) -> ReMeJobResult:
        return self._run_command("search", query=query, limit=limit)

    def read(self, *, path: str, start_line: int | None = None, end_line: int | None = None) -> ReMeJobResult:
        kwargs: dict[str, Any] = {"path": path}
        if start_line is not None:
            kwargs["start_line"] = start_line
        if end_line is not None:
            kwargs["end_line"] = end_line
        return self._run_command("read", **kwargs)

    def traverse(self, *, path: str, depth: int = 1, direction: str = "both") -> ReMeJobResult:
        return self._run_command("traverse", path=path, depth=depth, direction=direction)

    def auto_dream(
        self,
        *,
        date: str = "",
        hint: str = "",
        scan_days: int | None = None,
        max_units: int | None = None,
    ) -> ReMeJobResult:
        kwargs: dict[str, Any] = {"date": date, "hint": hint}
        if scan_days is not None:
            kwargs["scan_days"] = scan_days
        if max_units is not None:
            kwargs["max_units"] = max_units
        return self._run_command("auto_dream", **kwargs)

    def proactive(self, *, date: str = "", include_content: bool = True) -> ReMeJobResult:
        return self._run_command("proactive", date=date, include_content=include_content)

    def reindex(self) -> ReMeJobResult:
        return self._run_command("reindex")

    def start_service(self, *, host: str = "127.0.0.1", port: int = 2333) -> int:
        result = self.check_preflight(create_workspace=True, check_service=False)
        if result.cli_path is None:
            raise RuntimeError(result.message)
        command = [
            "reme",
            "start",
            f"workspace_dir={self.workspace_dir}",
            "enable_logo=false",
            "log_to_console=true",
            "log_to_file=false",
            f"service.host={host}",
            f"service.port={port}",
        ]
        completed = subprocess.run(command, cwd=self.project_dir, env=build_reme_subprocess_env(self.project_dir), check=False)
        return completed.returncode

    def _run_job(self, job: str, **kwargs: Any) -> ReMeJobResult:
        self.preflight()
        command = [
            "reme",
            "start",
            f"job={job}",
            f"workspace_dir={self.workspace_dir}",
            "enable_logo=false",
            "log_to_console=false",
            "log_to_file=false",
            "service.show_metadata=true",
        ]
        for key, value in kwargs.items():
            command.append(f"{key}={format_reme_cli_value(value)}")

        completed = subprocess.run(
            command,
            cwd=self.project_dir,
            env=build_reme_subprocess_env(self.project_dir),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        answer, metadata = split_answer_metadata(completed.stdout)
        result = ReMeJobResult(
            job=job,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            answer=answer,
            metadata=metadata,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"ReMe job failed: {job}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
        return result

    def _run_command(self, command_name: str, **kwargs: Any) -> ReMeJobResult:
        return self._run_job(command_name, **kwargs)

    def preflight(self, *, check_service: bool = False) -> None:
        result = self.check_preflight(create_workspace=True, check_service=check_service)
        if not result.ok:
            raise RuntimeError(result.message)

    def check_preflight(self, *, create_workspace: bool = False, check_service: bool = False) -> ReMePreflightResult:
        cli_path = shutil.which("reme")
        if cli_path is None:
            return ReMePreflightResult(
                ok=False,
                cli_path=None,
                workspace_dir=self.workspace_dir,
                message=(
                    "ReMe CLI is not available on PATH. Install ReMe or configure PATH before using "
                    "soul_reme memory mode."
                ),
            )
        if create_workspace:
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
        if check_service:
            completed = subprocess.run(
                [
                    "reme",
                    "find_reme",
                    f"workspace_dir={self.workspace_dir}",
                    "enable_logo=false",
                    "log_to_console=false",
                    "log_to_file=false",
                    "service.show_metadata=true",
                ],
                cwd=self.project_dir,
                env=build_reme_subprocess_env(self.project_dir),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                hint = " Try `reme start` before using Soul ReMe-backed evidence writes."
                return ReMePreflightResult(
                    ok=False,
                    cli_path=cli_path,
                    workspace_dir=self.workspace_dir,
                    message=f"ReMe service is not available. {detail}{hint}",
                    service_ok=False,
                    service_stdout=completed.stdout,
                    service_stderr=completed.stderr,
                )
        return ReMePreflightResult(
            ok=True,
            cli_path=cli_path,
            workspace_dir=self.workspace_dir,
            message=f"ReMe CLI found: {cli_path}",
            service_ok=True if check_service else None,
        )


def format_reme_cli_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def split_answer_metadata(stdout: str) -> tuple[str, dict[str, Any]]:
    lines = stdout.strip().splitlines()
    for index in range(len(lines) - 1, -1, -1):
        candidate = lines[index].strip()
        if not (candidate.startswith("{") and candidate.endswith("}")):
            continue
        try:
            metadata = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return "\n".join(lines[:index]).strip(), metadata
    return stdout.strip(), {}


def reme_evidence_refs(search_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in search_metadata.get("results", []):
        path = str(item.get("path", "")).replace("\\", "/")
        if not path:
            continue
        ref = {
            "type": "reme_file_chunk",
            "path": path,
            "chunk_id": item.get("id", ""),
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
            "score": item.get("score") or item.get("scores", {}).get("score"),
        }
        if item.get("source_conversation"):
            ref["source_conversation"] = str(item["source_conversation"]).replace("\\", "/")
        refs.append(ref)
    return refs


def reme_write_refs(write_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for path in sorted(extract_reme_paths(write_metadata)):
        refs.append({"type": "reme_file", "path": path})
    return refs


def extract_reme_paths(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {
                "path",
                "daily_path",
                "source_conversation",
                "source_path",
                "file_path",
            } and isinstance(nested, str):
                normalized = normalize_reme_path(nested)
                if normalized:
                    paths.add(normalized)
            else:
                paths.update(extract_reme_paths(nested))
    elif isinstance(value, list):
        for item in value:
            paths.update(extract_reme_paths(item))
    return paths


def normalize_reme_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if normalized.startswith("[[") and normalized.endswith("]]"):
        normalized = normalized[2:-2].strip()
    return normalized
