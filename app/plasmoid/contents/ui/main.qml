import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root

    // Replaced with the real path by install-app.sh
    readonly property string script: "@APPDIR@/tsvpn_status.py"
    readonly property string statusCmd: "python3 -I '" + script + "'"
    property var st: null
    property bool busy: false

    // green: all good · yellow: Tailscale side not working · red: risk of leakage
    readonly property string level: st ? st.level : "warn"
    readonly property string iconPath: "@APPDIR@/icons/tsvpn-" + (level === "ok" ? "green" : (level === "bad" ? "red" : "yellow")) + ".svg"
    readonly property string iconName: "file://" + iconPath
    readonly property color okColor: "#27ae60"
    readonly property color warnColor: "#f1c40f"
    readonly property color badColor: "#da4453"

    function colorFor(l) { return l === "ok" ? okColor : (l === "bad" ? badColor : (l === "off" ? Kirigami.Theme.disabledTextColor : warnColor)) }

    Plasmoid.icon: iconPath
    Plasmoid.title: "Tailscale VPN Status"
    toolTipMainText: st ? st.summary : "Checking…"
    toolTipSubText: st && st.problems.length ? st.problems.join("\n") : (st ? "ExpressVPN " + st.vpn.region + " · " + st.host.name + " " + Math.round(st.host.rtt_ms || 0) + " ms" : "")

    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar ? fullRepresentation : compactRepresentation

    function rows() {
        if (!st) return []
        var v = st.vpn, t = st.tailscale, h = st.host, r = []
        r.push([v.connected ? "ok" : (st.level === "bad" ? "bad" : "warn"), "ExpressVPN", v.connected ? "Connected · " + v.region : v.state])
        r.push([v.networklock ? "ok" : "off", "Network Lock", v.networklock || "unknown"])
        r.push([st.web_protected ? "ok" : "bad", "Web traffic", st.web_protected ? "through VPN" : "visible to this network"])
        r.push([t.state === "Running" ? "ok" : "warn", "Tailscale", t.state])
        if (h.reachable)
            r.push(["ok", h.name, Math.round(h.rtt_ms || 0) + " ms · " + (h.path || "connected")])
        else
            r.push(["warn", h.name, h.found ? (h.online ? "not answering" : "offline") : "not found"])
        r.push([h.dns ? "ok" : "warn", "Name lookup", h.dns ? "working" : "not working"])
        var svcOk = st.routing_rule
        for (var k in st.services) if (st.services[k] !== "active") svcOk = false
        r.push([svcOk ? "ok" : "warn", "Helpers", svcOk ? "running" : "needs repair"])
        return r
    }

    function refresh() { exec.connectSource(statusCmd) }
    function act(name) {
        busy = true
        exec.connectSource("python3 -I '" + script + "' action " + name)
    }

    P5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: function (source, data) {
            disconnectSource(source)
            if (source === root.statusCmd) {
                try { root.st = JSON.parse(data["stdout"]) } catch (e) { console.warn("tsvpn: bad status output", e) }
            } else {
                root.busy = false
                root.refresh()
            }
        }
    }

    Timer {
        interval: 5000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    compactRepresentation: MouseArea {
        onClicked: root.expanded = !root.expanded
        Kirigami.Icon {
            anchors.fill: parent
            source: root.iconName
            active: parent.containsMouse
        }
    }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 18
        Layout.preferredWidth: Kirigami.Units.gridUnit * 20
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            Kirigami.Icon {
                source: root.iconName
                Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                Layout.preferredHeight: Kirigami.Units.iconSizes.medium
            }
            PlasmaExtras.Heading {
                level: 4
                text: root.st ? root.st.summary : "Checking…"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        GridLayout {
            columns: 3
            columnSpacing: Kirigami.Units.smallSpacing * 2
            rowSpacing: 2
            Layout.fillWidth: true
            Repeater {
                model: root.rows().length * 3
                delegate: PlasmaComponents3.Label {
                    readonly property var row: root.rows()[Math.floor(index / 3)]
                    readonly property int col: index % 3
                    text: col === 0 ? "●" : row[col]
                    color: col === 0 ? root.colorFor(row[0]) : Kirigami.Theme.textColor
                    opacity: col === 1 ? 0.75 : 1
                    elide: col === 2 ? Text.ElideRight : Text.ElideNone
                    Layout.fillWidth: col === 2
                }
            }
        }

        PlasmaComponents3.Label {
            visible: root.st && root.st.problems.length > 0
            text: root.st ? root.st.problems.map(function (p) { return "• " + p }).join("\n") : ""
            color: root.colorFor(root.level)
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Item { Layout.fillHeight: true }

        RowLayout {
            Layout.fillWidth: true
            enabled: !root.busy
            PlasmaComponents3.Button {
                text: root.st && root.st.vpn.connected ? "Disconnect VPN" : "Connect VPN"
                onClicked: root.act(root.st && root.st.vpn.connected ? "vpn-disconnect" : "vpn-connect")
            }
            PlasmaComponents3.Button {
                text: root.st && root.st.tailscale.state === "Running" ? "Tailscale off" : "Tailscale on"
                onClicked: root.act(root.st && root.st.tailscale.state === "Running" ? "ts-down" : "ts-up")
            }
            Item { Layout.fillWidth: true }
            PlasmaComponents3.ToolButton {
                icon.name: "tools-wizard"
                PlasmaComponents3.ToolTip.text: "Repair helper services (asks for your password)"
                PlasmaComponents3.ToolTip.visible: hovered
                onClicked: root.act("repair")
            }
            PlasmaComponents3.ToolButton {
                icon.name: "view-refresh"
                PlasmaComponents3.ToolTip.text: "Refresh now"
                PlasmaComponents3.ToolTip.visible: hovered
                onClicked: root.refresh()
            }
        }
    }
}
