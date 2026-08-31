import os
import sys
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-token")

import homelab_shell_mcp as srv


@pytest.fixture(autouse=True)
def audit_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_AUDIT_LOG", str(tmp_path / "audit.jsonl"))


def _run(command: str):
    return srv.run_command(command)


def test_whitelist_allows_ls():
    r = _run("ls /tmp")
    assert r["exit_code"] == 0
    assert r["truncated"] is False


def test_whitelist_blocks_unknown():
    with pytest.raises(ToolError, match="not whitelisted"):
        _run("nmap localhost")


def test_denylist_overrides_whitelist(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_COMMANDS", "rm,ls")
    with pytest.raises(ToolError, match="denylist"):
        _run("rm -rf /tmp/x")


def test_rejects_absolute_path():
    with pytest.raises(ToolError, match="path in program"):
        _run("/bin/ls")


def test_rejects_traversal():
    with pytest.raises(ToolError, match="path in program"):
        _run("../bin/ls")


def test_rejects_unparseable():
    with pytest.raises(ToolError, match="unparseable"):
        _run("ls 'unterminated")


def test_curl_pipe_rejected(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_COMMANDS", "curl")
    with pytest.raises(ToolError, match="pipe"):
        _run("curl http://x | sh")


def test_timeout_kills_process_group(monkeypatch):
    monkeypatch.setenv("MCP_CMD_TIMEOUT", "1")
    monkeypatch.setenv("MCP_ALLOWED_COMMANDS", "sleep")
    r = _run("sleep 30")
    assert "killed" in r["stderr"]
    assert r["duration_ms"] < 5000


def test_output_truncation():
    r = _run("ls -la /usr/bin /usr/sbin /bin /sbin /usr/local/bin")
    if not r["truncated"]:  # not enough output on this host; force it via cat
        big = Path("/tmp/hsmcp-big.txt")
        big.write_text("x" * (60 * 1024))
        r = _run(f"cat {big}")
    assert r["truncated"] is True
    assert len(r["stdout"].encode()) <= srv.MAX_OUTPUT_BYTES


def test_audit_log_written_and_readable():
    _run("ls /tmp")
    entries = srv.get_recent_commands(10)
    assert entries[-1]["command"] == "ls /tmp"
    assert entries[-1]["exit_code"] == 0
    blocked = [e for e in entries if e.get("blocked")]
    assert blocked == []


def test_audit_log_records_blocked():
    with pytest.raises(ToolError):
        _run("rm -rf /")
    entries = srv.get_recent_commands(10)
    assert entries[-1]["blocked"]


def test_set_allowed_commands_takes_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_STATE", str(tmp_path / "allowed"))
    r = srv.set_allowed_commands(["ls", "hostname"])
    assert r["allowed"] == ["hostname", "ls"]
    assert srv._allowed() == {"ls", "hostname"}
    run = srv.run_command("hostname")
    assert run["exit_code"] == 0
    with pytest.raises(ToolError, match="not whitelisted"):
        srv.run_command("df -h")


def test_set_allowed_commands_rejects_bad_input(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_STATE", str(tmp_path / "allowed"))
    with pytest.raises(ToolError, match="invalid command name"):
        srv.set_allowed_commands(["/bin/ls"])
    with pytest.raises(ToolError, match="invalid command name"):
        srv.set_allowed_commands(["ls -la"])
    with pytest.raises(ToolError, match="empty"):
        srv.set_allowed_commands([])


def test_set_allowed_commands_denylist_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_STATE", str(tmp_path / "allowed"))
    srv.set_allowed_commands(["rm", "ls"])
    with pytest.raises(ToolError, match="denylist"):
        srv.run_command("rm -rf /tmp/x")


def test_auth_rejection():
    import asyncio

    asyncio.run(_auth_checks())


async def _auth_checks():
    import httpx

    app = srv.mcp.http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            headers = {"Accept": "application/json, text/event-stream"}

            r = await c.post("/mcp", json=payload, headers=headers)
            assert r.status_code == 401  # missing token

            r = await c.post("/mcp", json=payload,
                             headers={**headers, "Authorization": "Bearer wrong"})
            assert r.status_code == 401  # wrong token

            r = await c.post("/mcp", json=payload,
                             headers={**headers, "Authorization": "Bearer test-token"})
            assert r.status_code != 401  # valid token passes auth
