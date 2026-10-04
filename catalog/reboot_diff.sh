#!/usr/bin/env bash
# macOS only. Observe launchd jobs and listeners; write only local snapshots.
# Usage: reboot_diff.sh snapshot|diff|mines [remote-host]
# REBOOT_STATE_DIR controls snapshot storage; default state/reboot-diff.
set -euo pipefail
cmd=${1:-}; target=${2:-local}; state_dir=${REBOOT_STATE_DIR:-state/reboot-diff}
run() { if [ "$target" = local ]; then bash -c "$1"; else ssh -o LogLevel=ERROR "$target" "$1"; fi; }
labels='launchctl list | awk "\$1!=\"-\" && NR>1 {print \$3}" | grep -v -E "^(com\.apple|application\.)" | sort'
ports='lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk "NR>1{print \$1\" \"\$9}" | sort -u'
case "$cmd" in
 snapshot)
  out="$state_dir/$target-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$out"
  run "$labels" > "$out/labels.txt"
  run "$ports" > "$out/ports.txt"
  echo "Snapshot: $out";;
 diff)
  out=$(ls -dt "$state_dir/$target-"* 2>/dev/null | head -1)
  [ -n "$out" ] || { echo 'No snapshot'; exit 1; }
  scratch=$(mktemp -d "$state_dir/diff.XXXXXX")
  trap 'rm -rf "$scratch"' EXIT
  run "$labels" > "$scratch/labels.txt"
  run "$ports" > "$scratch/ports.txt"
  echo 'Missing jobs'; comm -23 "$out/labels.txt" "$scratch/labels.txt"
  echo 'Added jobs'; comm -13 "$out/labels.txt" "$scratch/labels.txt"
  echo 'Missing listeners'; comm -23 "$out/ports.txt" "$scratch/ports.txt"
  echo 'Added listeners'; comm -13 "$out/ports.txt" "$scratch/ports.txt";;
 mines)
  run 'domain=gui/$(id -u)
  disabled=$(launchctl print-disabled "$domain" | awk -F\" "/=> disabled/{print \$2}")
  running=$(launchctl list | awk "\$1!=\"-\"{print \$3}")
  for path in ~/Library/LaunchAgents/*.plist; do
    label=$(/usr/libexec/PlistBuddy -c "Print :Label" "$path" 2>/dev/null) || continue
    start=$(/usr/libexec/PlistBuddy -c "Print :RunAtLoad" "$path" 2>/dev/null)
    keep=$(/usr/libexec/PlistBuddy -c "Print :KeepAlive" "$path" 2>/dev/null | head -1)
    [ "$start" = true ] || [ -n "$keep" ] || continue
    echo "$disabled" | grep -qx "$label" && continue
    echo "$running" | grep -qx "$label" && continue
    interval=$(/usr/libexec/PlistBuddy -c "Print :StartInterval" "$path" 2>/dev/null)
    /usr/libexec/PlistBuddy -c "Print :StartCalendarInterval" "$path" >/dev/null 2>&1 && continue
    [ -n "$interval" ] && continue
    echo "Dormant auto-start job: $label"
  done
  for path in /Library/LaunchDaemons/*.plist /Library/LaunchAgents/*.plist; do
    [ -f "$path" ] || continue
    label=$(/usr/libexec/PlistBuddy -c "Print :Label" "$path" 2>/dev/null) || continue
    case "$label" in com.apple.*|com.microsoft.*|com.google.*|us.zoom.*|com.adobe.*) continue;; esac
    launchctl print-disabled system 2>/dev/null | grep -q "\"$label\" => disabled" && continue
    start=$(/usr/libexec/PlistBuddy -c "Print :RunAtLoad" "$path" 2>/dev/null)
    keep=$(/usr/libexec/PlistBuddy -c "Print :KeepAlive" "$path" 2>/dev/null | head -1)
    [ "$start" = true ] || [ -n "$keep" ] || continue
    program=$(/usr/libexec/PlistBuddy -c "Print :ProgramArguments:0" "$path" 2>/dev/null || /usr/libexec/PlistBuddy -c "Print :Program" "$path" 2>/dev/null)
    if pgrep -f "^$program" >/dev/null; then status=running; else status=stopped; fi
    echo "System auto-start job: $label ($status)"
  done';;
 *) echo 'Usage: reboot_diff.sh snapshot|diff|mines [remote-host]'; exit 2;;
esac
