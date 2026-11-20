#!/usr/bin/env python3
"""Tray app for Tailscale inside ExpressVPN: tray icon, status window,
quick actions and notifications when something changes."""
import fcntl
import os
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GLib, Gtk, Notify  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tsvpn_status as backend  # noqa: E402

REFRESH_SECONDS = 5
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
# green: all good · yellow: Tailscale side not working · red: risk of leakage
ICONS = {"ok": "tsvpn-green", "warn": "tsvpn-yellow", "bad": "tsvpn-red"}
COLORS = {"ok": "#27ae60", "warn": "#f1c40f", "bad": "#da4453", "off": "#7f8c8d"}
Gtk.IconTheme.get_default().append_search_path(ICON_DIR)
APP_NAME = "Tailscale VPN Status"


def dot(level):
    return '<span foreground="%s">●</span>' % COLORS[level]


def esc(text):
    return GLib.markup_escape_text(str(text))


def rows_for(s):
    """(level, label, value) rows shared by the menu and the window."""
    v, t, h = s["vpn"], s["tailscale"], s["host"]
    rows = []
    if v["connected"]:
        rows.append(("ok", "ExpressVPN", "Connected · %s" % v["region"]))
    elif s["level"] == "bad":
        rows.append(("bad", "ExpressVPN", v["state"]))
    else:
        rows.append(("warn", "ExpressVPN", v["state"]))
    rows.append(("ok" if v["networklock"] else "off", "Network Lock", v["networklock"] or "unknown"))
    rows.append(("ok" if s["web_protected"] else "bad", "Web traffic",
                 "through VPN" if s["web_protected"] else "visible to this network (%s)" % s["web_route"]))
    rows.append(("ok" if t["state"] == "Running" else "warn", "Tailscale",
                 t["state"] + (" · " + t["self_ip"] if t["self_ip"] and t["state"] == "Running" else "")))
    if h["reachable"]:
        hv = "%s · %.0f ms · %s" % (h["ip"], h["rtt_ms"] or 0, h["path"] or "connected")
        rows.append(("ok", h["name"], hv))
    elif h["found"]:
        rows.append(("warn", h["name"], "not answering" + ("" if h["online"] else " · offline")))
    else:
        rows.append(("warn", h["name"], "not found"))
    rows.append(("ok" if h["dns"] else "warn", "Name lookup", "working" if h["dns"] else "not working"))
    bad_services = [n for n, st in s["services"].items() if st != "active"]
    rows.append(("ok" if not bad_services and s["routing_rule"] else "warn", "Helper services",
                 "running" if not bad_services and s["routing_rule"] else "needs repair"))
    return rows


class StatusWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title=APP_NAME)
        self.app = app
        self.set_default_size(460, -1)
        self.set_border_width(16)
        self.set_icon_name("tsvpn-green")
        self.connect("delete-event", lambda *a: self.hide() or True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(box)

        head = Gtk.Box(spacing=10)
        self.head_icon = Gtk.Image.new_from_icon_name("tsvpn-yellow", Gtk.IconSize.DIALOG)
        head.pack_start(self.head_icon, False, False, 0)
        self.summary = Gtk.Label(xalign=0)
        self.summary.set_line_wrap(True)
        head.pack_start(self.summary, True, True, 0)
        box.pack_start(head, False, False, 0)

        self.grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        box.pack_start(self.grid, False, False, 0)

        self.problems = Gtk.Label(xalign=0)
        self.problems.set_line_wrap(True)
        box.pack_start(self.problems, False, False, 0)

        buttons = Gtk.Box(spacing=6)
        self.vpn_btn = Gtk.Button(label="Disconnect VPN")
        self.vpn_btn.connect("clicked", lambda b: self.app.toggle_vpn())
        self.ts_btn = Gtk.Button(label="Tailscale off")
        self.ts_btn.connect("clicked", lambda b: self.app.toggle_tailscale())
        repair = Gtk.Button(label="Repair")
        repair.set_tooltip_text("Restart the routing and firewall helper services (asks for your password)")
        repair.connect("clicked", lambda b: self.app.action("repair"))
        refresh = Gtk.Button.new_from_icon_name("view-refresh", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text("Refresh now")
        refresh.connect("clicked", lambda b: self.app.refresh())
        for b in (self.vpn_btn, self.ts_btn, repair):
            buttons.pack_start(b, False, False, 0)
        buttons.pack_end(refresh, False, False, 0)
        box.pack_start(buttons, False, False, 0)

        self.updated = Gtk.Label(xalign=0)
        self.updated.get_style_context().add_class("dim-label")
        box.pack_start(self.updated, False, False, 0)

    def update(self, s):
        self.head_icon.set_from_icon_name(ICONS[s["level"]], Gtk.IconSize.DIALOG)
        self.summary.set_markup("<big><b>%s</b></big>" % esc(s["summary"]))
        for child in self.grid.get_children():
            self.grid.remove(child)
        for i, (level, label, value) in enumerate(rows_for(s)):
            d = Gtk.Label()
            d.set_markup(dot(level))
            name = Gtk.Label(label=label, xalign=0)
            val = Gtk.Label(label=value, xalign=0)
            self.grid.attach(d, 0, i, 1, 1)
            self.grid.attach(name, 1, i, 1, 1)
            self.grid.attach(val, 2, i, 1, 1)
        if s["problems"]:
            self.problems.set_markup("\n".join("• " + esc(p) for p in s["problems"]))
            self.problems.show()
        else:
            self.problems.hide()
        self.vpn_btn.set_label("Disconnect VPN" if s["vpn"]["connected"] else "Connect VPN")
        self.ts_btn.set_label("Tailscale off" if s["tailscale"]["state"] == "Running" else "Tailscale on")
        self.updated.set_text("Updated %s · refreshes every %ds" % (GLib.DateTime.new_now_local().format("%H:%M:%S"),
                                                                    REFRESH_SECONDS))
        self.grid.show_all()


class TrayApp:
    def __init__(self):
        Notify.init(APP_NAME)
        self.status = None
        self.busy = False
        self.ind = AppIndicator.Indicator.new_with_path("tsvpn-status", "tsvpn-yellow",
                                                        AppIndicator.IndicatorCategory.SYSTEM_SERVICES,
                                                        ICON_DIR)
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_title(APP_NAME)
        self.window = StatusWindow(self)
        self.build_menu()
        self.refresh()
        GLib.timeout_add_seconds(REFRESH_SECONDS, self.tick)

    def build_menu(self):
        m = Gtk.Menu()
        self.summary_item = Gtk.MenuItem(label="Checking…")
        self.summary_item.connect("activate", lambda i: self.show_window())
        m.append(self.summary_item)
        m.append(Gtk.SeparatorMenuItem())
        self.row_items = []
        for _ in range(7):
            it = Gtk.MenuItem(label="")
            it.set_sensitive(False)
            m.append(it)
            self.row_items.append(it)
        m.append(Gtk.SeparatorMenuItem())
        self.vpn_item = Gtk.MenuItem(label="Disconnect VPN")
        self.vpn_item.connect("activate", lambda i: self.toggle_vpn())
        m.append(self.vpn_item)
        self.ts_item = Gtk.MenuItem(label="Turn Tailscale off")
        self.ts_item.connect("activate", lambda i: self.toggle_tailscale())
        m.append(self.ts_item)
        repair = Gtk.MenuItem(label="Repair helper services…")
        repair.connect("activate", lambda i: self.action("repair"))
        m.append(repair)
        m.append(Gtk.SeparatorMenuItem())
        for label, cb in (("Show status window", lambda i: self.show_window()),
                          ("Open documentation", lambda i: self.open_docs()),
                          ("Quit", lambda i: Gtk.main_quit())):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", cb)
            m.append(it)
        m.show_all()
        self.ind.set_menu(m)
        self.ind.set_secondary_activate_target(self.summary_item)

    def tick(self):
        self.refresh()
        return True

    def refresh(self):
        if self.busy:
            return
        self.busy = True

        def work():
            try:
                s = backend.collect()
            except Exception as e:  # keep the tray alive whatever happens
                s = None
                print("status check failed:", e, file=sys.stderr)
            GLib.idle_add(self.apply, s)

        threading.Thread(target=work, daemon=True).start()

    def apply(self, s):
        self.busy = False
        if s is None:
            return False
        old = self.status
        self.status = s
        self.ind.set_icon_full(ICONS[s["level"]], s["summary"])
        self.ind.set_title(s["summary"])
        self.summary_item.set_label(s["summary"])
        rows = rows_for(s)
        for it, (level, label, value) in zip(self.row_items, rows):
            mark = {"ok": "✓", "warn": "!", "bad": "✗", "off": "·"}[level]
            it.set_label("%s  %s: %s" % (mark, label, value))
        self.vpn_item.set_label("Disconnect VPN" if s["vpn"]["connected"] else "Connect VPN")
        self.ts_item.set_label("Turn Tailscale off" if s["tailscale"]["state"] == "Running" else "Turn Tailscale on")
        if self.window.get_visible():
            self.window.update(s)
        if old:
            self.notify_changes(old, s)
        return False

    def notify_changes(self, old, s):
        msgs = []
        if old["vpn"]["connected"] and not s["vpn"]["connected"]:
            msgs.append("ExpressVPN disconnected (%s)" % s["vpn"]["state"])
        if not old["vpn"]["connected"] and s["vpn"]["connected"]:
            msgs.append("ExpressVPN connected · %s" % s["vpn"]["region"])
        if old["host"]["reachable"] and not s["host"]["reachable"]:
            msgs.append("%s stopped answering" % s["host"]["name"])
        if not old["host"]["reachable"] and s["host"]["reachable"]:
            msgs.append("%s is reachable again" % s["host"]["name"])
        if old["web_protected"] and not s["web_protected"]:
            msgs.append("Web traffic is no longer going through the VPN")
        if msgs:
            n = Notify.Notification.new(s["summary"], "\n".join(msgs),
                                        os.path.join(ICON_DIR, ICONS[s["level"]] + ".svg"))
            n.set_urgency(Notify.Urgency.CRITICAL if s["level"] == "bad" else Notify.Urgency.NORMAL)
            try:
                n.show()
            except GLib.Error:
                pass

    def show_window(self):
        if self.status:
            self.window.update(self.status)
        self.window.show_all()
        if self.status and not self.status["problems"]:
            self.window.problems.hide()
        self.window.present()

    def open_docs(self):
        subprocess.Popen(["xdg-open", backend.DOCS])

    def toggle_vpn(self):
        connected = self.status and self.status["vpn"]["connected"]
        self.action("vpn-disconnect" if connected else "vpn-connect")

    def toggle_tailscale(self):
        running = self.status and self.status["tailscale"]["state"] == "Running"
        self.action("ts-down" if running else "ts-up")

    def action(self, name):
        def work():
            code, out = backend.do_action(name)
            if code != 0 and name != "repair":
                GLib.idle_add(self.error, name, out)
            GLib.idle_add(self.refresh)

        threading.Thread(target=work, daemon=True).start()

    def error(self, name, out):
        n = Notify.Notification.new("Action failed: " + name, out or "No output", "dialog-error")
        try:
            n.show()
        except GLib.Error:
            pass
        return False


def single_instance():
    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "tsvpn-tray.lock")
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Already running.")
        sys.exit(0)
    return f


if __name__ == "__main__":
    _lock = single_instance()
    app = TrayApp()
    if "--window" in sys.argv:
        GLib.timeout_add(800, lambda: app.show_window() or False)
    Gtk.main()
