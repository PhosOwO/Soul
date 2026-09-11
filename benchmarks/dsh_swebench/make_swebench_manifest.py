from __future__ import annotations

import argparse
import hashlib
import json
import textwrap
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DATASETS = {
    "lite": {
        "hf_id": "princeton-nlp/SWE-bench_Lite",
        "dataset": "swe-bench-lite",
        "split": "test",
    },
    "verified": {
        "hf_id": "princeton-nlp/SWE-bench_Verified",
        "dataset": "swe-bench-verified",
        "split": "test",
    },
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    args = parse_args()
    dataset = DATASETS[args.dataset]
    rows = fetch_rows(dataset["hf_id"], dataset["split"])
    selected = select_rows(rows, args)
    manifest_rows = [to_manifest_row(row, dataset["dataset"], index) for index, row in enumerate(selected)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in manifest_rows) + "\n",
        encoding="utf-8",
    )
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(render_summary(args, rows, manifest_rows), encoding="utf-8")
    print(f"Wrote {len(manifest_rows)} rows to {args.output}")
    if args.summary:
        print(f"Wrote summary to {args.summary}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate DSH runner manifests from external SWE-bench metadata.")
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="lite")
    parser.add_argument("--limit", type=int, default=10, help="Number of instances for independent sampling.")
    parser.add_argument("--seed", default="soulkit-v0", help="Deterministic selection seed.")
    parser.add_argument("--output", type=Path, required=True, help="Output manifest JSONL path.")
    parser.add_argument("--summary", type=Path, default=None, help="Optional Markdown summary path.")
    parser.add_argument("--continuity", action="store_true", help="Select grouped repository continuity instances.")
    parser.add_argument("--repos", type=int, default=3, help="Repository count for continuity mode.")
    parser.add_argument("--per-repo", type=int, default=3, help="Instances per repository for continuity mode.")
    return parser.parse_args()


def fetch_rows(dataset: str, split: str) -> list[dict[str, Any]]:
    offset = 0
    length = 100
    rows: list[dict[str, Any]] = []
    while True:
        query = urllib.parse.urlencode(
            {
                "dataset": dataset,
                "config": "default",
                "split": split,
                "offset": offset,
                "length": length,
            }
        )
        url = f"https://datasets-server.huggingface.co/rows?{query}"
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        batch = [item["row"] for item in payload.get("rows", [])]
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < length:
            break
        offset += length
    if not rows:
        raise SystemExit(f"No rows fetched from {dataset}/{split}")
    return rows


def select_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.continuity:
        return select_continuity_rows(rows, seed=args.seed, repo_count=args.repos, per_repo=args.per_repo)
    ordered = sorted(rows, key=lambda row: stable_key(args.seed, str(row["instance_id"])))
    return ordered[: args.limit]


def select_continuity_rows(
    rows: list[dict[str, Any]],
    seed: str,
    repo_count: int,
    per_repo: int,
) -> list[dict[str, Any]]:
    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_repo[str(row["repo"])].append(row)
    eligible = {
        repo: sorted(repo_rows, key=lambda row: str(row["instance_id"]))
        for repo, repo_rows in by_repo.items()
        if len(repo_rows) >= per_repo
    }
    selected_repos = sorted(eligible, key=lambda repo: stable_key(seed, repo))[:repo_count]
    selected: list[dict[str, Any]] = []
    for repo in selected_repos:
        selected.extend(eligible[repo][:per_repo])
    return selected


def to_manifest_row(row: dict[str, Any], dataset_name: str, sequence_index: int) -> dict[str, Any]:
    instance_id = str(row["instance_id"])
    return {
        "instance_id": instance_id,
        "dataset": dataset_name,
        "repo": row["repo"],
        "base_commit": row["base_commit"],
        "problem_statement": row["problem_statement"],
        "test_command": "official-swebench",
        "group_id": row["repo"],
        "sequence_index": sequence_index,
        "evaluation": {
            "type": "swebench",
            "instance_id": instance_id,
        },
        "metadata": {
            "created_at": row.get("created_at"),
            "version": row.get("version"),
            "difficulty": row.get("difficulty"),
            "fail_to_pass_count": count_json_list(row.get("FAIL_TO_PASS")),
            "pass_to_pass_count": count_json_list(row.get("PASS_TO_PASS")),
        },
    }


def render_summary(args: argparse.Namespace, all_rows: list[dict[str, Any]], manifest_rows: list[dict[str, Any]]) -> str:
    by_repo: dict[str, int] = defaultdict(int)
    for row in manifest_rows:
        by_repo[str(row["repo"])] += 1
    lines = [
        f"# {args.dataset.title()} Manifest Summary",
        "",
        f"- Generated: `{utc_now()}`",
        f"- Source dataset: `{DATASETS[args.dataset]['hf_id']}`",
        f"- Source split: `{DATASETS[args.dataset]['split']}`",
        f"- Source rows available: `{len(all_rows)}`",
        f"- Selected rows: `{len(manifest_rows)}`",
        f"- Selection seed: `{args.seed}`",
        f"- Continuity mode: `{args.continuity}`",
        "",
        "## Repository Distribution",
        "",
        "| Repo | Count |",
        "| --- | ---: |",
    ]
    for repo, count in sorted(by_repo.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| `{repo}` | {count} |")
    lines.extend(
        [
            "",
            "## Instances",
            "",
            "| # | Instance | Repo | Summary | F2P | P2P | Difficulty |",
            "| ---: | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for index, row in enumerate(manifest_rows, start=1):
        meta = row.get("metadata", {})
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{row['instance_id']}`",
                    f"`{row['repo']}`",
                    escape_table(summarize_problem(str(row["problem_statement"]))),
                    str(meta.get("fail_to_pass_count") or 0),
                    str(meta.get("pass_to_pass_count") or 0),
                    f"`{meta.get('difficulty') or 'n/a'}`",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "These rows are selected from an external benchmark dataset. They are not locally invented tasks.",
            "The manifest intentionally omits gold patches, test patches, and hints from the agent prompt path.",
        ]
    )
    return "\n".join(lines) + "\n"


def stable_key(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()


def summarize_problem(problem: str, width: int = 150) -> str:
    lines = [line.strip() for line in problem.splitlines() if line.strip()]
    first = lines[0] if lines else ""
    return textwrap.shorten(first, width=width, placeholder="...")


def count_json_list(raw: Any) -> int:
    if not raw:
        return 0
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        return len(parsed) if isinstance(parsed, list) else 0
    return 0


def escape_table(value: str) -> str:
    return value.replace("|", "\\|")


if __name__ == "__main__":
    raise SystemExit(main())
