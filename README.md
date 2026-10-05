# MuseBoy for the RetroFlag GPi Case 2

<div align="center">
  <img src="docs/images/museboy-hero.jpg" alt="A retro handheld showing MuseBoy's tiny explorer in a moonlit pixel world" width="100%">
  <br>
  <strong>A pocket-sized Muse gadget with local voice tools and a controller-first app hub.</strong>
  <br><br>
  <img alt="Raspberry Pi CM4" src="https://img.shields.io/badge/Raspberry_Pi-CM4-c51a4a?logo=raspberrypi&logoColor=white">
  <img alt="Display" src="https://img.shields.io/badge/display-640×480-26324a">
  <img alt="Muse" src="https://img.shields.io/badge/cloud-Muse-7857c5">
  <img alt="Local AI" src="https://img.shields.io/badge/voice%20and%20planning-local-168f84">
</div>

MuseBoy brings **Home**, **App Mart**, **App Builder**, and **Settings** to the GPi Case 2. Whisper transcribes voice locally, Gemma drafts the app plan locally, and Muse receives only the text plan after you approve it.

## Screenshots

These 640×480 previews are rendered from the project's Pygame screens. They are not photographs or captures from a connected GPi. App Mart is shown in its offline demo mode, and App Builder shows a sample plan.

| MuseBoy Home | App Builder |
|:---:|:---:|
| ![MuseBoy Home app grid](docs/images/home.png) | ![App Builder showing a scrollable plan](docs/images/app-builder.png) |

| App Mart | Settings |
|:---:|:---:|
| ![App Mart offline demo shelf](docs/images/app-mart.png) | ![Settings with Wi-Fi, Bluetooth, audio, cameras, and Local AI](docs/images/settings.png) |

| On-device model selection |
|:---:|
| ![Local AI settings showing the loaded Gemma model](docs/images/local-ai.png) |

The intended target is a Raspberry Pi Compute Module 4 with eMMC, 8 GB RAM, the RetroFlag GPi Case 2, and Raspberry Pi OS 64-bit Bookworm or a compatible Debian 12 installation with X11. The build and local inference are tuned for the 8 GB CM4. Smaller RAM configurations are not supported by the default Gemma service profile.

## What happens to your voice

App Builder records from the microphone selected in **Settings → Audio**. Whisper transcribes the recording on the GPi. Gemma drafts a full `plan.json` locally; Bolt shows the full contract for review and keeps a short 2–4 sentence preview in `status.json`. **A** approves and sends only that text plan to Muse. **X** records an added detail and regenerates the plan. **B** cancels. The recording is deleted after local transcription and is never included in a Muse request.

The contract covers each screen's normal, empty, loading, and error behavior; D-pad and A/B/X/Y/Start actions; exact data endpoints, parameters, timeouts, and offline behavior; scope; icon direction; and 3–5 acceptance checks. Missing or unverified data sources become blocking questions rather than invented URLs.

Muse publishes progress only when it reaches a real milestone. Bolt displays the reported stage, any percentage Muse explicitly reports, and the age of Muse's last status update. It never derives percentage from elapsed time. A status older than three minutes is labeled stale; the worker's milestone cadence is about two minutes, so age naturally grows between updates.

## First setup

1. Use Raspberry Pi Imager to install a current 64-bit Raspberry Pi OS Bookworm image to the **CM4 eMMC**. The eMMC must be exposed through the GPi's rear programming port using `rpiboot`; a microSD card does not replace the eMMC on an eMMC CM4. Set a named login, Wi-Fi, and SSH if desired. Do not put Wi-Fi passwords, SSH private keys, or Muse SDK tokens in this repository.
2. Boot the CM4 in the GPi and connect it to the network. From a terminal, clone this project and run the GPi display/power patch if it is not already installed:

   ```sh
   git clone https://github.com/moiz-qureshi/museboy-gpi.git
   cd museboy-gpi
   sudo ./scripts/enable-gpi2-display.sh
   sudo reboot
   ```

   This patch uses the CM4's DPI GPIO display timing and `gpio-shutdown` power-button overlay. It backs up `config.txt` before adding the include. The RetroFlag repository's own safe-shutdown installer is written for RetroPie/Recalbox/Batocera; do not pipe its RetroPie script into a stock Debian install. RetroFlag documents that its dock display choice is sampled at power-on. Hot docking is not guaranteed. HDMI switching still depends on the dock, cable, monitor, and CM4 output configuration; verify it on your hardware.
3. After reboot, install the four-app stack and local runtimes:

   ```sh
   cd ~/museboy-gpi
   sudo ./scripts/install.sh
   ```

   The installer downloads Whisper's tiny English weights and a Gemma 3 1B Q4 model after asking you to review and accept Google's Gemma terms. The model is about 0.8 GB. Its checksum is verified. The launcher and local model service are enabled at boot.
4. In MuseBoy, open **Settings → Bluetooth → Muse SDK token** and enter your own token from [gadgets.muse.ai/settings/sdk-tokens](https://gadgets.muse.ai/settings/sdk-tokens). This one-time SDK credential is separate from pairing. The Settings keyboard hides it while typing; the root-owned file is saved with mode `0600` at `/var/lib/musegadget/sdk_token` and is not logged or passed as a command-line argument.
5. In **Settings → Bluetooth → Pair this GPi with Muse**, then in the Muse phone app turn on Developer Mode and select **Settings → Devices → Add Device**. Choose the nearby `MuseGadget…` device. The SDK opens BLE pairing for ten minutes. No Tailscale account or QR scan is required for ordinary Muse pairing.

**Optional remote access:** Tailscale is off by default and is not needed to pair Muse. To add it during setup, run:

```sh
sudo ./scripts/install.sh --tailscale
```

That flag installs Tailscale and displays its sign-in QR code. Omitting the flag leaves Tailscale out of the installation.

## Using the four apps

- **MuseBoy Home:** D-pad moves through the grid; A opens an app; B or Start returns; Select returns Home from a running app.
- **App Mart:** browse/install community apps when its backend is configured; the included local demo shelf works offline.
- **App Builder:** press A to record a new idea, or X to choose an installed app to update. B stops a recording early. Review the full scrollable plan, A approves, X adds a detail, and B cancels.
- **Settings:** choose Wi-Fi and enter passwords with the D-pad keyboard; pair Bluetooth audio/controller devices; choose microphone and speaker; inspect USB cameras; enter/pair Muse; and review local model status.

In **Settings → Local AI**, the first row shows the model currently loaded by llama.cpp. GGUF files placed in `/opt/gpi/local-ai/models/` appear below it. Select a supported downloaded model to reload the planner. MuseBoy marks models over 2.5 GB as too large for its configured memory limit. Other GGUF models may vary in JSON-plan quality and speed; the default Gemma model is the one exercised by this project.

To add a model, copy its `.gguf` file into `/opt/gpi/local-ai/models/`, then set ownership and permissions:

```sh
sudo install -m 0644 your-model.gguf /opt/gpi/local-ai/models/
```

Restart the app or use **Settings → Local AI → Refresh models**. Only download models whose license and use terms you have reviewed.

## Engineering notes

- Local planner API binds to `127.0.0.1:8089`; it is not exposed to the network.
- The llama.cpp and whisper.cpp ARM64 runtime binaries are statically linked and included. Source commit IDs and notices are in [`THIRD_PARTY.md`](THIRD_PARTY.md).
- Model weights are downloaded separately and are not in Git. Gemma is subject to Google's terms. Whisper's tiny English model is MIT-licensed.
- Muse's Linux Device SDK is installed from its upstream installer at setup. Each owner supplies a personal SDK token. Muse commands run as the unprivileged `tendo` account without general sudo rights; the SDK can read and modify files available to that account. Review the official SDK security and token terms before pairing.
- Local model selection is limited to valid GGUF files no larger than 2.5 GB. The service reserves memory for the UI, audio, and operating system. Large context windows or bigger models may be slow or fail on a CM4.
- Use the GPi power switch and wait for shutdown to finish. The bundled GPIO overlay sends an orderly system power event; verify safe shutdown after installation.

## Tests

Run host-side planner and model inventory tests with:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q launcher common apps input
bash -n scripts/*.sh
```

On the CM4, check the local services with `systemctl status gpi-local-llm gpi-console gpi-input`. The local model test is also exercised by speaking a short idea in App Builder; confirm the transcript and full plan before pressing A. Then verify the approved request contains `plan.json` and no audio file.

## Upstream hardware references

- [RetroFlag GPi Case 2 scripts](https://github.com/RetroFlag/GPiCase2-Script) — their instructions warn to apply the display patch before their safe-shutdown installer and describe selecting the LCD or HDMI when powering on.
- [Muse Linux Device SDK](https://github.com/facebookincubator/muse-gadget-sdk/tree/main/linux) — SDK token, Bluetooth pairing, account permissions, and service operation.
- [Muse Gadget SDK token terms](https://gadgets.muse.ai/sdk-terms).
- [Tailscale CLI](https://tailscale.com/docs/reference/tailscale-cli) — optional `tailscale up --ssh --qr` sign-in.
