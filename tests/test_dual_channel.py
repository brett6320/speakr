"""Tests for dual-channel (stereo call) transcription.

Covers the three pieces unique to the feature:
- ``_dual_channel_labels``  — participants -> caller/callee label mapping + fallbacks.
- ``_merge_dual_channel``   — per-channel responses merged, timestamp-ordered,
                              speaker forced to the channel's label.
- ``split_stereo_channels`` — a real ffmpeg split of a *synthesized* stereo file
                              into two mono streams (skipped when ffmpeg/ffprobe
                              are unavailable).
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.app import app
import src.tasks.processing as proc
from src.services.transcription import TranscriptionResponse, TranscriptionSegment
from src.utils.ffmpeg_utils import split_stereo_channels
from src.utils.ffprobe import get_codec_info


# ---------------------------------------------------------------------------
# _dual_channel_labels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("participants,expected", [
    ("Alice, Bob", ("Alice", "Bob")),          # first=caller, second=callee
    ("Alice, Bob, Carol", ("Alice", "Bob")),   # only the first two are used
    ("  Alice ,  Bob  ", ("Alice", "Bob")),    # surrounding whitespace trimmed
    ("Alice,,Bob", ("Alice", "Bob")),          # empty middle entry dropped
    ("Alice", ("Alice", "Callee")),            # one name -> caller known, callee generic
    ("", ("Caller", "Callee")),                # not set -> both generic
    (None, ("Caller", "Callee")),              # missing -> both generic
    ("Alice, Alice", ("Caller", "Callee")),    # identical -> generic (avoid ambiguity)
])
def test_dual_channel_labels(participants, expected):
    assert proc._dual_channel_labels(participants) == expected


# ---------------------------------------------------------------------------
# _merge_dual_channel
# ---------------------------------------------------------------------------

def _seg(text, start, end=None, speaker=None):
    return TranscriptionSegment(text=text, speaker=speaker, start_time=start, end_time=end)


def _resp(segs=None, text="", **kw):
    return TranscriptionResponse(text=text, segments=segs, **kw)


def test_merge_orders_by_time_and_forces_labels():
    left = _resp(
        [_seg("hi", 0.0, 1.0, "SPEAKER_00"), _seg("still me", 2.0, 3.0, "SPEAKER_00")],
        text="hi still me",
    )
    right = _resp([_seg("hello", 1.0, 1.5, "SPEAKER_00")], text="hello")

    merged = proc._merge_dual_channel(left, right, "Alice", "Bob")

    # Segments interleave chronologically across the two channels ...
    assert [s.start_time for s in merged.segments] == [0.0, 1.0, 2.0]
    # ... and each segment's speaker is forced to its channel's label,
    # regardless of what the connector returned.
    assert [s.speaker for s in merged.segments] == ["Alice", "Bob", "Alice"]
    assert [s.text for s in merged.segments] == ["hi", "hello", "still me"]
    assert merged.speakers == ["Alice", "Bob"]
    assert merged.text == "Alice: hi\nBob: hello\nAlice: still me"


def test_merge_plaintext_channel_becomes_single_segment():
    # A channel that returns only plain text (no diarized segments) must not be
    # lost — it becomes one segment at t=0 with the channel label.
    left = _resp(segs=None, text="left only words", duration=5.0)
    right = _resp([_seg("hey", 0.5, 1.0, "X")], text="hey")

    merged = proc._merge_dual_channel(left, right, "Caller", "Callee")

    caller_segs = [s for s in merged.segments if s.speaker == "Caller"]
    assert len(caller_segs) == 1
    assert caller_segs[0].text == "left only words"
    assert caller_segs[0].start_time == 0.0
    # The synthesized segment (t=0.0) precedes the right channel's t=0.5.
    assert merged.segments[0].speaker == "Caller"
    assert merged.segments[1].speaker == "Callee"


def test_merge_handles_empty_channel():
    left = _resp([_seg("solo", 0.0, 1.0)], text="solo")
    right = _resp(segs=None, text="")  # nothing at all on this channel

    merged = proc._merge_dual_channel(left, right, "A", "B")

    assert [s.speaker for s in merged.segments] == ["A"]
    assert merged.text == "A: solo"


# ---------------------------------------------------------------------------
# split_stereo_channels — real ffmpeg on a synthesized stereo file
# ---------------------------------------------------------------------------

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


@pytest.mark.skipif(not (_FFMPEG and _FFPROBE), reason="ffmpeg/ffprobe not installed")
def test_split_stereo_channels_produces_two_mono_files(tmp_path):
    stereo = str(tmp_path / "stereo.wav")
    # Synthesize a genuine 2-channel file: 440 Hz on the left, 880 Hz on the right.
    subprocess.run(
        [
            _FFMPEG, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
            "-filter_complex", "[0:a][1:a]amerge=inputs=2",
            "-ac", "2", "-y", stereo,
        ],
        check=True,
    )

    with app.app_context():
        # Sanity: the source really is stereo.
        assert get_codec_info(stereo, timeout=30).get("channels") == 2

        left, right = split_stereo_channels(stereo)
        try:
            for path in (left, right):
                assert os.path.exists(path) and os.path.getsize(path) > 0
                # Each split output is a single-channel (mono) stream.
                assert get_codec_info(path, timeout=30).get("channels") == 1
        finally:
            for path in (left, right):
                if os.path.exists(path):
                    os.remove(path)
