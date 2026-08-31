#!/bin/sh
# Run INSIDE an existing Proxmox LXC, as root:
#   sh -c "$(curl -fsSL https://raw.githubusercontent.com/NitsujY/homelab-shell-mcp/main/install-lxc.sh)"
# Detects Alpine vs Debian and runs the right installer. POSIX sh (no bash needed).
set -eu

REPO=https://github.com/NitsujY/homelab-shell-mcp.git
SRC=/opt/src/homelab-shell-mcp

# Tailscale needs /dev/net/tun. An unprivileged CT can't create it, so fail
# early with the exact host-side fix if it's missing.
if [ ! -c /dev/net/tun ]; then
    cat >&2 <<'EOF'
ERROR: /dev/net/tun is missing. On the Proxmox HOST run (replace CTID):

  CTID=<your-ct-id>
  printf 'lxc.cgroup2.devices.allow: c 10:200 rwm\nlxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file\n' >> /etc/pve/lxc/$CTID.conf
  pct reboot $CTID

Then re-run this installer.
EOF
    exit 1
fi

if command -v apk >/dev/null; then
    apk add --no-cache curl git tailscale
    rc-update add tailscale default
    rc-service tailscale start
elif command -v apt-get >/dev/null; then
    apt-get update -qq && apt-get install -y -qq curl git
    curl -fsSL https://tailscale.com/install.sh | sh
else
    echo "Unsupported distro: need apk (Alpine) or apt (Debian)." >&2
    exit 1
fi

cd /        # leave $SRC first in case the script was launched from inside it
rm -rf "$SRC"
git clone --depth 1 "$REPO" "$SRC"
cd "$SRC"

if command -v apk >/dev/null; then
    sh ./install-alpine.sh
else
    bash ./install.sh
    systemctl enable --now homelab-shell-mcp
fi

grep -q '^MCP_AUTH_TOKEN=' /etc/homelab-shell-mcp.env
TOKEN=$(cut -d= -f2- /etc/homelab-shell-mcp.env)   # f2- : base64 tokens end in =

echo
echo "=== App installed ==="
echo "MCP_AUTH_TOKEN: $TOKEN"
echo

if [ -n "${TS_AUTHKEY:-}" ]; then
    tailscale up --authkey="$TS_AUTHKEY"
else
    tailscale up    # prints a login URL; open it to join your tailnet
fi

tailscale funnel --bg 8080
tailscale funnel status

cat <<EOF

=== Done ===
Endpoint: https://$(hostname).<tailnet>.ts.net/mcp  (Authorization: Bearer <token>)
EOF
