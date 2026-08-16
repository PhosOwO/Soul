from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReMeJobResult:
    job: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    answer: str
    metadata: dict[str, Any]


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

    def search(self, *, query: str, limit: int = 5) -> ReMeJobResult:
        return self._run_job("search", query=query, limit=limit)

    def read(self, *, path: str) -> ReMeJobResult:
        return self._run_job("read", path=path)

    def _run_job(self, job: str, **kwargs: Any) -> ReMeJobResult:
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
        refs.append(
            {
                "type": "reme_file_chunk",
                "path": path,
                "chunk_id": item.get("id", ""),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "score": item.get("score") or item.get("scores", {}).get("score"),
            }
        )
    return refs
