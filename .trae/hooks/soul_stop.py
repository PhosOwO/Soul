from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    add_soul_package_path(Path.cwd())
    from soul.hooks.runtime import read_payload, run_stop_hook, write_json

    write_json(run_stop_hook(read_payload(), host="traex").output)


def add_soul_package_path(project_dir: Path) -> None:
    package_path = find_soul_package_path(project_dir)
    sys.path.insert(0, str(package_path or project_dir))


def find_soul_package_path(project_dir: Path) -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("SOULKIT_HOME")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(project_dir / "node_modules" / "@soulkit" / "soul")
    candidates.append(Path(__file__).resolve().parents[2])

    for candidate in candidates:
        if (candidate / "soul" / "api.py").exists():
            return candidate
    return None


if __name__ == "__main__":
    main()
