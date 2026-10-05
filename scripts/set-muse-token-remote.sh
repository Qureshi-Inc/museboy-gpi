#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[a-zA-Z0-9_.@:-]+$ || "$1" == -* ]]; then
  echo "Usage: $0 ssh-user@gpi-host" >&2
  exit 2
fi
host=$1
tmp=$(mktemp)
remote_name="muse-token-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
remote_path="/tmp/$remote_name"
uploaded=0
cleanup() {
  rm -f "$tmp"
  if [[ $uploaded -eq 1 ]]; then
    ssh -T "$host" "rm -f '$remote_path'" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT HUP INT TERM
chmod 600 "$tmp"

printf 'Muse SDK token from gadgets.muse.ai/settings/sdk-tokens: '
IFS= read -r -s token
printf '\n'
if [[ ! "$token" =~ ^mgst_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$ ]]; then
  unset token
  echo "That does not match the expected Muse SDK token format." >&2
  exit 2
fi
printf '%s\n' "$token" > "$tmp"
unset token

echo "Copying the token through encrypted SSH (input and temporary files are protected)…"
scp -p "$tmp" "$host:$remote_path"
uploaded=1
ssh -t "$host" "sudo /usr/local/sbin/gpi-muse-token < '$remote_path'; result=\$?; rm -f '$remote_path'; exit \$result"
uploaded=0
echo "Muse SDK token installed. Continue with Bluetooth pairing on the GPi and in the Muse app."
