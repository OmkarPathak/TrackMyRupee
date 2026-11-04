# Kokoro TTS model files (not committed — too large for plain git)
`kokoro-v1.0.onnx` (~325 MB) and `voices-v1.0.bin` (~28 MB). Kokoro is Apache-2.0.
Run `bash studio/assets/models/fetch_models.sh` to download and checksum-verify them (from the kokoro-onnx GitHub release `model-files-v1.0`).
Used by `studio/render/tts_kokoro.py` (`pip install kokoro-onnx soundfile`).
