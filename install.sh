#!/usr/bin/env bash
# Install homelab-shell-mcp on Debian 12. Run as root from the repo directory.
set -euo pipefail

APP_DIR=/opt/homelab-shell-mcp
LOG_DIR=/var/log/homelab-shell-mcp
ENV_FILE=/etc/homelab-shell-mcp.env

apt-get update && apt-get install -y curl

# uv gives us Python 3.12 (Debian 12 ships 3.11).
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
fi
export UV_PYTHON_INSTALL_DIR=/usr/local/share/uv/python
mkdir -p "$UV_PYTHON_INSTALL_DIR"

id mcpshell >/dev/null 2>&1 || \
    useradd --system --shell /usr/sbin/nologin --home-dir "$APP_DIR" mcpshell

install -d -o mcpshell -g mcpshell "$APP_DIR" "$LOG_DIR"
install -m 644 pyproject.toml "$APP_DIR/"
install -m 644 src/homelab_shell_mcp.py "$APP_DIR/src/homelab_shell_mcp.py" 2>/dev/null || {
    mkdir -p "$APP_DIR/src"
    install -m 644 src/homelab_shell_mcp.py "$APP_DIR/src/"
}

sudo -u mcpshell uv venv --python 3.12 "$APP_DIR/.venv"
sudo -u mcpshell uv pip install --python "$APP_DIR/.venv/bin/python" "$APP_DIR"

install -m 644 homelab-shell-mcp.service /etc/systemd/system/
if [ ! -f "$ENV_FILE" ]; then
    install -m 600 /dev/null "$ENV_FILE"
    echo "MCP_AUTH_TOKEN=$(head -c 32 /dev/urandom | base64)" > "$ENV_FILE"
    echo "Generated MCP_AUTH_TOKEN in $ENV_FILE -- save it for the connector config."
fi

systemctl daemon-reload
echo "Done. Start with: systemctl enable --now homelab-shell-mcp"
