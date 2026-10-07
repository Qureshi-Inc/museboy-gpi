#!/usr/bin/env bash
# MuseBoy runtime build recipe (DOCUMENTATION — not yet verified end to end).
#
# The committed binaries in runtime/linux-aarch64/ (llama-server, whisper-cli)
# were built for AArch64 Linux against musl libc from:
#   llama.cpp  commit 8216c84 (0.5.0-dev)
#   whisper.cpp commit 60c0be6
# (see THIRD_PARTY.md).
#
# This script records the INTENDED build procedure so the blobs are not magic.
# TODO-verify: run this on a clean aarch64-musl build host, then compare the
# resulting binaries' SHA-256 against the committed ones. Until the hashes
# match, this recipe must not be treated as the canonical source of truth.
#
#   llama.cpp:
#     TODO-verify: exact cmake flags. Intended outline:
#       git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
#       git checkout 8216c84
#       cmake -B build -DCMAKE_C_COMPILER=<aarch64-linux-musl-gcc> \
#                      -DCMAKE_CXX_COMPILER=<aarch64-linux-musl-g++> \
#                      -DCMAKE_BUILD_TYPE=Release \
#                      -DBUILD_SHARED_LIBS=OFF \
#                      -DLLAMA_STATIC=ON
#       cmake --build build --config Release --target llama-server
#     TODO-verify: whether upstream 8216c84 exposes the llama-server target
#     by default or needs -DLLAMA_BUILD_SERVER=ON (flag name varies by era).
#     TODO-verify: static-link flags and any extra -D options needed so the
#     binary runs on a Pi OS host without glibc.
#
#   whisper.cpp:
#     TODO-verify: exact cmake flags. Intended outline:
#       git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
#       git checkout 60c0be6
#       cmake -B build -DCMAKE_C_COMPILER=<aarch64-linux-musl-gcc> \
#                      -DCMAKE_CXX_COMPILER=<aarch64-linux-musl-g++> \
#                      -DCMAKE_BUILD_TYPE=Release \
#                      -DBUILD_SHARED_LIBS=OFF
#       cmake --build build --config Release --target whisper-cli
#     TODO-verify: target name (whisper-cli vs main) at commit 60c0be6.
#     TODO-verify: which whisper.cpp options produce a fully static binary.
#
# Verification step (run after building):
#   sha256sum runtime/linux-aarch64/llama-server <newly-built-llama-server>
#   sha256sum runtime/linux-aarch64/whisper-cli  <newly-built-whisper-cli>
# Exact match is not guaranteed (build metadata, timestamps) — a mismatch
# means this recipe still needs work, not that the committed blobs are bad.
set -euo pipefail
echo "build-runtime.sh is documentation only. It performs no build."
echo "See the comments inside for the intended recipe and TODO-verify items."
exit 0
