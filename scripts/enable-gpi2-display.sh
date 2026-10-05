#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ $(id -u) == 0 ]] || { echo "Run with sudo." >&2; exit 1; }
boot=/boot/firmware
config="$boot/config.txt"
[[ -f "$config" ]] || { echo "Expected Raspberry Pi OS at /boot/firmware/config.txt" >&2; exit 1; }
if grep -q 'include muse-gpi.txt' "$config" || \
   grep -qE '^[[:space:]]*dtoverlay=(vc4-kms-dpi-generic|gpio-shutdown)' "$config" 2>/dev/null || \
   [[ -e "$boot/muse-gpi.txt" ]]; then
  echo "A GPi display fragment already exists; inspect $config before continuing." >&2
  exit 2
fi
cp -n "$config" "$config.museboy.bak"
fragment=$(mktemp)
cp "$ROOT/config/muse-gpi.txt" "$fragment"
if grep -qE '^[[:space:]]*dtoverlay=vc4-kms-v3d([[:space:]]|$)' "$config"; then
  sed -i '/^[[:space:]]*dtoverlay=vc4-kms-v3d[[:space:]]*$/d' "$fragment"
fi
install -m 0644 "$fragment" "$boot/muse-gpi.txt"
rm -f "$fragment"
printf '\n# Added by MuseBoy GPi installer\ninclude muse-gpi.txt\n' >> "$config"
echo "GPi Case 2 DPI and orderly power-button patch installed. Reboot to apply."
echo "Dock HDMI switching depends on the dock/firmware and is not guaranteed by this patch."
