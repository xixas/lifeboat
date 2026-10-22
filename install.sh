#!/usr/bin/env bash
# User-level installer for Lifeboat. No root. Usage: ./install.sh [--uninstall]
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
share="${XDG_DATA_HOME:-$HOME/.local/share}"
lib="$share/lifeboat/lib"
bin="$HOME/.local/bin/lifeboat"
app_id=io.github.xixas.Lifeboat
desktop="$share/applications/$app_id.desktop"
dbus="$share/dbus-1/services/$app_id.service"
icon="$share/icons/hicolor/scalable/apps/$app_id.svg"

refresh_caches() {
  command -v update-desktop-database >/dev/null && update-desktop-database "$share/applications" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$share/icons/hicolor" 2>/dev/null || true
}

if [ "${1:-}" = --uninstall ]; then
  rm -rf "$lib"
  rm -f "$bin" "$desktop" "$dbus" "$icon"
  refresh_caches
  echo "Lifeboat removed. Your config (~/.config/lifeboat), state and systemd timers were left alone;"
  echo "remove the timers with 'lifeboat uninstall-timer' BEFORE uninstalling if you want them gone."
  exit 0
fi

die() { echo "error: $*" >&2; exit 1; }

command -v python3 >/dev/null || die "python3 not found"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "Python 3.11 or newer is required"
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null || die "PyGObject, GTK 4 and libadwaita are missing. Install them with:
  Fedora/Nobara: sudo dnf install python3-gobject gtk4 libadwaita
  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
  Arch:          sudo pacman -S python-gobject gtk4 libadwaita"

command -v rclone >/dev/null || die "rclone 1.60 or newer is required. Install it with:
  Fedora/Nobara: sudo dnf install rclone
  Debian/Ubuntu: sudo apt install rclone   (or https://rclone.org/install/ for a newer one)
  Arch:          sudo pacman -S rclone"
ver=$(rclone version | head -1 | sed -E 's/^rclone v?//')
IFS=. read -r maj min _ <<<"${ver%%-*}"
{ [ "${maj:-0}" -gt 1 ] || { [ "${maj:-0}" -eq 1 ] && [ "${min:-0}" -ge 60 ]; }; } 2>/dev/null \
  || die "rclone $ver is too old; 1.60 or newer is required (https://rclone.org/install/)"

mkdir -p "$lib" "$(dirname "$bin")" "$(dirname "$desktop")" "$(dirname "$dbus")" "$(dirname "$icon")"
rm -rf "$lib/lifeboat" "$lib/data"
cp -r "$here/lifeboat" "$lib/lifeboat"
find "$lib/lifeboat" -name __pycache__ -type d -prune -exec rm -rf {} +
mkdir -p "$lib/data"
cp "$here/data/config.example.toml" "$lib/data/"

cat >"$bin" <<LAUNCHER
#!/bin/sh
PYTHONPATH="$lib\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m lifeboat "\$@"
LAUNCHER
chmod +x "$bin"

sed "s|^Exec=lifeboat|Exec=$bin|" "$here/data/$app_id.desktop" >"$desktop"
sed "s|@BIN@|$bin|" "$here/data/$app_id.service.in" >"$dbus"
cp "$here/data/$app_id.svg" "$icon"
refresh_caches

echo "Installed. Make sure ~/.local/bin is on your PATH, then:"
echo "  lifeboat            open the app (it offers to create an example config)"
echo "  lifeboat install-timer   after you have edited ~/.config/lifeboat/config.toml"
