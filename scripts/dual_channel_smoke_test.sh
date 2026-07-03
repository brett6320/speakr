#!/usr/bin/env bash
# Build a 2-channel test recording for the dual-channel (stereo call) feature:
# LEFT channel = caller, RIGHT channel = callee. Upload the result with
# "Dual-channel (stereo call)" enabled and confirm the transcript attributes
# left-channel speech to the first participant (caller) and right-channel speech
# to the second (callee).
#
# Usage:
#   scripts/dual_channel_smoke_test.sh CALLER_AUDIO CALLEE_AUDIO [OUTPUT.wav]
set -euo pipefail

caller="${1:-}"
callee="${2:-}"
out="${3:-stereo_call_test.wav}"

if [[ -z "$caller" || -z "$callee" ]]; then
  cat <<'USAGE'
Usage: scripts/dual_channel_smoke_test.sh CALLER_AUDIO CALLEE_AUDIO [OUTPUT.wav]

  CALLER_AUDIO  speech clip placed on the LEFT channel  (maps to participant #1)
  CALLEE_AUDIO  speech clip placed on the RIGHT channel (maps to participant #2)
  OUTPUT        output stereo file (default: stereo_call_test.wav)

Use two short clips of clearly different speech (ideally different voices).
USAGE
  exit 1
fi

command -v ffmpeg >/dev/null || { echo "ffmpeg not found on PATH" >&2; exit 1; }

# Downmix each input to mono, then join caller -> left / callee -> right into a
# true stereo file (no mixing between channels).
ffmpeg -hide_banner -loglevel error -y \
  -i "$caller" -i "$callee" \
  -filter_complex "[0:a]aformat=channel_layouts=mono[l];[1:a]aformat=channel_layouts=mono[r];[l][r]join=inputs=2:channel_layout=stereo[a]" \
  -map "[a]" "$out"

channels=$(ffprobe -hide_banner -loglevel error -select_streams a:0 \
  -show_entries stream=channels -of default=nk=1:nw=1 "$out" | head -1)
echo "Wrote stereo test file: $out (channels: ${channels:-unknown})"

cat <<EOF

Next:
  1. Upload "$out" in Speakr.
  2. Set participants to two names (first = caller/left, second = callee/right).
  3. Enable "Dual-channel (stereo call)" in the upload advanced options.
  4. After transcription, confirm left-channel speech is attributed to the first
     participant and right-channel speech to the second.
EOF
