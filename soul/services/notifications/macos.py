from __future__ import annotations

import json
import subprocess
from typing import Any


class MacOSNotifier:
    def send(self, title: str, body: str) -> dict[str, Any]:
        script = f"display notification {applescript_string(body)} with title {applescript_string(title)}"
        try:
            completed = subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)
        except OSError as exc:
            return {"attempted": True, "delivered": False, "platform": "darwin", "error": str(exc)}
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "").strip()
            return {"attempted": True, "delivered": False, "platform": "darwin", "error": error}
        return {"attempted": True, "delivered": True, "platform": "darwin"}


def applescript_string(value: str) -> str:
    return json.dumps(value)
