from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import NoReturn

from soul.services.codex_workflow import ingest_codex_session
from soul.services.importer import import_codex_session
from soul.services.reflection import reflect_episode
from soul.services.state import (
    apply_patch_proposal,
    append_patch_proposal,
    find_patch_proposal,
    format_state_context,
    load_state,
    propose_patch,
    record_state_event,
    save_state,
)
from soul.storage.database import connect, default_db_path, init_database


def init_command(args: argparse.Namespace) -> None:
    db_path = default_db_path()
    with connect(db_path) as conn:
        init_database(conn, project_name=args.project_name)
    load_state(project_name=args.project_name)
    print(f"Initialized Soul database: {db_path}")


def status_command(_: argparse.Namespace) -> None:
    db_path = default_db_path()
    if not db_path.exists():
        abort("Soul is not initialized. Run `soul init` first.")

    with connect(db_path) as conn:
        init_database(conn)
        project = conn.execute("SELECT name FROM scopes WHERE parent_id IS NULL ORDER BY id LIMIT 1").fetchone()
        episodes = conn.execute(
            "SELECT id, source, summary, created_at FROM episodes ORDER BY id DESC LIMIT 5"
        ).fetchall()
        cognitive_events = conn.execute(
            "SELECT id, type, reason, created_at FROM cognitive_events ORDER BY id DESC LIMIT 5"
        ).fetchall()
        system_events = conn.execute(
            "SELECT id, type, reason, created_at FROM system_events ORDER BY id DESC LIMIT 5"
        ).fetchall()

    print("Soul Status")
    print("")
    print(f"Project: {project['name'] if project else 'unknown'}")
    print("")
    print(format_state_context(load_state(), limit=10))
    print("")
    print("Recent Episodes:")
    for row in episodes:
        print(f"- {row['id']}. [{row['source']}] {row['summary']} ({row['created_at']})")
    if not episodes:
        print("- none")
    print("")
    print("Recent Cognitive Events:")
    for row in cognitive_events:
        reason = f": {row['reason']}" if row["reason"] else ""
        print(f"- {row['id']}. {row['type']}{reason}")
    if not cognitive_events:
        print("- none")
    print("")
    print("Recent System Events:")
    for row in system_events:
        print(f"- {row['id']}. {row['type']} ({row['created_at']})")
    if not system_events:
        print("- none")


def context_command(args: argparse.Namespace) -> None:
    print(format_state_context(load_state(), limit=args.limit))

def state_show_command(args: argparse.Namespace) -> None:
    print(format_state_context(load_state(), limit=args.limit))


def state_diff_command(args: argparse.Namespace) -> None:
    state = load_state()
    evidence = {
        "source": args.source,
        "summary": args.summary,
        "content": args.content or args.summary,
    }
    proposal = propose_patch(state, evidence)
    append_patch_proposal(proposal)
    print(json.dumps(proposal, ensure_ascii=False, indent=2))


def state_apply_command(args: argparse.Namespace) -> None:
    proposal = find_patch_proposal(args.proposal_id)
    state = load_state()
    if proposal.get("base_version") != state.get("version"):
        abort(
            f"Patch base version {proposal.get('base_version')} does not match current state version {state.get('version')}."
        )
    next_state = apply_patch_proposal(state, proposal, confirmed_by=args.confirmed_by)
    save_state(next_state)
    with connect_existing() as conn:
        record_state_event(
            conn,
            "state_patch_applied",
            proposal,
            "soul state apply",
            f"Applied State Patch {args.proposal_id}",
        )
        conn.commit()
    print(f"Applied State Patch: {args.proposal_id}")
    print(f"New State version: {next_state['version']}")


def agent_before_task_command(args: argparse.Namespace) -> None:
    from soul.adapters.agent import SoulAgentAdapter

    with connect_existing() as conn:
        payload = SoulAgentAdapter(conn).before_task(args.task, limit=args.limit)
    print(payload["context"])


def agent_after_task_command(args: argparse.Namespace) -> None:
    from soul.adapters.agent import SoulAgentAdapter

    with connect_existing() as conn:
        payload = SoulAgentAdapter(conn).after_task(
            args.task,
            args.outcome,
            evidence={"source": args.source, "content": args.evidence or args.outcome},
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def api_serve_command(args: argparse.Namespace) -> None:
    from soul.api import serve

    serve(Path(args.project_dir), host=args.host, port=args.port)


def import_codex_command(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    if not path.exists():
        abort(f"Codex session file not found: {path}")

    with connect_existing() as conn:
        episode_id = import_codex_session(conn, path)
        row = conn.execute("SELECT summary, metadata_json FROM episodes WHERE id = ?", (episode_id,)).fetchone()
    print(f"Imported Codex episode: {episode_id}")
    print(f"Summary: {row['summary']}")
    print(f"Metadata: {row['metadata_json']}")


def list_episodes_command(_: argparse.Namespace) -> None:
    with connect_existing() as conn:
        rows = conn.execute(
            "SELECT id, source, summary, created_at FROM episodes ORDER BY id"
        ).fetchall()
    for row in rows:
        print(f"{row['id']}\t{row['source']}\t{row['summary']}\t{row['created_at']}")


def show_episode_command(args: argparse.Namespace) -> None:
    with connect_existing() as conn:
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (args.episode_id,)).fetchone()
    if row is None:
        abort(f"Episode not found: {args.episode_id}")

    messages = json.loads(row["content_json"])
    metadata = json.loads(row["metadata_json"])
    limit = max(args.limit, 0)

    print(f"id: {row['id']}")
    print(f"source: {row['source']}")
    print(f"summary: {row['summary']}")
    print(f"source_path: {row['source_path']}")
    print(f"created_at: {row['created_at']}")
    print(f"metadata: {json.dumps(metadata, ensure_ascii=False)}")
    print("")
    print(f"Messages: {len(messages)}")

    shown_messages = messages[:limit] if limit else messages
    for index, message in enumerate(shown_messages, start=1):
        print("")
        print(f"[{index}] {message.get('role', 'unknown')}")
        timestamp = message.get("timestamp")
        if timestamp:
            print(f"timestamp: {timestamp}")
        print(message.get("text", ""))

    remaining = len(messages) - len(shown_messages)
    if remaining > 0:
        print("")
        print(f"... {remaining} more message(s). Use --limit 0 to show all.")


def reflect_episode_command(args: argparse.Namespace) -> None:
    with connect_existing() as conn:
        try:
            patch_proposal_ids = reflect_episode(conn, args.episode_id, max_patches=args.max_patches)
        except ValueError as exc:
            abort(str(exc))

    print(f"Reflected episode: {args.episode_id}")
    if patch_proposal_ids:
        print("Patch proposals:")
        for proposal_id in patch_proposal_ids:
            print(f"- {proposal_id}")
    else:
        print("Patch proposals: none")


def codex_ingest_command(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    if not path.exists():
        abort(f"Codex session file not found: {path}")

    with connect_existing() as conn:
        result = ingest_codex_session(conn, path, max_patches=args.max_patches)

    action = "Imported" if result.imported else "Updated"
    print(f"{action} Codex episode: {result.episode_id}")
    print(f"Summary: {result.summary}")
    if result.patch_proposal_ids:
        print("Patch proposals:")
        for proposal_id in result.patch_proposal_ids:
            print(f"- {proposal_id}")
    else:
        print("Patch proposals: none")


def connect_existing():
    db_path = default_db_path()
    if not Path(db_path).exists():
        abort("Soul is not initialized. Run `soul init` first.")
    conn = connect(db_path)
    init_database(conn)
    return conn


def abort(message: str) -> NoReturn:
    raise SystemExit(message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soul", description="Soul Core command line interface.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize .brain/soul.db.")
    init_parser.add_argument("--project-name", default="Soul Project")
    init_parser.set_defaults(func=init_command)

    status_parser = subparsers.add_parser("status", help="Print current project cognition.")
    status_parser.set_defaults(func=status_command)

    context_parser = subparsers.add_parser("context", help="Print compact context for Codex.")
    context_parser.add_argument("--limit", type=int, default=10)
    context_parser.set_defaults(func=context_command)

    state_parser = subparsers.add_parser("state", help="Inspect and update Current State.")
    state_subparsers = state_parser.add_subparsers(dest="state_command", required=True)
    state_show = state_subparsers.add_parser("show", help="Print Current State.")
    state_show.add_argument("--limit", type=int, default=10)
    state_show.set_defaults(func=state_show_command)
    state_diff = state_subparsers.add_parser("diff", help="Create a State Patch proposal from evidence.")
    state_diff.add_argument("--summary", required=True)
    state_diff.add_argument("--content")
    state_diff.add_argument("--source", default="manual")
    state_diff.set_defaults(func=state_diff_command)
    state_apply = state_subparsers.add_parser("apply", help="Confirm and apply a State Patch proposal.")
    state_apply.add_argument("proposal_id")
    state_apply.add_argument("--confirmed-by", default="user")
    state_apply.set_defaults(func=state_apply_command)

    agent_parser = subparsers.add_parser("agent", help="Minimal Soul-Agent adapter.")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_before = agent_subparsers.add_parser("before-task", help="Get Current State before a task.")
    agent_before.add_argument("task")
    agent_before.add_argument("--limit", type=int, default=10)
    agent_before.set_defaults(func=agent_before_task_command)
    agent_after = agent_subparsers.add_parser("after-task", help="Submit task outcome as evidence.")
    agent_after.add_argument("task")
    agent_after.add_argument("outcome")
    agent_after.add_argument("--evidence")
    agent_after.add_argument("--source", default="agent")
    agent_after.set_defaults(func=agent_after_task_command)

    api_parser = subparsers.add_parser("api", help="Run the local Soul HTTP API.")
    api_subparsers = api_parser.add_subparsers(dest="api_command", required=True)
    api_serve = api_subparsers.add_parser("serve", help="Serve the localhost HTTP API for Harness plugins.")
    api_serve.add_argument("--project-dir", default=".")
    api_serve.add_argument("--host", default="127.0.0.1")
    api_serve.add_argument("--port", type=int, default=8765)
    api_serve.set_defaults(func=api_serve_command)

    codex_parser = subparsers.add_parser("codex", help="Codex workflow integration.")
    codex_subparsers = codex_parser.add_subparsers(dest="codex_command", required=True)
    codex_ingest = codex_subparsers.add_parser("ingest", help="Import and reflect a Codex JSONL session.")
    codex_ingest.add_argument("path")
    codex_ingest.add_argument("--max-patches", dest="max_patches", type=int, default=3)
    codex_ingest.set_defaults(func=codex_ingest_command)

    import_parser = subparsers.add_parser("import", help="Import external interaction traces.")
    import_subparsers = import_parser.add_subparsers(dest="import_command", required=True)
    import_codex = import_subparsers.add_parser("codex", help="Import a Codex JSONL session.")
    import_codex.add_argument("path")
    import_codex.set_defaults(func=import_codex_command)

    episode_parser = subparsers.add_parser("episode", help="Inspect imported interaction episodes.")
    episode_subparsers = episode_parser.add_subparsers(dest="episode_command", required=True)
    episode_list = episode_subparsers.add_parser("list", help="List episodes.")
    episode_list.set_defaults(func=list_episodes_command)
    episode_show = episode_subparsers.add_parser("show", help="Show one episode.")
    episode_show.add_argument("episode_id", type=int)
    episode_show.add_argument("--limit", type=int, default=20, help="Maximum messages to print. Use 0 for all.")
    episode_show.set_defaults(func=show_episode_command)

    reflect_parser = subparsers.add_parser("reflect", help="Reflect on imported episodes.")
    reflect_subparsers = reflect_parser.add_subparsers(dest="reflect_command", required=True)
    reflect_episode_parser = reflect_subparsers.add_parser("episode", help="Create State Patch proposals from one episode.")
    reflect_episode_parser.add_argument("episode_id", type=int)
    reflect_episode_parser.add_argument("--max-patches", dest="max_patches", type=int, default=3)
    reflect_episode_parser.set_defaults(func=reflect_episode_command)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
