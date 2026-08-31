"""homelab-shell-mcp: security-first shell execution MCP server.

Verified against FastMCP 3.4.7 (installed version). Auth API
(StaticTokenVerifier, FastMCP(auth=...)) and http_app() are version-sensitive;
re-check if fastmcp is upgraded beyond 3.x.
"""

import json
import os
import shlex
import shutil
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import StaticTokenVerifier

DENYLIST: frozenset[str] = frozenset(
    {"rm", "sudo", "su", "dd", "mkfs", "shutdown", "reboot", "chmod", "chown"}
)
DEFAULT_ALLOWED = (
    "ls,cat,df,free,uptime,ps,docker,systemctl,journalctl,tailscale,ping"
)
MAX_OUTPUT_BYTES = 50 * 1024
AUDIT_DEFAULT = "/var/log/homelab-shell-mcp/audit.jsonl"
# Runtime whitelist state; dir is owned by mcpshell in both installers.
ALLOWED_STATE_DEFAULT = "/opt/homelab-shell-mcp/allowed-commands"


def _allowed() -> set[str]:
    state = Path(os.environ.get("MCP_ALLOWED_STATE", ALLOWED_STATE_DEFAULT))
    if state.is_file():
        raw = state.read_text(encoding="utf-8")
    else:
        raw = os.environ.get("MCP_ALLOWED_COMMANDS", DEFAULT_ALLOWED)
    return {c.strip() for c in raw.split(",") if c.strip()}


def _timeout() -> int:
    return int(os.environ.get("MCP_CMD_TIMEOUT", "60"))


def _audit_path() -> Path:
    return Path(os.environ.get("MCP_AUDIT_LOG", AUDIT_DEFAULT))


def _audit(entry: dict[str, Any]) -> None:
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _validate(command: str) -> list[str]:
    """Parse and policy-check a command string. Returns argv or raises ToolError."""
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise ToolError(f"unparseable command: {e}") from e
    if not argv:
        raise ToolError("empty command")

    prog = argv[0]
    if "/" in prog:
        raise ToolError(f"rejected: path in program name {prog!r}; use a bare command name")

    base = prog  # no '/' so prog is already the basename
    if base in DENYLIST:
        raise ToolError(f"rejected: {base!r} is on the hard denylist")
    if base not in _allowed():
        raise ToolError(f"rejected: {base!r} is not whitelisted")
    if base in {"curl", "wget"} and any(t in argv[1:] for t in ("|", ">", ">>")):
        raise ToolError(f"rejected: {base} pipe/redirect constructs are denied")

    resolved = shutil.which(base)
    if resolved is None:
        raise ToolError(f"rejected: {base!r} not found on PATH")
    argv[0] = resolved
    return argv


def _truncate(b: bytes) -> tuple[str, bool]:
    if len(b) > MAX_OUTPUT_BYTES:
        return b[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), True
    return b.decode("utf-8", errors="replace"), False


def _execute(argv: list[str]) -> dict[str, Any]:
    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # own process group so timeout kills children too
    )
    timed_out = False
    try:
        out_b, err_b = proc.communicate(timeout=_timeout())
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        out_b, err_b = proc.communicate()
    duration_ms = int((time.monotonic() - start) * 1000)

    stdout, t1 = _truncate(out_b)
    stderr, t2 = _truncate(err_b)
    if timed_out:
        stderr += f"\n[killed: exceeded {_timeout()}s timeout]"
    return {
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "truncated": t1 or t2,
    }


auth_token = os.environ.get("MCP_AUTH_TOKEN", "")
mcp = FastMCP(
    "homelab-shell-mcp",
    auth=StaticTokenVerifier(tokens={auth_token: {"client_id": "perplexity", "scopes": []}})
    if auth_token
    else None,
)


@mcp.tool
def run_command(command: str) -> dict[str, Any]:
    """Run a whitelisted shell command and return exit code, stdout, stderr."""
    started = datetime.now(UTC).isoformat()
    try:
        argv = _validate(command)
    except ToolError as e:
        _audit(
            {"timestamp": started, "command": command, "argv": None,
             "exit_code": None, "duration_ms": 0, "truncated": False,
             "blocked": str(e)}
        )
        raise
    result = _execute(argv)
    _audit(
        {"timestamp": started, "command": command, "argv": argv,
         "exit_code": result["exit_code"], "duration_ms": result["duration_ms"],
         "truncated": result["truncated"]}
    )
    return result


@mcp.tool
def list_allowed_commands() -> dict[str, Any]:
    """Return the current command whitelist and the hard denylist."""
    return {"allowed": sorted(_allowed()), "denied": sorted(DENYLIST)}


@mcp.tool
def set_allowed_commands(commands: list[str]) -> dict[str, Any]:
    """Replace the command whitelist (persisted, takes effect immediately).

    The hard denylist is not editable — it still wins over anything added here.
    """
    cleaned: set[str] = set()
    for c in commands:
        c = c.strip()
        if not c:
            continue
        if "/" in c or any(ch.isspace() for ch in c):
            raise ToolError(f"invalid command name {c!r}; use bare names like 'ls'")
        cleaned.add(c)
    if not cleaned:
        raise ToolError("refusing to set an empty whitelist")
    state = Path(os.environ.get("MCP_ALLOWED_STATE", ALLOWED_STATE_DEFAULT))
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(",".join(sorted(cleaned)) + "\n", encoding="utf-8")
    _audit({"timestamp": datetime.now(UTC).isoformat(), "command": None,
            "argv": None, "exit_code": None, "duration_ms": 0,
            "truncated": False, "config_change": {"allowed": sorted(cleaned)}})
    return {"allowed": sorted(cleaned), "denied": sorted(DENYLIST)}


@mcp.tool
def get_recent_commands(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent audit log entries (newest last)."""
    limit = max(1, min(limit, 100))
    path = _audit_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in lines if line.strip()]


def main() -> None:
    if not auth_token:
        raise SystemExit("MCP_AUTH_TOKEN is not set; refusing to start without auth")
    mcp.run(transport="http", host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()
