from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
import webbrowser
from importlib import resources
from urllib.error import URLError
from urllib.request import Request, urlopen
from pathlib import Path
from typing import Any, Mapping, NoReturn, cast

from soul.adapters.reme import ReMeCliAdapter
from soul.hooks.runtime import HookHost, read_payload, run_stop_hook, run_user_prompt_submit_hook, write_json
from soul.services.integrations.episodes import find_episode, read_episodes, resolve_episode_selector
from soul.services.shared.constants import (
    HOST_DEEPSEEK_HARNESS,
    PATCH_STATUS_ACCEPTED,
    PATCH_STATUS_APPLIED,
    PATCH_STATUS_PROPOSED,
    PATCH_STATUS_REJECTED,
    REME_LLM_API_KEY_ENV,
    REME_LLM_BASE_URL_ENV,
    REME_LLM_MODEL_NAME_ENV,
)
from soul.services.integrations.integration_runs import append_integration_run, latest_matching_run, read_integration_runs
from soul.services.integrations.codex_workflow import ingest_codex_session
from soul.services.integrations.importer import import_codex_session
from soul.services.integrations.queue import drain_queue, queue_status
from soul.services.integrations.reflection import reflect_episode
from soul.services.reme.runtime_config import resolve_reme_runtime_config, soul_home
from soul.services.state_core.proposals import apply_patch_proposal, append_patch_status, edit_patch_proposal, propose_patch
from soul.services.state_core.review_card import build_review_card
from soul.services.state_core.state_render import format_state_context
from soul.services.state_core.state_store import (
    append_patch_proposal,
    find_patch_proposal,
    load_patch_proposals,
    load_state,
    save_state,
)
from soul.services.state_core.working_state import (
    edit_working_item,
    expire_working_item,
    load_working_state,
    promote_working_item,
    reject_working_item,
    review_working_items,
)
from soul.services.shared.text import compact_text


def init_command(args: argparse.Namespace) -> None:
    project_name = args.project_name or Path.cwd().name
    load_state(project_name=project_name)
    print(f"Initialized Soul state: {Path.cwd() / '.soul' / 'state'}")


def status_command(_: argparse.Namespace) -> None:
    state = load_state(project_name=Path.cwd().name)
    integration_runs = read_integration_runs(Path.cwd(), limit=5)
    working_items = load_working_state(Path.cwd(), project_name=Path.cwd().name).get("items", [])[-5:]
    patches = read_recent_jsonl(Path.cwd() / ".soul" / "state" / "patch_proposals.jsonl", limit=5)
    print("Soul Status")
    print("")
    print(f"Project: {state.get('project', 'unknown')}")
    print("")
    print(format_state_context(state, limit=10))
    print("")
    print("Recent Integration Runs:")
    for run in integration_runs:
        print(
            f"- [{run.get('host', 'unknown')}] {run.get('operation', 'unknown')} "
            f"({run.get('created_at', 'unknown')})"
        )
    if not integration_runs:
        print("- none")
    print("")
    print("Recent Working State:")
    for item in working_items:
        print(f"- {item.get('id', 'unknown')} [{item.get('status', 'unknown')}] {item.get('scope', '')}")
    if not working_items:
        print("- none")
    print("")
    print("Recent Patch Proposals:")
    for patch in patches:
        print(f"- {patch.get('id', 'unknown')} [{patch.get('status', 'unknown')}] {patch.get('source', 'unknown')}")
    if not patches:
        print("- none")


def context_command(args: argparse.Namespace) -> None:
    print(format_state_context(load_state(), limit=args.limit))


def queue_status_command(args: argparse.Namespace) -> None:
    summary = queue_status(Path(args.project_dir).resolve())
    print("Soul Queue")
    print(f"- queued: {summary.queued}")
    print(f"- processing: {summary.started}")
    print(f"- completed: {summary.completed}")
    print(f"- failed retryable: {summary.failed_retryable}")
    print(f"- blocked: {summary.blocked}")
    print(f"- dead letter: {summary.dead_letter}")
    if summary.last_completed:
        working = (
            f" {summary.last_completed.get('working_state_id')}"
            if summary.last_completed.get("working_state_id")
            else ""
        )
        print(f"- last completed: {summary.last_completed.get('created_at', 'unknown')}{working}")
    else:
        print("- last completed: none")
    if summary.last_error:
        print(f"- last error: {summary.last_error.get('error', 'unknown')}")
    else:
        print("- last error: none")


def queue_drain_command(args: argparse.Namespace) -> None:
    result = drain_queue(Path(args.project_dir).resolve(), limit=args.limit)
    if result.get("locked"):
        print("Soul queue worker is already running.")
        return
    processed = result.get("processed", [])
    print(f"Processed queue jobs: {len(processed) if isinstance(processed, list) else 0}")
    if isinstance(processed, list):
        for item in processed:
            if not isinstance(item, dict):
                continue
            detail = f" {item.get('working_state_id')}" if item.get("working_state_id") else ""
            print(f"- {item.get('job_id', 'unknown')}: {item.get('status', 'unknown')}{detail}")


def hook_user_prompt_submit_command(args: argparse.Namespace) -> None:
    write_json(run_user_prompt_submit_hook(read_payload(), host=cast(HookHost, args.host)).output)


def hook_stop_command(args: argparse.Namespace) -> None:
    write_json(run_stop_hook(read_payload(), host=cast(HookHost, args.host)).output)


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


def state_review_command(args: argparse.Namespace) -> None:
    proposals = load_patch_proposals()
    if args.proposal_id:
        proposals = [proposal for proposal in proposals if proposal.get("id") == args.proposal_id]
        if not proposals:
            abort(f"Patch proposal not found: {args.proposal_id}")
    else:
        proposals = [proposal for proposal in proposals if proposal.get("status", PATCH_STATUS_PROPOSED) == PATCH_STATUS_PROPOSED]
    proposals = proposals[-args.limit :]
    if not proposals:
        print("No patch proposals to review.")
        return
    for index, proposal in enumerate(proposals):
        if index:
            print("")
        print(format_patch_review(proposal, show_refs=args.refs))


def state_apply_command(args: argparse.Namespace) -> None:
    proposal = find_patch_proposal(args.proposal_id)
    if proposal.get("status") in {PATCH_STATUS_REJECTED, PATCH_STATUS_APPLIED}:
        abort(f"Patch proposal {args.proposal_id} is already {proposal.get('status')}.")
    state = load_state()
    if proposal.get("base_version") != state.get("version"):
        abort(
            f"Patch base version {proposal.get('base_version')} does not match current state version {state.get('version')}."
        )
    next_state = apply_patch_proposal(state, proposal, confirmed_by=args.confirmed_by)
    save_state(next_state)
    append_patch_status(proposal, PATCH_STATUS_APPLIED, updated_by=args.confirmed_by)
    print(f"Applied State Patch: {args.proposal_id}")
    print(f"New State version: {next_state['version']}")


def state_reject_command(args: argparse.Namespace) -> None:
    proposal = find_patch_proposal(args.proposal_id)
    if proposal.get("status") in {PATCH_STATUS_REJECTED, PATCH_STATUS_APPLIED}:
        abort(f"Patch proposal {args.proposal_id} is already {proposal.get('status')}.")
    append_patch_status(proposal, PATCH_STATUS_REJECTED, reason=args.reason or "", updated_by=args.rejected_by)
    print(f"Rejected State Patch: {args.proposal_id}")


def state_edit_command(args: argparse.Namespace) -> None:
    proposal = find_patch_proposal(args.proposal_id)
    knowledge_points = None
    if args.json:
        payload = json.loads(args.json)
        if not isinstance(payload, list):
            abort("--json must be a JSON array of knowledge point objects.")
        knowledge_points = payload
    elif args.knowledge_point:
        knowledge_points = [
            {
                "statement": point,
                "kind": args.kind,
                "priority": args.priority,
                "confidence": args.confidence,
                "status": PATCH_STATUS_ACCEPTED,
                "why_remember": args.why_remember or "",
            }
            for point in args.knowledge_point
        ]
    edited = edit_patch_proposal(
        proposal,
        title=args.title,
        why_remember=args.why_remember,
        knowledge_points=knowledge_points,
        updated_by=args.updated_by,
    )
    print(f"Edited State Patch: {edited['id']}")
    print(format_patch_review(edited, show_refs=False))


def state_working_review_command(args: argparse.Namespace) -> None:
    items = review_working_items(Path.cwd(), limit=args.limit)
    if not items:
        print("No Working State items to review.")
        return
    for index, item in enumerate(items, start=1):
        if index > 1:
            print("")
        print(f"Working State {item.get('id', 'unknown')}")
        print(f"Status: {item.get('status', 'working')}")
        print(f"Scope: {item.get('scope', '')}")
        if item.get("review_after"):
            print(f"Review after: {item.get('review_after')}")
        print(f"Expires: {item.get('expires_at', '')}")
        print(f"Statement: {item.get('statement', '')}")
        if item.get("reason"):
            print(f"Reason: {item.get('reason')}")
        print("Actions:")
        print(f"- soul state working-promote {item.get('id', '')}")
        print(f"- soul state working-expire {item.get('id', '')} --reason <reason>")
        print(f"- soul state working-reject {item.get('id', '')} --reason <reason>")


def state_working_promote_command(args: argparse.Namespace) -> None:
    result = promote_working_item(Path.cwd(), args.working_id, confirmed_by=args.confirmed_by)
    proposal = result["patch_proposal"]
    print(f"Promoted Working State: {args.working_id}")
    print(f"Created State Patch: {proposal['id']}")
    print(format_patch_review(proposal, show_refs=False))


def state_working_expire_command(args: argparse.Namespace) -> None:
    expire_working_item(Path.cwd(), args.working_id, reason=args.reason or "")
    print(f"Expired Working State: {args.working_id}")


def state_working_reject_command(args: argparse.Namespace) -> None:
    reject_working_item(Path.cwd(), args.working_id, reason=args.reason or "")
    print(f"Rejected Working State: {args.working_id}")


def state_working_edit_command(args: argparse.Namespace) -> None:
    item = edit_working_item(
        Path.cwd(),
        args.working_id,
        statement=args.statement,
        reason=args.reason,
        scope=args.scope,
        review_after=args.review_after,
        expires_at=args.expires_at,
        updated_by=args.updated_by,
    )
    print(f"Edited Working State: {args.working_id}")
    print(f"Statement: {item.get('statement', '')}")
    if item.get("reason"):
        print(f"Reason: {item.get('reason')}")
    print(f"Scope: {item.get('scope', '')}")
    print(f"Review after: {item.get('review_after', '')}")
    print(f"Expires: {item.get('expires_at', '')}")


def state_review_card_command(args: argparse.Namespace) -> None:
    card = build_review_card(Path.cwd(), limit=args.limit, near_expiry_hours=args.near_expiry_hours)
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return
    print(format_review_card(card, show_refs=args.refs))


def agent_before_task_command(args: argparse.Namespace) -> None:
    from soul.services.state_core.state_store import load_state_markdown

    print(load_state_markdown(limit=args.limit, task=args.task))


def api_serve_command(args: argparse.Namespace) -> None:
    from soul.api import serve

    serve(Path(args.project_dir), host=args.host, port=args.port)


def review_web_command(args: argparse.Namespace) -> None:
    from soul.api import serve

    project_dir = Path(args.review_project_dir)
    base_url = f"http://{args.review_host}:{args.review_port}"
    review_url = base_url + "/review"
    health = check_http_health(base_url)
    if args.review_restart and health == "ok":
        print(f"Restarting Soul Review at {review_url}")
        shutdown_status = shutdown_http_api(base_url)
        if shutdown_status != "ok":
            abort(
                "Existing Soul Review server does not support automatic restart. "
                "Stop the old `soul --review` process manually, then run `soul --review` again."
            )
        if not wait_for_http_status(base_url, expected_down=True, timeout_seconds=3):
            abort(
                "Existing Soul Review server did not stop in time. "
                "Stop it manually, then run `soul --review` again."
            )
        health = check_http_health(base_url)
    if health == "ok":
        print(f"Soul Review is available: {review_url}")
        print("Use --review-restart to restart the local Review server.")
        if not args.review_no_open:
            webbrowser.open(review_url)
        return
    print(f"Starting Soul Review for {project_dir.resolve()}: {review_url}")
    if not args.review_no_open:
        threading.Timer(0.8, lambda: webbrowser.open(review_url)).start()
    serve(project_dir, host=args.review_host, port=args.review_port)


def import_codex_command(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    if not path.exists():
        abort(f"Codex session file not found: {path}")

    episode_id = import_codex_session(path, project_dir=Path.cwd())
    episode = find_episode(Path.cwd(), episode_id)
    if episode is None:
        abort(f"Episode not found after import: {episode_id}")
    print(f"Imported Codex episode: {episode_id}")
    print(f"Summary: {episode.get('summary', '')}")
    print(f"Metadata: {json.dumps(episode.get('metadata', {}), ensure_ascii=False)}")


def list_episodes_command(_: argparse.Namespace) -> None:
    for index, episode in enumerate(read_episodes(Path.cwd()), start=1):
        print(
            f"#{index}\t{episode.get('id')}\t{episode.get('source', 'unknown')}\t"
            f"{episode.get('summary', '')}\t{episode.get('created_at', 'unknown')}"
        )


def show_episode_command(args: argparse.Namespace) -> None:
    episode = resolve_episode_selector(Path.cwd(), args.episode_id)
    if episode is None:
        abort(f"Episode not found: {args.episode_id}")

    messages = episode.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    metadata = episode.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    limit = max(args.limit, 0)

    print(f"id: {episode.get('id')}")
    print(f"source: {episode.get('source', 'unknown')}")
    print(f"summary: {episode.get('summary', '')}")
    print(f"source_path: {episode.get('source_path')}")
    print(f"created_at: {episode.get('created_at', 'unknown')}")
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
    try:
        episode = resolve_episode_selector(Path.cwd(), args.episode_id)
        if episode is None:
            raise ValueError(f"Episode not found: {args.episode_id}")
        episode_id = str(episode.get("id") or args.episode_id)
        patch_proposal_ids = reflect_episode(episode_id, project_dir=Path.cwd(), max_patches=args.max_patches)
    except ValueError as exc:
        abort(str(exc))

    print(f"Reflected episode: {episode_id}")
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

    result = ingest_codex_session(path, project_dir=Path.cwd(), max_patches=args.max_patches)

    action = "Imported" if result.imported else "Updated"
    print(f"{action} Codex episode: {result.episode_id}")
    print(f"Summary: {result.summary}")
    if result.patch_proposal_ids:
        print("Patch proposals:")
        for proposal_id in result.patch_proposal_ids:
            print(f"- {proposal_id}")
    else:
        print("Patch proposals: none")
    append_integration_run(
        Path.cwd().resolve(),
        {
            "host": "codex:cli",
            "operation": "codex_ingest",
            "status": "success",
            "episode_id": result.episode_id,
            "imported": result.imported,
            "source_path": str(path),
            "patch_count": len(result.patch_proposal_ids),
        },
    )


def codex_install_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    if args.scope != "user":
        abort("Codex install currently supports only `--scope user`.")
    install_codex_user_config(args, project_dir)
    if args.init:
        init_project(project_dir, project_name=args.project_name)


def codex_doctor_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    user_config = resolve_codex_config_path(args.codex_config)
    project_config = project_dir / ".codex" / "config.toml"
    runs = read_integration_runs(project_dir, limit=50)
    codex_run = latest_matching_run(runs, host_prefix="codex:mcp")
    imported_codex_episode = latest_episode_summary(project_dir, source="codex")

    print("Codex Soul doctor:")
    print(f"- project: {project_dir}")
    print(f"- user config: {user_config} ({'present' if user_config.exists() else 'missing'})")
    print(f"- user MCP: {config_contains(user_config, '[mcp_servers.soul]')}")
    print(f"- project config: {project_config} ({'present' if project_config.exists() else 'missing'})")
    print(f"- project MCP: {config_contains(project_config, '[mcp_servers.soul]')}")
    print_integration_run("Codex MCP execution", codex_run)
    if imported_codex_episode:
        print(f"- Codex CLI ingest: present ({imported_codex_episode})")
    else:
        print("- Codex CLI ingest: none")
    print_file_summary("ReMe evidence", newest_files(project_dir / ".soul" / "reme", limit=3), project_dir)
    print_file_summary("Soul traces", newest_files(project_dir / ".soul" / "traces", limit=3), project_dir)
    print_file_summary("Working State", newest_files(project_dir / ".soul" / "state", names={"working_state.json"}, limit=1), project_dir)
    print_file_summary("Patch proposals", newest_files(project_dir / ".soul" / "state", names={"patch_proposals.jsonl"}, limit=1), project_dir)
    if codex_run:
        print("- status: Codex MCP has executed Soul recently.")
    elif imported_codex_episode:
        print("- status: Codex CLI ingest has run, but no recent Codex MCP execution heartbeat was found.")
    else:
        print("- status: MCP configuration visibility is not enough; no Codex Soul execution evidence was found.")


def dsh_doctor_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    runs = read_integration_runs(project_dir, limit=50)
    dsh_runs = [run for run in runs if str(run.get("host") or "").startswith(HOST_DEEPSEEK_HARNESS)]
    api_status = check_http_health(args.api_url)

    print("DeepSeek Harness Soul doctor:")
    print(f"- project: {project_dir}")
    print(f"- api: {args.api_url} ({api_status})")
    print_integration_run("DSH before-turn/state", latest_operation_run(dsh_runs, "get_state"))
    print_integration_run("DSH after-turn/evidence", latest_operation_run(dsh_runs, "enqueue"))
    print_file_summary("ReMe evidence", newest_files(project_dir / ".soul" / "reme", limit=3), project_dir)
    print_file_summary("Soul traces", newest_files(project_dir / ".soul" / "traces", limit=3), project_dir)
    print_file_summary("Working State", newest_files(project_dir / ".soul" / "state", names={"working_state.json"}, limit=1), project_dir)
    print_file_summary("Patch proposals", newest_files(project_dir / ".soul" / "state", names={"patch_proposals.jsonl"}, limit=1), project_dir)
    if dsh_runs:
        print("- status: DeepSeek Harness has executed Soul recently.")
    else:
        print("- status: API availability is not enough; no DeepSeek Harness Soul execution heartbeat was found.")


def dsh_install_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = project_dir / output
    output.parent.mkdir(parents=True, exist_ok=True)
    patch = build_dsh_patch(
        project_dir=project_dir,
        package_root=package_root(),
        api_url=args.api_url,
        search_limit=args.search_limit,
    )
    output.write_text(patch, encoding="utf-8")
    print(f"Installed Soul DeepSeek Harness patch: {output}")
    print("Use it with:")
    print(f"  dsh web --patch {shell_quote(str(output))}")
    print("")
    print("Make sure the Soul API is running:")
    print(f"  soul-api --project-dir {shell_quote(str(project_dir))} --port {api_port(args.api_url)}")


def traex_install_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    if args.scope == "user":
        install_traex_user_config(args, project_dir)
        if args.init:
            init_project(project_dir, project_name=args.project_name)
        return

    template_dir = package_root() / ".trae"
    if not template_dir.exists():
        abort(f"TraeX template directory not found: {template_dir}")

    target_dir = project_dir / ".trae"
    copied: list[str] = []
    skipped: list[str] = []

    for source in sorted(path for path in template_dir.rglob("*") if path.is_file()):
        relative = source.relative_to(template_dir)
        target = target_dir / relative
        if target.exists() and not args.force:
            skipped.append(str(target.relative_to(project_dir)))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(target.relative_to(project_dir)))

    if not copied and skipped:
        print("TraeX Soul files already exist. Re-run with --force to overwrite.")
    else:
        print(f"Installed TraeX Soul integration into: {target_dir}")
    if copied:
        print("Copied:")
        for path in copied:
            print(f"- {path}")
    if skipped:
        print("Skipped existing files:")
        for path in skipped:
            print(f"- {path}")

    if args.init:
        init_project(project_dir, project_name=args.project_name)

    if not args.skip_reme_check:
        print("")
        print_reme_preflight(project_dir, create_workspace=True)


def install_traex_user_config(args: argparse.Namespace, project_dir: Path) -> None:
    config_path = resolve_traex_config_path(args.traex_config)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    managed_block = build_traex_user_managed_block(project_dir=project_dir, package_root=package_root())
    next_text, skipped = merge_traex_user_config(current, managed_block=managed_block)
    config_path.write_text(next_text, encoding="utf-8")

    print(f"Installed Soul TraeX user config into: {config_path}")
    if skipped:
        print("Skipped existing unmanaged entries:")
        for item in skipped:
            print(f"- {item}")
        print("Re-run with --force only after moving existing Soul entries into the managed block.")
    print("- MCP: configured in user traecli.toml")
    print("- hooks: configured in user traecli.toml")

    if not args.skip_reme_check:
        print("")
        print_reme_preflight(project_dir, create_workspace=True)


def init_project(project_dir: Path, *, project_name: str | None = None) -> None:
    load_state(project_dir, project_name=project_name or project_dir.name)
    print(f"Initialized Soul state: {project_dir / '.soul' / 'state'}")


def reme_doctor_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    print_reme_preflight(project_dir, create_workspace=args.create_workspace)
    print_reme_workspace_layout_warnings(project_dir)


def traex_doctor_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    user_config = resolve_traex_config_path(args.traex_config)
    project_mcp = project_dir / ".trae" / ".mcp.json"
    project_hooks = project_dir / ".trae" / "hooks.json"
    hook_runs = read_recent_jsonl(project_dir / ".soul" / "state" / "hook_runs.jsonl", limit=20)
    reme_files = newest_files(project_dir / ".soul" / "reme", limit=3)
    trace_files = newest_files(project_dir / ".soul" / "traces", limit=3)
    working_files = newest_files(project_dir / ".soul" / "state", names={"working_state.json"}, limit=1)
    patch_files = newest_files(project_dir / ".soul" / "state", names={"patch_proposals.jsonl"}, limit=1)
    queue = queue_status(project_dir)

    print("TraeX Soul doctor:")
    print(f"- project: {project_dir}")
    print(f"- user config: {user_config} ({'present' if user_config.exists() else 'missing'})")
    print(f"- project MCP: {project_mcp} ({config_contains(project_mcp, 'soul')})")
    print(f"- project hooks: {project_hooks} ({config_contains(project_hooks, 'soul_')})")
    print(f"- user MCP: {config_contains(user_config, '[mcp_servers.soul]')}")
    print(f"- user hooks: {config_contains(user_config, 'SoulKit managed TraeX integration')}")
    print_hook_summary(hook_runs)
    print_file_summary("ReMe evidence", reme_files, project_dir)
    print_file_summary("Soul traces", trace_files, project_dir)
    print_file_summary("Working State", working_files, project_dir)
    print_file_summary("Patch proposals", patch_files, project_dir)
    print_queue_summary(queue)

    if not hook_runs:
        print("- status: configured status is not enough; no recent Soul hook execution heartbeat was found.")
        print("- action: start a new TraeX turn in this project, then re-run `soul traex doctor --project-dir .`.")
    elif any(run.get("status") == "error" for run in hook_runs[:5]):
        print("- status: hooks executed, but recent errors were recorded.")
    elif queue.queued or queue.failed_retryable:
        print("- status: hooks executed; queued evidence is waiting for background processing.")
        print("- action: run `soul queue drain --project-dir .` if it does not clear automatically.")
    else:
        print("- status: hooks executed recently.")


def reme_start_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    code = ReMeCliAdapter(project_dir).start_service(host=args.host, port=args.port, foreground=args.foreground)
    raise SystemExit(code)


def reme_init_config_command(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).expanduser().resolve()
    config_path = reme_config_template_path(project_dir, scope=args.scope)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and not args.force:
        print(f"ReMe auto_memory config already exists: {config_path}")
        print("Edit this file and fill in LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL_NAME.")
        print("Use --force to recreate the template.")
        return
    config_path.write_text(reme_env_template(), encoding="utf-8")
    print(f"Created ReMe auto_memory config template: {config_path}")
    print("Edit this file and fill in LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL_NAME.")
    if args.scope == "global":
        print("Project .env files can override this global config when needed.")


def reme_config_template_path(project_dir: Path, *, scope: str) -> Path:
    if scope == "project":
        return project_dir / ".env"
    return soul_home() / ".env"


def reme_env_template() -> str:
    return resources.files("soul.templates").joinpath("reme.env").read_text(encoding="utf-8")


def print_reme_preflight(project_dir: Path, *, create_workspace: bool) -> bool:
    result = ReMeCliAdapter(project_dir).check_preflight(create_workspace=create_workspace, check_service=True)
    print("ReMe preflight:")
    print(f"- workspace: {result.workspace_dir}")
    print(f"- cli: {result.cli_path or 'not found'}")
    if result.service_ok is not None:
        print(f"- service: {'ok' if result.service_ok else 'not available'}")
    if result.ok:
        print("- status: ok")
    else:
        print(f"- status: {result.message}")
        if result.cli_path is None:
            print("- action: install ReMe or make sure the `reme` executable is on PATH.")
        else:
            print("- action: start ReMe with `reme start` before expecting Soul evidence writes.")
    print_reme_auto_memory_env(project_dir)
    return result.ok


def print_reme_auto_memory_env(project_dir: Path) -> bool:
    required = [REME_LLM_API_KEY_ENV, REME_LLM_BASE_URL_ENV, REME_LLM_MODEL_NAME_ENV]
    runtime_config = resolve_reme_runtime_config(project_dir)
    missing = runtime_config.missing
    print("ReMe auto_memory config:")
    for env_file in runtime_config.env_files:
        print(f"- env file: {env_file}")
    for name in required:
        source = runtime_config.sources.get(name, "not found")
        status = "set" if runtime_config.values.get(name) else "missing"
        print(f"- {name}: {status} ({source})")
    if missing:
        joined = ", ".join(missing)
        print(f"- status: missing environment for ReMe auto_memory: {joined}")
        print("- action: run `soul reme init-config --scope global`, then fill in the generated file.")
        return False
    print("- status: ok")
    return True


def print_reme_workspace_layout_warnings(project_dir: Path) -> None:
    default_workspace = project_dir / ".soul" / "reme"
    root_daily = project_dir / "daily"
    root_session = project_dir / "session"
    workspace_has_files = any(default_workspace.rglob("*")) if default_workspace.exists() else False
    misplaced = [path for path in (root_daily, root_session) if path.exists()]
    if misplaced and not workspace_has_files:
        print("- warning: ReMe files appear to be written at the project root, not under .soul/reme.")
        for path in misplaced:
            print(f"  - found: {path.relative_to(project_dir)}")
        print("- action: remove custom reme.workspace_dir values and use Soul's default .soul/reme workspace.")


MANAGED_TRAEX_BEGIN = "# >>> SoulKit managed TraeX integration >>>"
MANAGED_TRAEX_END = "# <<< SoulKit managed TraeX integration <<<"
MANAGED_CODEX_BEGIN = "# >>> SoulKit managed Codex integration >>>"
MANAGED_CODEX_END = "# <<< SoulKit managed Codex integration <<<"


def package_root() -> Path:
    # commands/parser.py lives under soul/commands; package assets live at the repository/package root.
    return Path(__file__).resolve().parents[2]


def install_codex_user_config(args: argparse.Namespace, project_dir: Path) -> None:
    config_path = resolve_codex_config_path(args.codex_config)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    managed_block = build_codex_user_managed_block(project_dir=project_dir, package_root=package_root())
    next_text, skipped = merge_managed_config(
        current,
        managed_block=managed_block,
        begin_marker=MANAGED_CODEX_BEGIN,
        end_marker=MANAGED_CODEX_END,
        protected_headings=["[mcp_servers.soul]"],
    )
    config_path.write_text(next_text, encoding="utf-8")

    print(f"Installed Soul Codex user config into: {config_path}")
    if skipped:
        print("Skipped existing unmanaged entries:")
        for item in skipped:
            print(f"- {item}")
    print("- MCP: configured in Codex config.toml")

    if not args.skip_reme_check:
        print("")
        print_reme_preflight(project_dir, create_workspace=True)


def resolve_traex_config_path(raw_config_path: str | None = None) -> Path:
    if raw_config_path:
        return Path(raw_config_path).expanduser().resolve()
    trae_home = os.environ.get("TRAE_HOME")
    if trae_home:
        return (Path(trae_home).expanduser() / "traecli.toml").resolve()
    return (Path.home() / ".trae" / "traecli.toml").resolve()


def resolve_codex_config_path(raw_config_path: str | None = None) -> Path:
    if raw_config_path:
        return Path(raw_config_path).expanduser().resolve()
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return (Path(codex_home).expanduser() / "config.toml").resolve()
    return (Path.home() / ".codex" / "config.toml").resolve()


def build_codex_user_managed_block(*, project_dir: Path, package_root: Path) -> str:
    soul_mcp = package_root / "bin" / "soul-mcp.js"
    return "\n".join(
        [
            MANAGED_CODEX_BEGIN,
            "# Managed by `soul codex install --scope user`; edit with care.",
            "",
            "[mcp_servers.soul]",
            'command = "node"',
            f"args = {json.dumps([command_path(soul_mcp), '--project-dir', str(project_dir)])}",
            "enabled = true",
            "startup_timeout_sec = 30",
            "tool_timeout_sec = 60",
            "",
            MANAGED_CODEX_END,
            "",
        ]
    )


def build_traex_user_managed_block(*, project_dir: Path, package_root: Path) -> str:
    soul_mcp = package_root / "bin" / "soul-mcp.js"
    soul_cli = package_root / "bin" / "soul.js"
    return "\n".join(
        [
            MANAGED_TRAEX_BEGIN,
            "# Managed by `soul traex install --scope user`; edit with care.",
            "",
            "[mcp_servers.soul]",
            'command = "node"',
            f"args = {json.dumps([str(soul_mcp), '--project-dir', str(project_dir)])}",
            "enabled = true",
            "startup_timeout_sec = 30.0",
            "tool_timeout_sec = 60.0",
            "",
            "[[hooks.UserPromptSubmit]]",
            "",
            "[[hooks.UserPromptSubmit.hooks]]",
            'type = "command"',
            f"command = {json.dumps(shell_command(['node', command_path(soul_cli), 'hook', 'user-prompt-submit', '--host', 'traex']))}",
            "timeout = 10",
            'statusMessage = "loading Soul Current State"',
            "",
            "[[hooks.Stop]]",
            "",
            "[[hooks.Stop.hooks]]",
            'type = "command"',
            f"command = {json.dumps(shell_command(['node', command_path(soul_cli), 'hook', 'stop', '--host', 'traex']))}",
            "timeout = 30",
            'statusMessage = "recording Soul evidence"',
            "",
            MANAGED_TRAEX_END,
            "",
        ]
    )


def build_dsh_patch(*, project_dir: Path, package_root: Path, api_url: str, search_limit: int) -> str:
    plugin_path = package_root / "soul" / "adapters" / "dsh_plugin.mjs"
    return "\n".join(
        [
            "# SoulKit managed DeepSeek Harness patch.",
            "# Pass this file to DSH with: dsh web --patch <this-file>",
            "- insert:",
            "    - id: soul",
            f"      name: {quote_yaml_string(command_path(plugin_path))}",
            "      config:",
            f"        baseUrl: {quote_yaml_string(api_url)}",
            f"        projectDir: {quote_yaml_string(str(project_dir))}",
            f"        searchLimit: {int(search_limit)}",
            "",
        ]
    )


def quote_yaml_string(value: str) -> str:
    return json.dumps(value)


def api_port(api_url: str) -> str:
    parsed = api_url.rsplit(":", 1)
    if len(parsed) == 2 and parsed[1].isdigit():
        return parsed[1]
    return "8765"


def command_path(value: str | Path) -> str:
    return str(value).replace("\\", "/")


def shell_command(parts: list[str]) -> str:
    return " ".join(shell_quote(part) for part in parts)


def shell_quote(value: str) -> str:
    if not value:
        return '""'
    if all(char.isalnum() or char in "/._:@%+=,-" for char in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def merge_traex_user_config(current: str, *, managed_block: str) -> tuple[str, list[str]]:
    return merge_managed_config(
        current,
        managed_block=managed_block,
        begin_marker=MANAGED_TRAEX_BEGIN,
        end_marker=MANAGED_TRAEX_END,
        protected_headings=["[mcp_servers.soul]"],
    )


def merge_managed_config(
    current: str,
    *,
    managed_block: str,
    begin_marker: str,
    end_marker: str,
    protected_headings: list[str],
) -> tuple[str, list[str]]:
    without_managed = remove_managed_block(current, begin_marker=begin_marker, end_marker=end_marker)
    skipped: list[str] = []
    body = without_managed
    for heading in protected_headings:
        if heading in body:
            skipped.append(heading)
            managed_section = extract_toml_section(managed_block, heading)
            managed_block = managed_block.replace(managed_section, "")
    separator = "\n\n" if body.strip() else ""
    return body.rstrip() + separator + managed_block.lstrip(), skipped


def remove_managed_block(text: str, *, begin_marker: str, end_marker: str) -> str:
    start = text.find(begin_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end < start:
        return text
    end += len(end_marker)
    while end < len(text) and text[end] in "\r\n":
        end += 1
    return text[:start].rstrip() + "\n" + text[end:].lstrip()


def extract_toml_section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start == -1:
        return ""
    next_heading = text.find("\n[", start + len(heading))
    if next_heading == -1:
        return text[start:]
    return text[start:next_heading]


def config_contains(path: Path, needle: str) -> str:
    if not path.exists():
        return "missing"
    try:
        return "configured" if needle in path.read_text(encoding="utf-8") else "not configured"
    except OSError:
        return "unreadable"


def read_recent_jsonl(path: Path, *, limit: int) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
        if len(rows) >= limit:
            break
    return rows


def format_patch_review(proposal: Mapping[str, Any], *, show_refs: bool = False) -> str:
    lines = [
        f"Patch {proposal.get('id', 'unknown')}",
        f"Title: {proposal.get('title') or compact_text(proposal.get('evidence', {}).get('summary', ''), 80)}",
        f"Status: {proposal.get('status', PATCH_STATUS_PROPOSED)}",
        f"Recommendation: {proposal.get('review_recommendation', 'unknown')}",
        "",
        "Knowledge Points:",
    ]
    knowledge_points = proposal.get("knowledge_points")
    if isinstance(knowledge_points, list) and knowledge_points:
        for index, point in enumerate(knowledge_points, start=1):
            if not isinstance(point, dict):
                continue
            lines.append(f"{index}. {point.get('statement', '')}")
            meta = ", ".join(
                part
                for part in [
                    str(point.get("kind", "")),
                    f"priority={point.get('priority')}" if point.get("priority") else "",
                    f"confidence={point.get('confidence')}" if point.get("confidence") is not None else "",
                ]
                if part
            )
            if meta:
                lines.append(f"   {meta}")
            if point.get("why_remember"):
                lines.append(f"   Why remember: {point.get('why_remember')}")
    else:
        lines.append("- none detected")
    if proposal.get("why_remember"):
        lines.extend(["", f"Why remember: {proposal.get('why_remember')}"])
    lines.extend(
        [
            "",
            "Actions:",
            f"- soul state apply {proposal.get('id', '')}",
            f"- soul state reject {proposal.get('id', '')} --reason <reason>",
            f"- soul state edit {proposal.get('id', '')} --knowledge-point <text>",
        ]
    )
    if show_refs:
        lines.extend(["", "Refs:"])
        lines.append(json.dumps(proposal.get("refs") or {}, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def format_review_card(card: Mapping[str, Any], *, show_refs: bool = False) -> str:
    counts = card.get("counts") if isinstance(card.get("counts"), Mapping) else {}
    lines = [
        f"Soul Review Card · {card.get('project', 'unknown')}",
        f"{counts.get('total', 0)} candidate(s): {counts.get('ready_to_confirm', 0)} ready, {counts.get('needs_review', 0)} need review",
        "",
    ]
    if not card.get("has_reviewable_content"):
        lines.append("No high-quality state decisions to review.")
        return "\n".join(lines)
    lines.extend(format_review_card_section("Ready to Confirm", card.get("ready_to_confirm"), show_refs=show_refs))
    if lines[-1] != "":
        lines.append("")
    lines.extend(format_review_card_section("Needs Review", card.get("needs_review"), show_refs=show_refs))
    if lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def format_review_card_section(title: str, raw_candidates: Any, *, show_refs: bool = False) -> list[str]:
    candidates = raw_candidates if isinstance(raw_candidates, list) else []
    lines = [title + ":"]
    if not candidates:
        lines.append("- none")
        lines.append("")
        return lines
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        lines.append(f"- [{candidate.get('recommended_action', 'review')}] {candidate.get('statement', '')}")
        lines.append(f"  actions: {', '.join(str(action) for action in candidate.get('actions', []))}")
        if show_refs:
            evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), Mapping) else {}
            refs = evidence.get("refs") if isinstance(evidence.get("refs"), list) else []
            lines.append(f"  reason: {candidate.get('review_reason', '')}")
            lines.append(f"  source: {candidate.get('source_type', 'unknown')} {candidate.get('source_id', '')}")
            lines.append(f"  evidence summary: {evidence.get('summary', '')}")
            if refs:
                lines.append("  evidence refs:")
                for ref in refs:
                    lines.append(f"    - {json.dumps(ref, ensure_ascii=False)}")
            else:
                lines.append("  evidence refs: none")
    lines.append("")
    return lines


def newest_files(root: Path, *, limit: int, names: set[str] | None = None) -> list[Path]:
    if not root.exists():
        return []
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and (names is None or path.name in names)
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def print_hook_summary(hook_runs: list[dict[str, object]]) -> None:
    if not hook_runs:
        print("- hook heartbeat: none")
        return
    print("- hook heartbeat: present")
    for run in hook_runs[:3]:
        event = run.get("hook_event_name", "unknown")
        status = run.get("status", "unknown")
        created = run.get("created_at", "unknown time")
        detail = ""
        if run.get("working_state_id"):
            detail = f", working_state={run['working_state_id']}"
        elif run.get("patch_id"):
            detail = f", patch={run['patch_id']}"
        elif run.get("injected") is not None:
            detail = f", injected={run['injected']}"
        print(f"  - {created}: {event} {status}{detail}")


def print_queue_summary(summary: Any) -> None:
    print(
        "- queue: "
        f"queued={summary.queued}, processing={summary.started}, completed={summary.completed}, "
        f"failed_retryable={summary.failed_retryable}, blocked={summary.blocked}, dead_letter={summary.dead_letter}"
    )
    if summary.last_completed:
        working = (
            f", working_state={summary.last_completed.get('working_state_id')}"
            if summary.last_completed.get("working_state_id")
            else ""
        )
        print(f"  - last completed: {summary.last_completed.get('created_at', 'unknown')}{working}")
    if summary.last_error:
        print(f"  - last error: {summary.last_error.get('error', 'unknown')}")


def print_file_summary(label: str, files: list[Path], project_dir: Path) -> None:
    if not files:
        print(f"- {label}: none")
        return
    print(f"- {label}: present")
    for path in files:
        try:
            relative = path.relative_to(project_dir)
        except ValueError:
            relative = path
        print(f"  - {str(relative).replace(chr(92), '/')}")


def latest_episode_summary(project_dir: Path, *, source: str) -> str | None:
    for episode in reversed(read_episodes(project_dir)):
        if episode.get("source") == source:
            return f"{episode.get('summary', '')} @ {episode.get('created_at', 'unknown')}"
    return None


def print_integration_run(label: str, run: dict[str, object] | None) -> None:
    if run is None:
        print(f"- {label}: none")
        return
    details = [str(run.get("created_at", "unknown time")), str(run.get("operation", "unknown"))]
    if run.get("status"):
        details.append(str(run["status"]))
    if run.get("working_state_id"):
        details.append(f"working_state={run['working_state_id']}")
    elif run.get("patch_id"):
        details.append(f"patch={run['patch_id']}")
    if run.get("injected") is not None:
        details.append(f"injected={run['injected']}")
    if run.get("memory_mode"):
        details.append(f"memory={run['memory_mode']}")
    print(f"- {label}: " + ", ".join(details))


def latest_operation_run(runs: list[dict[str, object]], operation_prefix: str) -> dict[str, object] | None:
    for run in runs:
        operation = str(run.get("operation") or "")
        if operation == operation_prefix or operation.startswith(operation_prefix):
            return run
    return None


def check_http_health(api_url: str) -> str:
    url = api_url.rstrip("/") + "/health"
    try:
        with urlopen(url, timeout=2) as response:
            return "ok" if response.status == 200 else f"http {response.status}"
    except (OSError, URLError) as exc:
        return f"not reachable: {str(exc).splitlines()[0]}"


def shutdown_http_api(api_url: str) -> str:
    url = api_url.rstrip("/") + "/shutdown"
    request = Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=2) as response:
            return "ok" if response.status == 200 else f"http {response.status}"
    except (OSError, URLError) as exc:
        return f"not reachable: {str(exc).splitlines()[0]}"


def wait_for_http_status(api_url: str, *, expected_down: bool, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        is_up = check_http_health(api_url) == "ok"
        if expected_down != is_up:
            return True
        time.sleep(0.1)
    return False


def abort(message: str) -> NoReturn:
    raise SystemExit(message)


def top_level_command(args: argparse.Namespace) -> None:
    if args.review:
        review_web_command(args)
        return
    abort("a command is required unless --review is used.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soul", description="Soul Core command line interface.")
    parser.add_argument("--review", action="store_true", help="Start or open the local Soul Review web UI.")
    parser.add_argument("--review-project-dir", default=".", help="Project directory for --review. Defaults to cwd.")
    parser.add_argument("--review-host", default="127.0.0.1", help="Host for --review local server.")
    parser.add_argument("--review-port", type=int, default=8765, help="Port for --review local server.")
    parser.add_argument("--review-no-open", action="store_true", help="Start/check --review without opening a browser.")
    parser.add_argument("--review-restart", action="store_true", help="Restart an existing local Soul Review server.")
    parser.set_defaults(func=top_level_command)
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize .soul/state state files.")
    init_parser.add_argument("--project-name", help="Defaults to the current directory name.")
    init_parser.set_defaults(func=init_command)

    status_parser = subparsers.add_parser("status", help="Print current project cognition.")
    status_parser.set_defaults(func=status_command)

    context_parser = subparsers.add_parser("context", help="Print compact context for Codex.")
    context_parser.add_argument("--limit", type=int, default=10)
    context_parser.set_defaults(func=context_command)

    queue_parser = subparsers.add_parser("queue", help="Inspect and process asynchronous Soul evidence jobs.")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command", required=True)
    queue_status_parser = queue_subparsers.add_parser("status", help="Show queued evidence processing status.")
    queue_status_parser.add_argument("--project-dir", default=".")
    queue_status_parser.set_defaults(func=queue_status_command)
    queue_drain = queue_subparsers.add_parser("drain", help="Process queued evidence jobs using FIFO over runnable jobs.")
    queue_drain.add_argument("--project-dir", default=".")
    queue_drain.add_argument("--limit", type=int, default=3)
    queue_drain.set_defaults(func=queue_drain_command)

    hook_parser = subparsers.add_parser("hook", help="Run reusable Soul hook adapters from stdin JSON.")
    hook_subparsers = hook_parser.add_subparsers(dest="hook_command", required=True)
    hook_hosts = ["traex", "codex", "dsh", "generic"]
    hook_prompt = hook_subparsers.add_parser("user-prompt-submit", help="Inject Soul Current State for a before-turn hook.")
    hook_prompt.add_argument("--host", choices=hook_hosts, default="generic")
    hook_prompt.set_defaults(func=hook_user_prompt_submit_command)
    hook_stop = hook_subparsers.add_parser("stop", help="Enqueue completed-turn evidence for background processing.")
    hook_stop.add_argument("--host", choices=hook_hosts, default="generic")
    hook_stop.set_defaults(func=hook_stop_command)

    state_parser = subparsers.add_parser("state", help="Inspect and update Current State.")
    state_subparsers = state_parser.add_subparsers(dest="state_command", required=True)
    state_show = state_subparsers.add_parser("show", help="Print Current State.")
    state_show.add_argument("--limit", type=int, default=10)
    state_show.set_defaults(func=state_show_command)
    state_review = state_subparsers.add_parser("review", help="Review proposed knowledge before it enters Current State.")
    state_review.add_argument("proposal_id", nargs="?")
    state_review.add_argument("--limit", type=int, default=5)
    state_review.add_argument("--refs", action="store_true", help="Show folded evidence refs and ReMe metadata.")
    state_review.set_defaults(func=state_review_command)
    state_review_card = state_subparsers.add_parser(
        "review-card",
        help="Show the low-noise state decisions intended for the tray review card.",
    )
    state_review_card.add_argument("--limit", type=int, default=5)
    state_review_card.add_argument("--near-expiry-hours", type=int, default=4)
    state_review_card.add_argument("--refs", action="store_true", help="Show evidence refs for each candidate.")
    state_review_card.add_argument("--json", action="store_true", help="Print machine-readable review-card JSON.")
    state_review_card.set_defaults(func=state_review_card_command)
    state_diff = state_subparsers.add_parser("diff", help="Create a State Patch proposal from evidence.")
    state_diff.add_argument("--summary", required=True)
    state_diff.add_argument("--content")
    state_diff.add_argument("--source", default="manual")
    state_diff.set_defaults(func=state_diff_command)
    state_apply = state_subparsers.add_parser("apply", help="Confirm and apply a State Patch proposal.")
    state_apply.add_argument("proposal_id")
    state_apply.add_argument("--confirmed-by", default="user")
    state_apply.set_defaults(func=state_apply_command)
    state_reject = state_subparsers.add_parser("reject", help="Reject a State Patch proposal without changing Current State.")
    state_reject.add_argument("proposal_id")
    state_reject.add_argument("--reason")
    state_reject.add_argument("--rejected-by", default="user")
    state_reject.set_defaults(func=state_reject_command)
    state_edit = state_subparsers.add_parser("edit", help="Edit a State Patch proposal's user-facing knowledge points.")
    state_edit.add_argument("proposal_id")
    state_edit.add_argument("--title")
    state_edit.add_argument("--why-remember")
    state_edit.add_argument("--knowledge-point", action="append")
    state_edit.add_argument("--kind", default="accepted_belief")
    state_edit.add_argument("--priority", default="medium")
    state_edit.add_argument("--confidence", type=float, default=0.75)
    state_edit.add_argument("--json", help="JSON array of knowledge point objects.")
    state_edit.add_argument("--updated-by", default="user")
    state_edit.set_defaults(func=state_edit_command)
    state_working_review = state_subparsers.add_parser(
        "working-review",
        help="Review unconfirmed Working State items.",
    )
    state_working_review.add_argument("--limit", type=int, default=10)
    state_working_review.set_defaults(func=state_working_review_command)
    state_working_promote = state_subparsers.add_parser(
        "working-promote",
        help="Create a State Patch proposal from a Working State item.",
    )
    state_working_promote.add_argument("working_id")
    state_working_promote.add_argument("--confirmed-by", default="user")
    state_working_promote.set_defaults(func=state_working_promote_command)
    state_working_expire = state_subparsers.add_parser(
        "working-expire",
        help="Expire a Working State item without promoting it.",
    )
    state_working_expire.add_argument("working_id")
    state_working_expire.add_argument("--reason")
    state_working_expire.set_defaults(func=state_working_expire_command)
    state_working_reject = state_subparsers.add_parser(
        "working-reject",
        help="Reject a Working State item.",
    )
    state_working_reject.add_argument("working_id")
    state_working_reject.add_argument("--reason")
    state_working_reject.set_defaults(func=state_working_reject_command)
    state_working_edit = state_subparsers.add_parser(
        "working-edit",
        help="Edit a Working State item before accepting or rejecting it.",
    )
    state_working_edit.add_argument("working_id")
    state_working_edit.add_argument("--statement")
    state_working_edit.add_argument("--reason")
    state_working_edit.add_argument("--scope")
    state_working_edit.add_argument("--review-after")
    state_working_edit.add_argument("--expires-at")
    state_working_edit.add_argument("--updated-by", default="user")
    state_working_edit.set_defaults(func=state_working_edit_command)

    agent_parser = subparsers.add_parser("agent", help="Read Current State for prompt injection.")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_before = agent_subparsers.add_parser("before-task", help="Get Current State before a task.")
    agent_before.add_argument("task")
    agent_before.add_argument("--limit", type=int, default=10)
    agent_before.set_defaults(func=agent_before_task_command)

    api_parser = subparsers.add_parser("api", help="Run the local Soul HTTP API.")
    api_subparsers = api_parser.add_subparsers(dest="api_command", required=True)
    api_serve = api_subparsers.add_parser("serve", help="Serve the localhost HTTP API for Harness plugins.")
    api_serve.add_argument("--project-dir", default=".")
    api_serve.add_argument("--host", default="127.0.0.1")
    api_serve.add_argument("--port", type=int, default=8765)
    api_serve.set_defaults(func=api_serve_command)

    codex_parser = subparsers.add_parser("codex", help="Codex workflow integration.")
    codex_subparsers = codex_parser.add_subparsers(dest="codex_command", required=True)
    codex_install = codex_subparsers.add_parser("install", help="Install Soul into Codex user config.")
    codex_install.add_argument("--project-dir", default=".")
    codex_install.add_argument(
        "--scope",
        choices=["user"],
        default="user",
        help="Install user-level Codex config. Codex user config is $CODEX_HOME/config.toml or ~/.codex/config.toml.",
    )
    codex_install.add_argument(
        "--codex-config",
        help="Override Codex config path. Defaults to $CODEX_HOME/config.toml or the current user's Codex home.",
    )
    codex_install.add_argument("--init", action="store_true", help="Initialize .soul/state files in the target project.")
    codex_install.add_argument("--project-name", help="Project name to use with --init. Defaults to project directory name.")
    codex_install.add_argument("--skip-reme-check", action="store_true", help="Skip the non-blocking ReMe PATH preflight.")
    codex_install.set_defaults(func=codex_install_command)
    codex_ingest = codex_subparsers.add_parser("ingest", help="Import and reflect a Codex JSONL session.")
    codex_ingest.add_argument("path")
    codex_ingest.add_argument("--max-patches", dest="max_patches", type=int, default=3)
    codex_ingest.set_defaults(func=codex_ingest_command)
    codex_doctor = codex_subparsers.add_parser("doctor", help="Check whether Codex has actually used Soul.")
    codex_doctor.add_argument("--project-dir", default=".")
    codex_doctor.add_argument(
        "--codex-config",
        help="Override Codex config path. Defaults to $CODEX_HOME/config.toml or the current user's Codex home.",
    )
    codex_doctor.set_defaults(func=codex_doctor_command)

    dsh_parser = subparsers.add_parser("dsh", help="DeepSeek Harness integration helpers.")
    dsh_subparsers = dsh_parser.add_subparsers(dest="dsh_command", required=True)
    dsh_install = dsh_subparsers.add_parser("install", help="Write a DSH patch that loads Soul evidence hooks.")
    dsh_install.add_argument("--project-dir", default=".")
    dsh_install.add_argument("--output", default=".soul/dsh/soul.patch.yml")
    dsh_install.add_argument("--api-url", default="http://127.0.0.1:8765")
    dsh_install.add_argument("--search-limit", type=int, default=5)
    dsh_install.set_defaults(func=dsh_install_command)
    dsh_doctor = dsh_subparsers.add_parser("doctor", help="Check whether DeepSeek Harness has actually used Soul.")
    dsh_doctor.add_argument("--project-dir", default=".")
    dsh_doctor.add_argument("--api-url", default="http://127.0.0.1:8765")
    dsh_doctor.set_defaults(func=dsh_doctor_command)

    traex_parser = subparsers.add_parser("traex", help="TraeX project integration helpers.")
    traex_subparsers = traex_parser.add_subparsers(dest="traex_command", required=True)
    traex_install = traex_subparsers.add_parser("install", help="Install Soul .trae templates into a project.")
    traex_install.add_argument("--project-dir", default=".")
    traex_install.add_argument(
        "--scope",
        choices=["project", "user"],
        default="project",
        help="Install project .trae files or user-level TraeX config.",
    )
    traex_install.add_argument(
        "--traex-config",
        help="Override user TraeX config path. Defaults to $TRAE_HOME/traecli.toml or the current user's TraeX home.",
    )
    traex_install.add_argument("--force", action="store_true", help="Overwrite existing .trae Soul integration files.")
    traex_install.add_argument("--init", action="store_true", help="Initialize .soul/state files in the target project.")
    traex_install.add_argument("--project-name", help="Project name to use with --init. Defaults to project directory name.")
    traex_install.add_argument("--skip-reme-check", action="store_true", help="Skip the non-blocking ReMe PATH preflight.")
    traex_install.set_defaults(func=traex_install_command)
    traex_doctor = traex_subparsers.add_parser("doctor", help="Check whether TraeX can see and has executed Soul hooks.")
    traex_doctor.add_argument("--project-dir", default=".")
    traex_doctor.add_argument(
        "--traex-config",
        help="Override user TraeX config path. Defaults to $TRAE_HOME/traecli.toml or the current user's TraeX home.",
    )
    traex_doctor.set_defaults(func=traex_doctor_command)

    reme_parser = subparsers.add_parser("reme", help="ReMe integration helpers.")
    reme_subparsers = reme_parser.add_subparsers(dest="reme_command", required=True)
    reme_doctor = reme_subparsers.add_parser("doctor", help="Check whether ReMe is available for Soul evidence writes.")
    reme_doctor.add_argument("--project-dir", default=".")
    reme_doctor.add_argument("--create-workspace", action="store_true", help="Create .soul/reme when ReMe is available.")
    reme_doctor.set_defaults(func=reme_doctor_command)
    reme_init_config = reme_subparsers.add_parser(
        "init-config",
        help="Create a local template for ReMe auto_memory LLM configuration.",
    )
    reme_init_config.add_argument("--project-dir", default=".")
    reme_init_config.add_argument(
        "--scope",
        choices=["global", "project"],
        default="global",
        help="Write $SOUL_HOME/.env by default, or project .env with --scope project.",
    )
    reme_init_config.add_argument("--force", action="store_true", help="Overwrite an existing config template.")
    reme_init_config.set_defaults(func=reme_init_config_command)
    reme_start = reme_subparsers.add_parser("start", help="Start the ReMe HTTP/Web service for this project.")
    reme_start.add_argument("--project-dir", default=".")
    reme_start.add_argument("--host", default="127.0.0.1")
    reme_start.add_argument("--port", type=int, default=2333)
    reme_start.add_argument(
        "--foreground",
        action="store_true",
        help="Run ReMe in the current console instead of hiding the service window on Windows.",
    )
    reme_start.set_defaults(func=reme_start_command)

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
    episode_show.add_argument("episode_id", help="Episode UUID or list index.")
    episode_show.add_argument("--limit", type=int, default=20, help="Maximum messages to print. Use 0 for all.")
    episode_show.set_defaults(func=show_episode_command)

    reflect_parser = subparsers.add_parser("reflect", help="Reflect on imported episodes.")
    reflect_subparsers = reflect_parser.add_subparsers(dest="reflect_command", required=True)
    reflect_episode_parser = reflect_subparsers.add_parser("episode", help="Create State Patch proposals from one episode.")
    reflect_episode_parser.add_argument("episode_id", help="Episode UUID or list index.")
    reflect_episode_parser.add_argument("--max-patches", dest="max_patches", type=int, default=3)
    reflect_episode_parser.set_defaults(func=reflect_episode_command)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
