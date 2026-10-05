# Third-party components and model notices

| Component | Use | License / notice |
|---|---|---|
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | ARM64 local LLM server binary; source commit `8216c84` (0.5.0-dev) | MIT; see upstream `LICENSE` |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | ARM64 local speech transcription binary; source commit `60c0be6` | MIT; see upstream `LICENSE` |
| [Whisper tiny.en weights](https://huggingface.co/ggerganov/whisper.cpp) | Downloaded by the installer, never committed | MIT; installer verifies SHA-256 `921e4cf868f6dd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f` |
| [Gemma 3 1B IT Q4_K_M](https://huggingface.co/ggml-org/gemma-3-1b-it-GGUF) | Downloaded by the installer, never committed | Google's Gemma Terms and Prohibited Use Policy apply; review and accept them before download |
| [Muse Linux Device SDK](https://github.com/facebookincubator/muse-gadget-sdk) | Installed from upstream during setup; not copied into this repository | Apache-2.0 for SDK code; separate personal SDK token terms apply |

The packaged ARM64 executables were built from the source commits above for AArch64 Linux with musl libc. The package contains no Muse SDK token, Wi-Fi credential, SSH key, user conversation, or model weights.
