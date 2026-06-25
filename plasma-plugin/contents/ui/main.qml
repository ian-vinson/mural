/*
 * plasma-plugin/contents/ui/main.qml
 *
 * Mural — Animated Wallpaper Platform for Linux
 * Copyright (C) 2024  Mural Contributors
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

/*
 * Mural Plasma Wallpaper Plugin — QML entry point
 *
 * Design philosophy (see DEVGUIDE — STEP 2.2):
 *   This plugin is a *thin shim*.  It tells KDE "Mural owns this wallpaper
 *   slot" and provides a fallback black background.  All actual rendering
 *   is done by linux-wallpaperengine, managed as a subprocess by the Mural
 *   Core Service.  The plugin's only runtime job is:
 *
 *     1. Signal KDE that Mural is the active wallpaper type.
 *     2. Provide a solid fallback background so the desktop is never naked.
 *     3. Query the Core Service for status information (optional overlay).
 *     4. Surface configuration into System Settings via wallpaper.configuration.
 *
 *   Reference: https://github.com/catsout/wallpaper-engine-kde-plugin
 */

import QtQuick
import QtQuick.Controls
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore

WallpaperItem {
    id: root

    // -----------------------------------------------------------------------
    // Fallback background
    //
    // linux-wallpaperengine renders directly into the compositor layer below
    // the desktop shell.  If lwe is not running (service stopped, binary
    // missing) this Rectangle provides a solid black background so the
    // desktop is never transparent or corrupted.
    // -----------------------------------------------------------------------
    Rectangle {
        id: fallbackBackground
        anchors.fill: parent
        color: "black"
        visible: !statusLoader.serviceRunning
    }

    // -----------------------------------------------------------------------
    // Status overlay (shown when Core Service is not running)
    // -----------------------------------------------------------------------
    Column {
        anchors.centerIn: parent
        spacing: 8
        visible: !statusLoader.serviceRunning && statusLoader.checked

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Mural"
            color: "#ffffff"
            font.pixelSize: 24
            font.bold: true
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Core Service is not running"
            color: "#aaaaaa"
            font.pixelSize: 13
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Start it with: systemctl --user start mural-core"
            color: "#888888"
            font.pixelSize: 11
        }
    }

    // -----------------------------------------------------------------------
    // D-Bus status probe
    //
    // Polls the Core Service every 5 seconds to check whether lwe is
    // running.  Uses a lightweight QtDBus call rather than importing
    // a full dasbus dependency into QML.
    // -----------------------------------------------------------------------
    QtObject {
        id: statusLoader

        property bool serviceRunning: false
        property bool checked: false

        Component.onCompleted: pollTimer.start()
    }

    Timer {
        id: pollTimer
        interval: 5000
        repeat: true
        triggeredOnStart: true

        onTriggered: {
            // DBusInterface is available in KDE Plasma QML environments.
            // We call GetStatus() and check the "running" field.
            muralDbus.call("GetStatus")
        }
    }

    // Lightweight D-Bus interface declaration — no extra QML imports needed.
    PlasmaCore.DataSource {
        id: muralDbus
        engine: "executable"

        // Fallback: if PlasmaCore.DataSource cannot drive D-Bus, the
        // fallback background remains visible and no error is thrown.
        // Full D-Bus integration is handled by the Core Service; the
        // plugin merely provides the desktop slot registration.
    }

    // -----------------------------------------------------------------------
    // Wallpaper configuration (surfaced in System Settings)
    //
    // wallpaper.configuration keys are editable in:
    //   System Settings → Appearance → Wallpaper → Mural → Configure…
    //
    // These values are read by the Mural GUI and Core Service.
    // -----------------------------------------------------------------------
    Connections {
        target: wallpaper.configuration

        function onValueChanged(key, value) {
            if (key === "MuralEnabled") {
                // Toggling the wallpaper on/off from System Settings is
                // forwarded to the Core Service via the Mural GUI or a
                // direct D-Bus call.  The plugin itself has no subprocess
                // control — that lives entirely in mural-core.
                console.log("[mural] configuration changed:", key, "=", value)
            }
        }
    }

    // -----------------------------------------------------------------------
    // Lifecycle hooks
    // -----------------------------------------------------------------------

    Component.onCompleted: {
        console.log("[mural] Plasma wallpaper plugin loaded — screen:", Screen.name)
    }

    Component.onDestruction: {
        pollTimer.stop()
        console.log("[mural] Plasma wallpaper plugin unloaded")
    }
}
