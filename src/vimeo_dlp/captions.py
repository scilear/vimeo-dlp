"""WebVTT parsing and conversion to SRT / plain text / timestamped text."""

from __future__ import annotations

import html
import re
import textwrap
from dataclasses import dataclass
from typing import Iterable, List, Optional

_TIMING_RE = re.compile(
    r"(?P<sh>\d+):(?P<sm>\d{2}):(?P<ss>\d{2})[.,](?P<sms>\d{3})"
    r"\s*-->\s*"
    r"(?P<eh>\d+):(?P<em>\d{2}):(?P<es>\d{2})[.,](?P<ems>\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_SKIP_BLOCKS = ("NOTE", "STYLE", "REGION", "WEBVTT")

CONTEXT_LINES = ("align:", "position:", "line:", "size:", "region:", "vertical:")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _clean_text(raw: str) -> str:
    text = html.unescape(raw)
    text = _TAG_RE.sub("", text)          # <v Speaker>, <i>, <c.highlight> ...
    text = _WS_RE.sub(" ", text)
    return text.strip()


def format_timestamp(seconds: float, separator: str = ",") -> str:
    total_ms = int(round(max(seconds, 0.0) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def format_clock(seconds: float) -> str:
    """[hh:mm:ss] style timestamp, used in the readable transcript."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_vtt(text: str) -> List[Cue]:
    """Parse WebVTT (with or without cue identifiers) into cues."""
    cues: List[Cue] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [line.strip() for line in block.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            continue
        if lines[0].upper().startswith(_SKIP_BLOCKS):
            continue

        index = 0
        if re.fullmatch(r"\d+", lines[0]) and len(lines) > 1:
            index = 1

        timing = None
        while index < len(lines):
            timing = _TIMING_RE.search(lines[index])
            if timing:
                break
            index += 1
        if not timing:
            continue

        body = [line for line in lines[index + 1 :] if not line.lower().startswith(CONTEXT_LINES)]
        cue_text = _clean_text(" ".join(body))
        if not cue_text:
            continue

        cues.append(
            Cue(
                start=_seconds(timing["sh"], timing["sm"], timing["ss"], timing["sms"]),
                end=_seconds(timing["eh"], timing["em"], timing["es"], timing["ems"]),
                text=cue_text,
            )
        )
    return cues


def to_srt(cues: Iterable[Cue]) -> str:
    blocks = []
    for number, cue in enumerate(cues, 1):
        blocks.append(
            f"{number}\n"
            f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n"
            f"{cue.text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_timestamped(cues: Iterable[Cue]) -> str:
    return "".join(f"[{format_clock(cue.start)}] {cue.text}\n" for cue in cues)


def _sentences(cues: Iterable[Cue]) -> List[str]:
    joined = " ".join(cue.text for cue in cues)
    joined = _WS_RE.sub(" ", joined).strip()
    if not joined:
        return []
    return [part for part in re.split(r"(?<=[.!?])\s+", joined) if part]


def to_text(
    cues: Iterable[Cue],
    *,
    sentences_per_paragraph: int = 7,
    width: int = 100,
    header: Optional[str] = None,
) -> str:
    """Readable transcript: cues reflowed into paragraphs, timestamps dropped."""
    sentences = _sentences(cues)
    paragraphs = [
        " ".join(sentences[i : i + sentences_per_paragraph])
        for i in range(0, len(sentences), sentences_per_paragraph)
    ]
    body = "\n\n".join(textwrap.fill(p, width) for p in paragraphs)
    if header:
        return f"{header}\n\n{body}\n"
    return body + ("\n" if body else "")


def to_text_with_timestamps(
    cues: List[Cue],
    *,
    sentences_per_paragraph: int = 7,
    width: int = 100,
    header: Optional[str] = None,
) -> Optional[str]:
    """Like :func:`to_text` but each paragraph is prefixed with its start time."""
    paragraphs: List[str] = []
    buffer: List[str] = []
    buffer_start: Optional[float] = None
    sentences = 0

    for cue in cues:
        if buffer_start is None:
            buffer_start = cue.start
        buffer.append(cue.text)
        sentences += len(re.findall(r"[.!?](?=\s|$)", cue.text))
        if sentences >= sentences_per_paragraph:
            paragraphs.append(
                f"[{format_clock(buffer_start)}] "
                + textwrap.fill(_WS_RE.sub(" ", " ".join(buffer)), width)
            )
            buffer, buffer_start, sentences = [], None, 0

    if buffer and buffer_start is not None:
        paragraphs.append(
            f"[{format_clock(buffer_start)}] "
            + textwrap.fill(_WS_RE.sub(" ", " ".join(buffer)), width)
        )

    if not paragraphs:
        return None
    body = "\n\n".join(paragraphs)
    if header:
        return f"{header}\n\n{body}\n"
    return body + "\n"
