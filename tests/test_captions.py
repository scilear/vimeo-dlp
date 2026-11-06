from vimeo_dlp.captions import (
    Cue,
    format_clock,
    format_timestamp,
    parse_vtt,
    to_srt,
    to_text,
    to_text_with_timestamps,
    to_timestamped,
)

SAMPLE = """WEBVTT

NOTE this block must be ignored

STYLE
::cue { color: white }

1
00:00:00.720 --> 00:00:04.650
Welcome, everybody. This is Charles, and I'm

2
00:00:04.740 --> 00:00:09.300 align:start position:0%
<v Steve>very pleased today.</v> P&amp;L is up.

00:00:10.000 --> 00:00:11.500
Second sentence here. And a third one.
"""


def test_parse_vtt_skips_notes_and_styles():
    cues = parse_vtt(SAMPLE)
    assert len(cues) == 3
    assert cues[0].start == 0.72
    assert cues[1].end == 9.3


def test_parse_vtt_strips_tags_and_entities():
    cues = parse_vtt(SAMPLE)
    assert "<v" not in cues[1].text
    assert "Steve" not in cues[1].text          # markup (including the voice label) is dropped
    assert cues[1].text.startswith("very pleased")
    assert "P&L" in cues[1].text                # &amp; is unescaped


def test_parse_vtt_handles_missing_cue_numbers():
    cues = parse_vtt(SAMPLE)
    assert cues[2].text.startswith("Second sentence")


def test_format_timestamp_and_clock():
    assert format_timestamp(3661.5) == "01:01:01,500"
    assert format_timestamp(0) == "00:00:00,000"
    assert format_clock(3661.9) == "01:01:01"


def test_to_srt():
    srt = to_srt(parse_vtt(SAMPLE))
    lines = srt.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,720 --> 00:00:04,650"
    assert lines[2].startswith("Welcome")
    assert "\n\n2\n" in srt


def test_to_text_reflows_sentences():
    text = to_text(parse_vtt(SAMPLE), sentences_per_paragraph=2, width=200)
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    assert len(paragraphs) >= 2
    assert "\n" not in paragraphs[0]


def test_to_timestamped_has_one_line_per_cue():
    lines = to_timestamped(parse_vtt(SAMPLE)).strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("[00:00:00] ")


def test_to_text_with_timestamps_prefixes_each_paragraph():
    cues = [Cue(start=float(i), end=i + 0.5, text=f"Sentence number {i}.") for i in range(6)]
    text = to_text_with_timestamps(cues, sentences_per_paragraph=3, width=200)
    assert text is not None
    assert text.count("[00:00:0") == 2


def test_empty_input_is_safe():
    assert parse_vtt("") == []
    assert to_srt([]) == ""
    assert to_text([]) == ""
    assert to_text_with_timestamps([]) is None
