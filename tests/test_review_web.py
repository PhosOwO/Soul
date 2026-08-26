from __future__ import annotations

from http.server import ThreadingHTTPServer
from threading import Thread

import soul.api
from soul.commands import parser as parser_module
from soul.api import SoulApi, make_handler
from soul.commands.parser import build_parser
from soul.services.state_core.state_store import load_state


def test_top_level_review_opens_existing_review_server(tmp_path, capsys):
    load_state(tmp_path, project_name="Review Web Test")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(SoulApi(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = str(server.server_address[1])
        parser = build_parser()
        args = parser.parse_args(
            [
                "--review",
                "--review-project-dir",
                str(tmp_path),
                "--review-port",
                port,
                "--review-no-open",
            ]
        )
        args.func(args)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert f"http://127.0.0.1:{port}/review" in capsys.readouterr().out


def test_top_level_review_restart_stops_existing_server_and_starts_new_one(tmp_path, monkeypatch, capsys):
    load_state(tmp_path, project_name="Review Web Test")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(SoulApi(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    calls = []

    def fake_serve(project_dir, *, host, port, register=True):
        calls.append({"project_dir": project_dir, "host": host, "port": port, "register": register})

    monkeypatch.setattr(soul.api, "serve", fake_serve)
    port = str(server.server_address[1])
    parser = build_parser()
    args = parser.parse_args(
        [
            "--review",
            "--review-project-dir",
            str(tmp_path),
            "--review-port",
            port,
            "--review-no-open",
            "--review-restart",
        ]
    )

    try:
        args.func(args)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    output = capsys.readouterr().out
    assert "Restarting Soul Review" in output
    assert calls == [{"project_dir": tmp_path, "host": "127.0.0.1", "port": int(port), "register": False}]


def test_top_level_review_does_not_register_current_directory(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    calls = []

    def fake_health(base_url):
        return "down"

    def fake_serve(project_dir, *, host, port, register=True):
        calls.append({"project_dir": project_dir, "host": host, "port": port, "register": register})

    monkeypatch.setattr(parser_module, "check_http_health", fake_health)
    monkeypatch.setattr(soul.api, "serve", fake_serve)

    parser = build_parser()
    args = parser.parse_args(
        [
            "--review",
            "--review-project-dir",
            str(plain_dir),
            "--review-no-open",
        ]
    )
    args.func(args)

    assert calls == [{"project_dir": plain_dir, "host": "127.0.0.1", "port": 8765, "register": False}]
    assert not (plain_dir / ".soul").exists()
    assert not (soul_home / "projects.json").exists()
