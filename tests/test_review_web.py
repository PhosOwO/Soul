from __future__ import annotations

from http.server import ThreadingHTTPServer
from threading import Thread

import soul.api
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

    def fake_serve(project_dir, *, host, port):
        calls.append({"project_dir": project_dir, "host": host, "port": port})

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
    assert calls == [{"project_dir": tmp_path, "host": "127.0.0.1", "port": int(port)}]
