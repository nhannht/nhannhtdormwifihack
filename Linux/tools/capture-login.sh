#!/bin/sh
# Record one sign-in, so the request the portal's button sends can be replayed.
#
# Some hotspots authorize a client by having the browser POST something to the
# router when someone presses a button. That request is the whole protocol: once
# it is known the app can send it by itself, every fifteen minutes, with no
# browser and no button. It cannot be guessed at, because the page that carries
# the button is served only to a client the router considers captive, and the
# splash page is a stub with no button in it at all.
#
# So: run this, sign in once by hand, stop it, and keep the file.
#
#   sudo Linux/tools/capture-login.sh          # writes /tmp/dormwifi-login.pcap
#
# Then the requests can be read back with:
#
#   tcpdump -r /tmp/dormwifi-login.pcap -A 'tcp port 80' | less
#
# Nothing leaves this machine. Port 443 traffic is recorded but stays encrypted;
# the login itself goes to the router over plain HTTP, which is the part that
# matters.

set -eu

out=${1:-/tmp/dormwifi-login.pcap}

if [ "$(id -u)" -ne 0 ]; then
    echo "This needs root to open the interface: sudo $0 [output-file]" >&2
    exit 1
fi

if ! command -v tcpdump >/dev/null 2>&1; then
    echo "tcpdump is not installed: sudo apt install tcpdump" >&2
    exit 1
fi

interface=$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2 == "wifi" { print $1; exit }')
if [ -z "${interface:-}" ]; then
    echo "No Wi-Fi interface found." >&2
    exit 1
fi

echo "Recording on $interface to $out"
echo
echo "  1. Join the dorm WiFi."
echo "  2. Press the 'Sign into Wi-Fi Network' notification."
echo "  3. Press the login button in the window it opens, and wait for the page"
echo "     to say you are connected."
echo "  4. Come back here and press Control-C."
echo
echo "The recording stops itself after five minutes if you forget."

# Bounded in time, so a forgotten terminal does not sit there filling the disk.
timeout 300 tcpdump -i "$interface" -s 0 -w "$out" \
    'tcp port 80 or tcp port 443 or udp port 53' || true

size=$(wc -c < "$out" 2>/dev/null || echo 0)
echo
echo "Saved $out ($size bytes)."
echo "Read it back with:  sudo tcpdump -r $out -A 'tcp port 80' | less"
