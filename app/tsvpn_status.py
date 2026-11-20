#!/usr/bin/env python3
"""Status checker for Tailscale inside ExpressVPN. Shared by the tray app and
the Plasma widget. No root needed.

  tsvpn_status.py                 print status as JSON
  tsvpn_status.py --host NAME     check a different Tailscale device
  tsvpn_status.py --demo LEVEL    made-up status (ok | warn | bad) for screenshots
  tsvpn_status.py action ACTION   vpn-connect | vpn-disconnect | ts-up | ts-down | repair
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "README.md")


def load_config():
    """Read KEY=VALUE lines from config.env (kept out of git). Env vars win."""
    cfg = {}
    try:
        with open(os.path.join(ROOT, "config.env")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"\'')
    except OSError:
        pass
    cfg.update({k: v for k, v in os.environ.items() if k.startswith("TSVPN_")})
    return cfg


# Empty host = watch the first Tailscale device found.
DEFAULT_HOST = load_config().get("TSVPN_HOST", "")
SERVICES = ["tailscaled", "tailscale-over-vpn", "tailscale-vpn-guard"]


def run(cmd, timeout=4):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, ""


def route_dev(ip):
    _, out = run(["ip", "route", "get", ip], timeout=2)
    m = re.search(r"\bdev (\S+)", out)
    return m.group(1) if m else None


def vpn_status():
    code, out = run(["expressvpnctl", "status"])
    v = {"installed": code != -1, "state": "Unknown", "region": "", "protocol": "", "networklock": ""}
    _, state = run(["expressvpnctl", "get", "connectionstate"])
    if state:
        v["state"] = state
    m = re.search(r"Connected to (\S+)", out)
    if m:
        v["region"] = m.group(1)
    m = re.search(r"Protocol in use: (\S+)", out)
    if m:
        v["protocol"] = m.group(1)
    m = re.search(r"Network Lock: (.+)", out)
    if m:
        v["networklock"] = m.group(1).strip()
    v["connected"] = v["state"] == "Connected"
    return v


def tailscale_status(host):
    code, out = run(["tailscale", "status", "--json"])
    t = {"state": "Stopped", "online": False, "self_ip": "", "health": []}
    h = {"name": host or "host", "found": False, "ip": "", "online": False, "path": "", "os": ""}
    try:
        d = json.loads(out) if out else {}
    except ValueError:
        d = {}
    if d:
        t["state"] = d.get("BackendState", "Unknown")
        me = d.get("Self") or {}
        t["online"] = bool(me.get("Online"))
        t["self_ip"] = (me.get("TailscaleIPs") or [""])[0]
        t["health"] = d.get("Health") or []
        for p in (d.get("Peer") or {}).values():
            names = {p.get("HostName", "").lower(), p.get("DNSName", "").split(".")[0].lower()}
            if not host or host.lower() in names:
                h["found"] = True
                h["name"] = p.get("DNSName", "").split(".")[0] or p.get("HostName", host)
                h["ip"] = (p.get("TailscaleIPs") or [""])[0]
                h["online"] = bool(p.get("Online"))
                h["os"] = p.get("OS", "")
                if p.get("CurAddr"):
                    h["path"] = "direct"
                elif p.get("Relay"):
                    h["path"] = "relay (" + p["Relay"] + ")"
                break
    return t, h


def ping(ip):
    code, out = run(["ping", "-c", "1", "-W", "1", ip], timeout=3)
    m = re.search(r"time=([\d.]+)", out)
    return code == 0, (float(m.group(1)) if m else None)


def collect(host=DEFAULT_HOST):
    s = {"time": int(time.time()), "docs": DOCS}
    s["vpn"] = vpn_status()
    s["web_route"] = route_dev("1.1.1.1") or "none"
    s["web_protected"] = s["web_route"] == "tun0"
    s["tailscale"], s["host"] = tailscale_status(host)
    h = s["host"]
    h["route_dev"] = route_dev(h["ip"]) if h["ip"] else None
    h["reachable"], h["rtt_ms"] = ping(h["ip"]) if h["ip"] and s["tailscale"]["state"] == "Running" else (False, None)
    code, _ = run(["getent", "hosts", h["name"]], timeout=2) if h["found"] else (1, "")
    h["dns"] = code == 0
    _, out = run(["systemctl", "is-active"] + [x + ".service" for x in SERVICES])
    states = out.splitlines()
    s["services"] = {name: (states[i] if i < len(states) else "unknown") for i, name in enumerate(SERVICES)}
    _, rules = run(["ip", "rule"], timeout=2)
    s["routing_rule"] = bool(re.search(r"^60:.*100\.64\.0\.0/10 lookup 52", rules, re.M))
    return evaluate(s, host)


def evaluate(s, host=""):
    """Add level, problems and summary to raw check results."""
    h = s["host"]
    # Colour rules:
    #   bad  (red)    risk of leakage: web traffic is not going through the VPN
    #   warn (yellow) no leak, but something is not working (Tailscale, host, helpers)
    #   ok   (green)  everything checks out
    problems = []
    v, t = s["vpn"], s["tailscale"]
    leak = not s["web_protected"]
    transitional = v["state"] in ("Interrupted", "Reconnecting", "Connecting", "DisconnectingToReconnect")
    if v["connected"] and leak:
        problems.append("VPN says connected but web traffic is not going through it")
    elif transitional:
        problems.append("VPN is " + v["state"].lower() + (
            ": web traffic may be exposed" if leak else "; internet is blocked until it reconnects"))
    elif not v["connected"]:
        problems.append("VPN is off: web traffic is visible to this network")
    if t["state"] != "Running":
        problems.append("Tailscale is " + t["state"].lower())
    elif not h["found"]:
        problems.append((host or "No device") + " not found in your tailnet")
    elif not h["reachable"]:
        problems.append(h["name"] + " is not answering" + ("" if h["online"] else " (Tailscale shows it offline)"))
    elif not h["dns"]:
        problems.append(h["name"] + " name does not resolve; use " + h["ip"])
    if h["ip"] and h["route_dev"] and h["route_dev"] != "tailscale0":
        problems.append("traffic to " + h["name"] + " goes via " + h["route_dev"] + ", not Tailscale (routing rule)")
    for name, st in s["services"].items():
        if st != "active":
            problems.append(name + " service is " + st)
    if not s["routing_rule"]:
        problems.append("Tailscale routing rule is missing")
    level = "bad" if leak else ("warn" if problems else "ok")
    s["level"] = level
    s["problems"] = problems

    if level == "ok":
        s["summary"] = "Protected · " + h["name"] + " reachable"
    else:
        first = problems[0][0].upper() + problems[0][1:]
        s["summary"] = ("Protected · " + problems[0]) if (level == "warn" and v["connected"]) else first
    return s


def demo(level="ok"):
    """Made-up status for screenshots and testing. Contains no real data."""
    s = {
        "time": int(time.time()), "docs": DOCS,
        "vpn": {"installed": True, "state": "Connected", "region": "germany-frankfurt-1",
                "protocol": "LightwayUdp", "networklock": "enabled when connected", "connected": True},
        "web_route": "tun0", "web_protected": True,
        "tailscale": {"state": "Running", "online": True, "self_ip": "100.64.0.2", "health": []},
        "host": {"name": "home-pc", "found": True, "ip": "100.64.0.10", "online": True, "path": "direct",
                 "os": "windows", "route_dev": "tailscale0", "reachable": True, "rtt_ms": 42.0, "dns": True},
        "services": {name: "active" for name in SERVICES},
        "routing_rule": True,
    }
    if level == "warn":
        s["host"].update(reachable=False, rtt_ms=None)
    elif level == "bad":
        s["vpn"].update(state="Disconnected", connected=False, region="")
        s["web_route"], s["web_protected"] = "wlan0", False
    return evaluate(s, "home-pc")


def rows(s):
    """(level, label, value) rows shared by the CLI, tray menu and window."""
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


ACTIONS = {
    "vpn-connect": ["expressvpnctl", "connect"],
    "vpn-disconnect": ["expressvpnctl", "disconnect"],
    "ts-up": ["tailscale", "up"],
    "ts-down": ["tailscale", "down"],
    "repair": ["pkexec", "systemctl", "restart", "tailscale-over-vpn.service", "tailscale-vpn-guard.service"],
}


def do_action(name):
    if name not in ACTIONS:
        return 2, "unknown action " + name
    return run(ACTIONS[name], timeout=120)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["action"] and len(args) == 2:
        code, out = do_action(args[1])
        print(json.dumps({"action": args[1], "code": code, "output": out}))
        sys.exit(0 if code == 0 else 1)
    if args[:1] == ["--demo"]:
        print(json.dumps(demo(args[1] if len(args) > 1 else "ok")))
        sys.exit(0)
    host = DEFAULT_HOST
    if args[:1] == ["--host"] and len(args) == 2:
        host = args[1]
    print(json.dumps(collect(host)))
