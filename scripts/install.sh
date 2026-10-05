#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENABLE_TAILSCALE=0
for arg in "$@"; do
  case "$arg" in
    --tailscale) ENABLE_TAILSCALE=1 ;;
    -h|--help) echo "Usage: sudo $0 [--tailscale]"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[[ $(id -u) == 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo "MuseBoy currently targets 64-bit ARM (CM4)." >&2; exit 1; }
. /etc/os-release
[[ ${ID:-} == debian || ${ID:-} == raspbian ]] || {
  echo "Use Raspberry Pi OS or Debian 12+ on the CM4." >&2; exit 1;
}

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  bluez curl ca-certificates git sudo network-manager python3 python3-evdev python3-pygame \
  python3-requests python3-pil python3-numpy xinit xserver-xorg \
  x11-xserver-utils xdotool pulseaudio pulseaudio-utils alsa-utils \
  v4l-utils

getent group tendo >/dev/null || groupadd --system tendo
if ! id tendo >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --gid tendo tendo
fi
for group in input audio video render gpio; do
  getent group "$group" >/dev/null && usermod -aG "$group" tendo || true
done
install -d -o root -g tendo -m 2775 /opt/gpi/{apps,common,launcher,input}
install -d -o root -g tendo -m 2775 /opt/gpi/apps/{appmart,builder,settings}
install -d -o root -g tendo -m 2775 /var/lib/gpi-builder/{requests,builds,.tmp}
install -d -o root -g tendo -m 2775 /opt/gpi/local-ai/{bin,models}

install -m 0644 "$ROOT/common/gpi_ui.py" "$ROOT/common/bolt.py" /opt/gpi/common/
install -m 0644 "$ROOT/common/local_models.py" /opt/gpi/common/
install -m 0755 "$ROOT/launcher/launcher.py" /opt/gpi/launcher/
install -m 0644 "$ROOT/launcher/gpi-console.service" /etc/systemd/system/
install -m 0755 "$ROOT/launcher/session.sh" /opt/gpi/launcher/
install -m 0755 "$ROOT/input/gpi-input.py" /opt/gpi/input/
install -m 0644 "$ROOT/input/gpi-input.service" /etc/systemd/system/

for app in appmart builder settings; do
  install -m 0644 "$ROOT/apps/$app/app.json" "$ROOT/apps/$app/icon.png" \
    "/opt/gpi/apps/$app/"
  install -m 0755 "$ROOT/apps/$app/run.sh" "/opt/gpi/apps/$app/"
done
install -m 0755 "$ROOT/apps/appmart/main.py" /opt/gpi/apps/appmart/
cp -a "$ROOT/apps/appmart/demo" /opt/gpi/apps/appmart/
install -m 0755 "$ROOT/apps/builder/builder.py" "$ROOT/apps/builder/local_plan.py" \
  /opt/gpi/apps/builder/
install -m 0644 "$ROOT/docs/MUSE-HANDOFF.md" \
  /opt/gpi/apps/builder/MUSE-HANDOFF.md
install -m 0644 "$ROOT/.agents/skills/museboy-app-builder/SKILL.md" \
  /opt/gpi/apps/builder/SKILL.md
install -m 0755 "$ROOT/scripts/gpi-muse-skill-onboard" /usr/local/sbin/
install -m 0644 "$ROOT/scripts/gpi-muse-skill-onboard.service" \
  "$ROOT/scripts/gpi-muse-skill-onboard.path" /etc/systemd/system/
install -m 0755 "$ROOT/apps/settings/main.py" /opt/gpi/apps/settings/

install -m 0755 "$ROOT/runtime/linux-aarch64/llama-server" \
  "$ROOT/runtime/linux-aarch64/whisper-cli" /opt/gpi/local-ai/bin/
install -m 0644 "$ROOT/scripts/gpi-local-llm.service" /etc/systemd/system/
install -m 0755 "$ROOT/scripts/gpi-model-select" /usr/local/sbin/
install -m 0755 "$ROOT/scripts/gpi-muse-token" \
  "$ROOT/scripts/gpi-muse-token-status" "$ROOT/scripts/gpi-muse-pair" \
  /usr/local/sbin/
install -m 0644 "$ROOT/config/90-gpi-settings" /etc/sudoers.d/90-gpi-settings
chmod 0440 /etc/sudoers.d/90-gpi-settings
visudo -cf /etc/sudoers.d/90-gpi-settings >/dev/null

install -d -m 0755 /etc/gpi/local-ai
if [[ ! -s /etc/gpi/local-ai/model.env ]]; then
  cat > /etc/gpi/local-ai/model.env <<'EOF'
MODEL_PATH=/opt/gpi/local-ai/models/gemma-3-1b-it-Q4_K_M.gguf
MODEL_ALIAS=gemma-3-1b-it-Q4_K_M
EOF
  chmod 0644 /etc/gpi/local-ai/model.env
fi
if ! id gpi-ai >/dev/null 2>&1; then
  useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin gpi-ai
fi
usermod -aG tendo gpi-ai

fetch_checked() {
  local url="$1" output="$2" expected="$3" temporary="$2.part"
  if [[ -s "$output" ]] && echo "$expected  $output" | sha256sum -c - >/dev/null 2>&1; then
    return
  fi
  rm -f "$temporary"
  curl -fL --retry 3 --connect-timeout 15 "$url" -o "$temporary"
  echo "$expected  $temporary" | sha256sum -c -
  mv "$temporary" "$output"
  chown root:tendo "$output"
  chmod 0644 "$output"
}
gemma_model=/opt/gpi/local-ai/models/gemma-3-1b-it-Q4_K_M.gguf
gemma_hash=8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135
if ! echo "$gemma_hash  $gemma_model" | sha256sum -c - >/dev/null 2>&1; then
  echo "Gemma 3 1B is governed by Google's Gemma Terms and Prohibited Use Policy."
  echo "Review https://ai.google.dev/gemma/terms before downloading the model."
  [[ -r /dev/tty ]] || { echo "Run interactively, or preinstall the reviewed model." >&2; exit 1; }
  read -r -p "Do you accept those terms for your own use? [y/N] " accept_gemma </dev/tty
  [[ "$accept_gemma" == y || "$accept_gemma" == Y ]] || {
    echo "Model download cancelled. Install with Gemma terms accepted to use local planning." >&2; exit 1;
  }
fi
fetch_checked \
  https://huggingface.co/ggml-org/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf \
  "$gemma_model" \
  "$gemma_hash"
fetch_checked \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin \
  /opt/gpi/local-ai/models/ggml-tiny.en.bin \
  921e4cf868f6dd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f

chown -R root:tendo /opt/gpi/local-ai
chown -R root:tendo /opt/gpi/apps
chmod 2775 /opt/gpi/apps /opt/gpi/apps/{appmart,builder,settings}
chmod 2755 /opt/gpi/local-ai /opt/gpi/local-ai/{bin,models}
chmod 0644 /opt/gpi/local-ai/models/*
chmod 0755 /opt/gpi/local-ai/bin/*
install -d -o root -g root -m 0755 /var/lib/gpi-builder
chown -R root:tendo /var/lib/gpi-builder
chmod 2775 /var/lib/gpi-builder /var/lib/gpi-builder/{requests,builds,.tmp}

# Install Muse's maintained Linux SDK without a shared token and without
# opening pairing yet. The owner enters their own SDK token in Settings.
if ! command -v musegadget >/dev/null 2>&1; then
  sdk_tmp=$(mktemp)
  trap 'rm -f "$sdk_tmp"' EXIT
  curl -fsSL https://raw.githubusercontent.com/facebookincubator/muse-gadget-sdk/main/linux/install.sh -o "$sdk_tmp"
  bash "$sdk_tmp" --run-as tendo --yes --no-pair
  rm -f "$sdk_tmp"
  trap - EXIT
fi
systemctl enable --now NetworkManager.service

if [[ "$ENABLE_TAILSCALE" == 1 ]]; then
  curl -fsSL https://tailscale.com/install.sh | sh
  echo "Tailscale is optional. Its sign-in QR code will appear in this terminal."
  tailscale up --ssh --qr
fi

systemctl daemon-reload
systemctl enable gpi-input.service gpi-console.service gpi-local-llm.service
systemctl restart gpi-input.service
systemctl restart gpi-local-llm.service
systemctl restart gpi-console.service
systemctl enable --now gpi-muse-skill-onboard.path
echo "MuseBoy installed. In Settings > Bluetooth, enter your personal Muse SDK token and open BLE pairing."
