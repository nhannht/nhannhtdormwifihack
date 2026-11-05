#!/bin/sh
# Put Dorm WiFi in the application grid, and optionally on the desktop.
#
# No root, nothing outside the home directory, and safe to run twice. Everything
# it writes is removed by --uninstall.
#
#   tools/install-launcher.sh              # application grid only
#   tools/install-launcher.sh --desktop    # ... and a shortcut on the Desktop
#   tools/install-launcher.sh --uninstall

set -eu

here=$(cd -- "$(dirname -- "$0")" && pwd)
root=$(dirname -- "$here")                      # .../Linux
launcher="$root/bin/dormwifi"

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
apps_dir="$data_home/applications"
icons_dir="$data_home/icons/hicolor/scalable/apps"
bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

entry="$apps_dir/nhannhtdormwifihack.desktop"
icon="$icons_dir/nhannhtdormwifihack.svg"
path_link="$bin_dir/dormwifi"
shortcut="$HOME/Desktop/Dorm WiFi.desktop"

with_desktop=no
uninstall=no

for argument in "$@"; do
    case "$argument" in
        --desktop) with_desktop=yes ;;
        --uninstall) uninstall=yes ;;
        -h|--help)
            sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $argument" >&2
            exit 2
            ;;
    esac
done

refresh_caches() {
    update-desktop-database "$apps_dir" >/dev/null 2>&1 || true
    # hicolor is the theme directory the icon was installed under.
    gtk-update-icon-cache -t -f "$(dirname "$(dirname "$icons_dir")")" >/dev/null 2>&1 || true
}

if [ "$uninstall" = yes ]; then
    removed=0
    for file in "$entry" "$icon" "$path_link" "$shortcut"; do
        if [ -e "$file" ]; then
            rm -f "$file"
            removed=$((removed + 1))
            echo "removed  $file"
        fi
    done
    refresh_caches
    [ "$removed" = 0 ] && echo "Nothing was installed."
    exit 0
fi

if [ ! -x "$launcher" ]; then
    echo "Cannot find an executable launcher at $launcher" >&2
    exit 1
fi

mkdir -p "$apps_dir" "$icons_dir" "$bin_dir"
install -m 0644 "$root/data/nhannhtdormwifihack.svg" "$icon"

# The terminal command, so `dormwifi` works everywhere and not only through this
# checkout's bin directory.
ln -sfn "$launcher" "$path_link"

# An absolute Exec, because a launcher does not necessarily inherit the shell's
# PATH. Moving this checkout means re-running this script.
cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Dorm WiFi
GenericName=Captive portal sign-in
Comment=Sign in to the dorm WiFi and keep the session alive
Exec=$launcher gui
TryExec=$launcher
Icon=nhannhtdormwifihack
Terminal=false
Categories=Network;
Keywords=wifi;captive;portal;hotspot;dorm;inet;
StartupNotify=true
StartupWMClass=dormwifi
EOF
chmod 0644 "$entry"

if command -v desktop-file-validate >/dev/null 2>&1; then
    if ! desktop-file-validate "$entry"; then
        echo "The desktop entry did not validate; the launcher may not appear." >&2
    fi
fi

if [ "$with_desktop" = yes ]; then
    mkdir -p "$(dirname "$shortcut")"
    cp "$entry" "$shortcut"
    chmod +x "$shortcut"
    # GNOME's desktop icon extension refuses to run an untrusted file, and says
    # so only by not launching it.
    gio set "$shortcut" metadata::trusted true >/dev/null 2>&1 || true
fi

refresh_caches

echo "Installed:"
echo "  launcher  $entry"
echo "  icon      $icon"
echo "  command   $path_link -> $launcher"
[ "$with_desktop" = yes ] && echo "  shortcut  $shortcut"
echo
echo "Open it from the application grid (press Super and type \"Dorm WiFi\"),"
echo "or run:  dormwifi gui"
