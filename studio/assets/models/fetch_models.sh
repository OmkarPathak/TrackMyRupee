#!/usr/bin/env bash
# Downloads the Kokoro TTS model files used by studio/render/tts_kokoro.py and verifies them.
set -euo pipefail
cd "$(dirname "$0")"
BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
get() { [ -f "$1" ] || curl -L --fail -o "$1" "$BASE/$1"; echo "$2  $1" | sha256sum -c -; }
get kokoro-v1.0.onnx 7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5
get voices-v1.0.bin bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d
