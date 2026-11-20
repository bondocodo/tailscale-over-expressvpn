#!/bin/sh
# Installs the tsvpn command, the tray app and the Plasma widget for your user.
# No sudo needed.
#   sh install-app.sh            install or update
#   sh install-app.sh remove     uninstall all three
set -e
APPDIR="$(cd "$(dirname "$0")" && pwd)"
WIDGET_ID=local.tsvpn.status
APPS="$HOME/.local/share/applications"
AUTOSTART="$HOME/.config/autostart"
BUILD="${XDG_RUNTIME_DIR:-/tmp}/tsvpn-plasmoid"
BIN="$HOME/.local/bin"
ROOT="$(dirname "$APPDIR")"

if [ "$1" = remove ]; then
  pkill -f "^python3 $APPDIR/tsvpn_tray.py" 2>/dev/null || true
  rm -f "$APPS/tsvpn-status.desktop" "$AUTOSTART/tsvpn-status.desktop"
  kpackagetool6 -t Plasma/Applet -r "$WIDGET_ID" 2>/dev/null || true
  [ "$(readlink "$BIN/tsvpn")" = "$ROOT/tsvpn" ] && rm -f "$BIN/tsvpn"
  echo "Removed the tsvpn command, tray app and widget. Remove the widget from your desktop or panel if it's still placed there."
  exit 0
fi

chmod 755 "$APPDIR/tsvpn_status.py" "$APPDIR/tsvpn_tray.py" "$ROOT/tsvpn"

echo "== tsvpn command"
mkdir -p "$BIN"
ln -sf "$ROOT/tsvpn" "$BIN/tsvpn"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "   Note: $BIN is not on your PATH. Add it, or run $ROOT/tsvpn directly." ;;
esac

echo "== Plasma widget"
rm -rf "$BUILD"
cp -r "$APPDIR/plasmoid" "$BUILD"
sed -i "s|@APPDIR@|$APPDIR|g" "$BUILD/contents/ui/main.qml"
if kpackagetool6 -t Plasma/Applet -l 2>/dev/null | grep -qx "$WIDGET_ID"; then
  kpackagetool6 -t Plasma/Applet -u "$BUILD"
else
  kpackagetool6 -t Plasma/Applet -i "$BUILD"
fi
rm -rf "$BUILD"

echo "== Tray app (menu entry + autostart)"
mkdir -p "$APPS" "$AUTOSTART"
cat > "$APPS/tsvpn-status.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tailscale VPN Status
Comment=Monitor ExpressVPN and your Tailscale host
Exec=python3 "$APPDIR/tsvpn_tray.py" --window
Icon=$APPDIR/icons/tsvpn-green.svg
Categories=Network;System;Monitor;
Terminal=false
EOF
sed 's|--window||' "$APPS/tsvpn-status.desktop" > "$AUTOSTART/tsvpn-status.desktop"
echo "X-GNOME-Autostart-Delay=5" >> "$AUTOSTART/tsvpn-status.desktop"

echo "== Starting tray app"
pkill -f "^python3 $APPDIR/tsvpn_tray.py" 2>/dev/null || true
sleep 1
nohup python3 "$APPDIR/tsvpn_tray.py" >/dev/null 2>&1 &

echo
echo "Done."
echo " - Tray icon: look for the shield in the system tray (it may be under the ^ arrow)."
echo " - Command line: run 'tsvpn' (or 'tsvpn help')."
echo " - Widget: right-click the desktop or panel > Add Widgets > search 'Tailscale VPN Status'."
