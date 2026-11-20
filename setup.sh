#!/bin/sh
# Tailscale inside ExpressVPN — full setup.
# Reproduces everything configured on 2026-11-20. Safe to run again.
#
#   sudo sh setup.sh
#
# Undo with: sudo sh uninstall.sh
set -e
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo sh $0"; exit 1; }
USER_NAME="${SUDO_USER:-}"
as_user() { if [ -n "$USER_NAME" ]; then sudo -u "$USER_NAME" "$@"; else "$@"; fi; }

echo "== 1/6 ExpressVPN: connect at startup, block LAN access, Network Lock 'when connected'"
as_user expressvpnctl background enable
as_user expressvpnctl set autoconnect true
as_user expressvpnctl set allowlan false
# 'false' = "enabled when connected" (blocks traffic if the VPN drops, but lets
# you disconnect on purpose). 'true' = "always enabled" (no internet without VPN).
expressvpnctl set networklock false

echo "== 2/6 Routing service: Tailscale addresses go to tailscale0 before ExpressVPN's catch-all"
cat > /etc/systemd/system/tailscale-over-vpn.service <<'EOF'
[Unit]
Description=Route Tailscale traffic ahead of ExpressVPN's catch-all rule
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'ip rule del priority 60 2>/dev/null; ip rule add to 100.64.0.0/10 lookup 52 priority 60; ip -6 rule del priority 60 2>/dev/null; ip -6 rule add to fd7a:115c:a1e0::/48 lookup 52 priority 60'
ExecStop=/bin/sh -c 'ip rule del priority 60 2>/dev/null; ip -6 rule del priority 60 2>/dev/null; true'

[Install]
WantedBy=multi-user.target
EOF

echo "== 3/6 Firewall guard: allow traffic out through tailscale0 past ExpressVPN's Network Lock"
cat > /usr/local/sbin/tailscale-vpn-guard <<'EOF'
#!/bin/sh
# Keeps "accept out tailscale0" as the first OUTPUT rule. ExpressVPN rebuilds
# its firewall on every reconnect, so this re-checks every 5 seconds.
RULE="-o tailscale0 -j ACCEPT"
ensure() {
  first=$($1 -w -S OUTPUT 2>/dev/null | sed -n 2p)
  [ "$first" = "-A OUTPUT $RULE" ] && return 0
  while $1 -w -D OUTPUT $RULE 2>/dev/null; do :; done
  $1 -w -I OUTPUT 1 $RULE
}
clear_rules() {
  for c in iptables ip6tables; do while $c -w -D OUTPUT $RULE 2>/dev/null; do :; done; done
}
[ "$1" = stop ] && { clear_rules; exit 0; }
while true; do
  ensure iptables; ensure ip6tables
  sleep 5
done
EOF
chmod 755 /usr/local/sbin/tailscale-vpn-guard

cat > /etc/systemd/system/tailscale-vpn-guard.service <<'EOF'
[Unit]
Description=Keep Tailscale traffic allowed through ExpressVPN Network Lock
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
ExecStart=/usr/local/sbin/tailscale-vpn-guard
ExecStopPost=/usr/local/sbin/tailscale-vpn-guard stop
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tailscale-over-vpn.service tailscale-vpn-guard.service
systemctl restart tailscale-over-vpn.service tailscale-vpn-guard.service

echo "== 4/6 Firewall (ufw): block incoming from the local network, allow Tailscale"
ufw default deny incoming
ufw default allow outgoing
ufw allow in on tailscale0
ufw --force enable

echo "== 5/6 Tailscale: start at boot, controllable without sudo"
systemctl enable --now tailscaled
[ -n "$USER_NAME" ] && tailscale set --operator="$USER_NAME"
tailscale up

echo "== 6/6 Done"
echo
echo "Check everything with: sh $(dirname "$0")/check.sh"
