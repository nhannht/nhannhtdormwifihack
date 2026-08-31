#!/bin/bash
# Builds the menu bar app and wraps it in a .app bundle.
#
# SwiftPM only produces a bare executable. A bundle is what lets macOS launch it
# from Spotlight, keep it in login items, and treat it as one app rather than a
# stray process.
set -euo pipefail

cd "$(dirname "$0")"

NAME="Dorm WiFi"
APP="${1:-$HOME/Applications}/$NAME.app"

swift build -c release --product nhannhtdormwifihack-menubar

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp .build/release/nhannhtdormwifihack-menubar "$APP/Contents/MacOS/DormWiFi"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Dorm WiFi</string>
    <key>CFBundleDisplayName</key>
    <string>Dorm WiFi</string>
    <key>CFBundleIdentifier</key>
    <string>io.nhannht.dormwifi</string>
    <key>CFBundleExecutable</key>
    <string>DormWiFi</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <!-- Without this the process launches and stays alive but never gets a
         window server connection, so it cannot draw a status item or anything
         else. `lsappinfo` reports it as `!cgsConnection`. -->
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <!-- No dock icon and no menu bar of its own: it lives in the status bar. -->
    <key>LSUIElement</key>
    <true/>
    <!-- A captive portal speaks plain HTTP by definition: it must answer before
         you have any internet, so it cannot hold a certificate a public
         authority would vouch for. Without this, step 2 of the login is refused
         by App Transport Security and reads as the portal being broken. -->
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
    </dict>
</dict>
</plist>
PLIST

# Ad-hoc signature. Without one, macOS kills it on launch on Apple silicon.
codesign --force --sign - "$APP" >/dev/null 2>&1 || true

echo "Built $APP"
echo "Open it with:  open \"$APP\""
