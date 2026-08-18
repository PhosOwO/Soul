from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    add_soul_package_path(Path.cwd())
    from soul.hooks.runtime import read_payload, run_user_prompt_submit_hook, write_json

    write_json(run_user_prompt_submit_hook(read_payload(), host="traex").output)


def add_soul_package_path(project_dir: Path) -> None:
    candidates: list[Path] = []
    configured = os.environ.get("SOULKIT_HOME")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(project_dir / "node_modules" / "@soulkit" / "soul")
    candidates.append(Path(__file__).resolve().parents[2])

    for candidate in candidates:
        if (candidate / "soul" / "api.py").exists():
            sys.path.insert(0, str(candidate))
            return

    sys.path.insert(0, str(project_dir))


if __name__ == "__main__":
    main()
