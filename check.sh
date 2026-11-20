#!/bin/sh
# Tailscale inside ExpressVPN — status check. No sudo needed, and nothing is
# sent to outside services: every check is local.
#   sh check.sh [host]      (default: TSVPN_HOST from config.env)
DIR="$(cd "$(dirname "$0")" && pwd)"
[ -f "$DIR/config.env" ] && . "$DIR/config.env"
HOST="${1:-${TSVPN_HOST:-}}"
ok()   { printf '  [ OK ] %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; }

echo "ExpressVPN"
expressvpnctl status 2>/dev/null | grep -E 'Connected|Network Lock' | sed 's/^/         /'
[ "$(expressvpnctl get connectionstate 2>/dev/null)" = Connected ] && ok "VPN connected" || bad "VPN not connected"
ip route get 1.1.1.1 2>/dev/null | grep -q 'dev tun0' \
  && ok "internet traffic goes through the VPN (tun0)" \
  || bad "internet traffic is NOT going through the VPN"

echo "Services"
for s in tailscaled tailscale-over-vpn tailscale-vpn-guard; do
  [ "$(systemctl is-active $s.service)" = active ] && ok "$s running" || bad "$s not running"
done
ip rule | grep -q '^60:.*100.64.0.0/10 lookup 52' && ok "routing rule present" || bad "routing rule missing"

echo "Tailscale"
tailscale status >/dev/null 2>&1 && ok "Tailscale up" || bad "Tailscale down (run: tailscale up)"
if [ -z "$HOST" ]; then
  bad "no host set: copy config.example.env to config.env and set TSVPN_HOST, or pass a host name"
  exit 1
fi
IP=$(timeout 3 getent hosts "$HOST" | awk '{print $1; exit}')
[ -n "$IP" ] && ok "$HOST resolves to $IP" || { bad "$HOST does not resolve"; IP=$(tailscale ip -4 "$HOST" 2>/dev/null); }
if [ -n "$IP" ]; then
  ip route get "$IP" | grep -q 'dev tailscale0' && ok "$HOST routed via tailscale0" || bad "$HOST not routed via tailscale0"
  ping -c 2 -W 3 "$IP" >/dev/null 2>&1 && ok "$HOST answers ping" || bad "$HOST does not answer ping"
fi
