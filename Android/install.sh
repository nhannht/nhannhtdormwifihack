#!/bin/bash
#
# Installs the app on the attached phone and opens it.
#
# The settings this hotspot needs are written on the first install, so the app
# starts out pointed at the right network and portal instead of at six empty fields.

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

APK=.build/dormwifi.apk
PACKAGE=vn.io.nhannht.dormwifihack

[ -f "$APK" ] || { printf 'No apk yet. Run ./build.sh first.\n' >&2; exit 1; }
command -v adb >/dev/null 2>&1 || { printf 'adb is not on the PATH.\n' >&2; exit 1; }

DEVICES=$(adb devices | awk 'NR>1 && $2=="device"' | wc -l)
if [ "$DEVICES" -eq 0 ]; then
    printf 'No phone is attached. Plug it in, unlock it, and accept the debugging prompt.\n' >&2
    exit 1
fi

# -g grants the runtime permissions this app asks for, so the first launch does not
# have to interrupt itself with a notification prompt.
printf 'installing\n'
adb install -r -g "$APK"

printf 'opening\n'
adb shell am start -n "$PACKAGE/.MainActivity" >/dev/null

printf '\ninstalled. The screen should be showing the status.\n'
printf 'To watch it from here:  adb logcat -s DormWiFi\n'
