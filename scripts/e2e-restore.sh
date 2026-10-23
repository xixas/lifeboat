#!/usr/bin/env bash
# End-to-end check of an installed Lifeboat: install.sh into a throwaway HOME, then back up, restore and compare
# through the CLI, including an old version. Uses a local rclone remote only; never touches your real HOME.
# Usage: scripts/e2e-restore.sh   (needs rclone, python3 with PyGObject + GTK 4 + libadwaita)
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
export HOME
HOME=$(mktemp -d)
trap 'rm -rf "$HOME"' EXIT
unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME
export RCLONE_CONFIG="$HOME/no-rclone.conf" RCLONE_CONFIG_LOCALDRV_TYPE=local

step() { echo "== $*"; }
step install
"$repo/install.sh" >/dev/null
lb="$HOME/.local/bin/lifeboat"
"$lb" --version

step "config with one job"
mkdir -p ~/src/sub
echo one > ~/src/a.txt; echo two > ~/src/sub/b.txt; echo gone-later > ~/src/c.txt
"$lb" init >/dev/null
"$lb" add-job docs '~/src' "localdrv:$HOME/remote/docs" >/dev/null
"$lb" remove-job documents >/dev/null
"$lb" config --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["ok"] and d["jobs"] == 1, d'

step "back up and restore"
"$lb" run --json >/dev/null 2>&1
"$lb" restore docs --to ~/restored >/dev/null 2>&1
diff -r ~/src ~/restored

step "old version keeps a deleted file"
rm ~/src/c.txt
sleep 1  # the next run needs a new timestamp
"$lb" run >/dev/null 2>&1
stamp=$("$lb" versions docs --json | python3 -c 'import json,sys; v=json.load(sys.stdin)["versions"]; assert v, v; print(v[0])')
"$lb" restore docs --to ~/old --version "$stamp" >/dev/null 2>&1
[ "$(cat ~/old/c.txt)" = gone-later ]

step "status"
"$lb" status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["last"]["ok"], d["last"]'
echo "e2e restore OK"
