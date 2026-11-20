#!/bin/sh
# Tailscale inside ExpressVPN — undo.
#
#   sudo sh uninstall.sh              undo everything
#   sudo sh uninstall.sh guard        undo only the named part(s):
#   sudo sh uninstall.sh routing firewall
#
# Parts:
#   guard       firewall rule letting Tailscale past Network Lock (+ its service)
#   routing     ip rule sending Tailscale addresses to tailscale0 (+ its service)
#   firewall    ufw (removes the tailscale0 rule and turns ufw off)
#   expressvpn  restores the settings from before: no auto-connect, LAN allowed,
#               Network Lock "enabled when connected"
#   tailscale   removes the no-sudo operator setting
#
# Note: removing guard or routing while you still use ExpressVPN + Tailscale
# together breaks access to your Tailscale devices again.
set -e
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo sh $0 [parts]"; exit 1; }
USER_NAME="${SUDO_USER:-}"
as_user() { if [ -n "$USER_NAME" ]; then sudo -u "$USER_NAME" "$@"; else "$@"; fi; }

undo_guard() {
  echo "== Removing firewall guard"
  systemctl disable --now tailscale-vpn-guard.service 2>/dev/null || true
  rm -f /etc/systemd/system/tailscale-vpn-guard.service
  for c in iptables ip6tables; do
    while $c -w -D OUTPUT -o tailscale0 -j ACCEPT 2>/dev/null; do :; done
  done
  rm -f /usr/local/sbin/tailscale-vpn-guard
}

undo_routing() {
  echo "== Removing routing rule"
  systemctl disable --now tailscale-over-vpn.service 2>/dev/null || true
  rm -f /etc/systemd/system/tailscale-over-vpn.service
  while ip rule del priority 60 2>/dev/null; do :; done
  while ip -6 rule del priority 60 2>/dev/null; do :; done
}

undo_firewall() {
  echo "== Turning off ufw"
  ufw delete allow in on tailscale0 2>/dev/null || true
  ufw --force disable
}

undo_expressvpn() {
  echo "== Restoring ExpressVPN settings"
  as_user expressvpnctl set autoconnect false
  as_user expressvpnctl set allowlan true
  expressvpnctl set networklock false
  echo "   Background mode left on. To turn it off too (disconnects the VPN"
  echo "   if the ExpressVPN app isn't open): expressvpnctl background disable"
}

undo_tailscale() {
  echo "== Removing Tailscale operator setting"
  tailscale set --operator= 2>/dev/null || echo "   (could not clear operator; harmless)"
  echo "   Tailscale itself is left installed and running. To stop it at boot:"
  echo "   sudo systemctl disable --now tailscaled"
}

PARTS="${*:-guard routing firewall expressvpn tailscale}"
for p in $PARTS; do
  case "$p" in
    guard|routing|firewall|expressvpn|tailscale) "undo_$p" ;;
    all) undo_guard; undo_routing; undo_firewall; undo_expressvpn; undo_tailscale ;;
    *) echo "Unknown part: $p (use guard, routing, firewall, expressvpn, tailscale)"; exit 1 ;;
  esac
done
systemctl daemon-reload
echo "== Done"
