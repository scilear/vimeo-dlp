"""Command line entry point for ``vimeo-dlp``."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Sequence

from . import __version__
from .captions import (
    parse_vtt,
    to_srt,
    to_text,
    to_text_with_timestamps,
    to_timestamped,
)
from .vimeo import (
    Video,
    VimeoError,
    fetch_api_details,
    fetch_text,
    resolve,
    select_tracks,
)

DEFAULT_FORMAT = "bv*+ba/b"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _log(message: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(message, file=sys.stderr)


def _yt_dlp_binary() -> Optional[str]:
    return shutil.which("yt-dlp")


def _auth_args(args: argparse.Namespace) -> List[str]:
    out: List[str] = []
    if args.cookies:
        out += ["--cookies", args.cookies]
    if args.cookies_from_browser:
        out += ["--cookies-from-browser", args.cookies_from_browser]
    if args.username:
        out += ["--username", args.username]
    if args.password:
        out += ["--password", args.password]
    return out


def _format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "unknown"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:d}:{secs:02d}"


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #
def show_info(video: Video) -> None:
    print(f"title     : {video.title or '(untitled)'}")
    print(f"id        : {video.id}")
    print(f"url       : {video.page_url}")
    if video.owner:
        print(f"owner     : {video.owner}")
    print(f"duration  : {_format_duration(video.duration)}")
    if video.width and video.height:
        print(f"size      : {video.width}x{video.height} @ {video.fps or '?'} fps")
    if video.privacy:
        extra = f" (embed: {video.embed_permission})" if video.embed_permission else ""
        print(f"privacy   : {video.privacy}{extra}")
    if video.download_allowed is not None:
        print(f"download  : {'yes' if video.download_allowed else 'no'} (original file)")
    streams = [name for name, url in (("hls", video.hls_url), ("dash", video.dash_url)) if url]
    if video.progressive:
        streams.append(f"{len(video.progressive)} progressive")
    print(f"streams   : {', '.join(streams) or 'none'}")
    if video.text_tracks:
        print("subtitles :")
        for track in video.text_tracks:
            extra = f" [{track.provenance}]" if track.provenance else ""
            default = " (default)" if track.is_default else ""
            print(f"  - {track.lang}: {track.label or '?'}{extra}{default}")
    else:
        print("subtitles : none")


def show_urls(video: Video) -> None:
    if video.hls_url:
        print(f"hls: {video.hls_url}")
    if video.dash_url:
        print(f"dash: {video.dash_url}")
    for entry in video.progressive:
        print(f"progressive-{entry.get('quality', '?')}: {entry['url']}")
    for track in video.text_tracks:
        print(f"sub-{track.lang}: {track.url}")


def write_transcripts(
    video: Video,
    args: argparse.Namespace,
    tracks_langs: Optional[List[str]],
) -> List[str]:
    impersonate = not args.no_impersonate
    tracks = select_tracks(video.text_tracks, tracks_langs)
    if not tracks:
        _log(f"  no subtitles available for {video.id}", quiet=args.quiet)
        return []

    written: List[str] = []
    for track in tracks:
        raw = fetch_text(track.url, impersonate=impersonate, timeout=args.timeout)
        cues = parse_vtt(raw)
        if not cues:
            _log(f"  {track.lang}: empty subtitle track, skipped", quiet=args.quiet)
            continue

        header_title = video.title or video.id
        header = (
            f"{header_title}\n"
            f"{video.page_url}\n"
            f"Transcript: {track.label or track.lang}"
        )

        base = os.path.join(args.directory, f"{video.slug}.{track.lang}")
        payloads: Dict[str, str] = {
            "vtt": raw if raw.endswith("\n") else raw + "\n",
            "srt": to_srt(cues),
            "txt": to_text(cues, header=header),
        }
        if not args.no_timestamps:
            stamped = to_text_with_timestamps(cues, header=header)
            if stamped:
                payloads["timestamps.txt"] = stamped
            payloads["cues.txt"] = to_timestamped(cues)

        for extension, content in payloads.items():
            path = f"{base}.{extension}"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            written.append(path)
            _log(f"  wrote {path}", quiet=args.quiet)
    return written


def build_yt_dlp_command(video: Video, args: argparse.Namespace, url: str) -> List[str]:
    binary = _yt_dlp_binary()
    if not binary:
        raise VimeoError(
            "yt-dlp not found in PATH. Install it with:\n"
            "  pipx install yt-dlp    (or: pip install --user yt-dlp)"
        )
    # The generic extractor that handles the signed HLS URL knows nothing about
    # the video, so the default name is built from the config we already have.
    template = args.output or f"{video.slug} [{video.id}].%(ext)s"
    template = os.path.join(args.directory, template)
    command = [
        binary,
        "--referer",
        video.page_url,
        "-f",
        args.format,
        "-o",
        template,
        *_auth_args(args),
        *args.yt_dlp_arg,
        url,
    ]
    return command


def download_video(video: Video, args: argparse.Namespace) -> int:
    url = video.hls_url or video.dash_url
    if not url and video.progressive:
        url = video.progressive[-1]["url"]
    if not url:
        raise VimeoError(f"no stream URL found for {video.id}")

    command = build_yt_dlp_command(video, args, url)
    if args.print_cmd:
        print(" ".join(command))
        return 0

    kind = "HLS" if url == video.hls_url else "direct"
    _log(f"  downloading with yt-dlp ({kind})", quiet=args.quiet)
    return subprocess.call(command)


def list_formats(video: Video, args: argparse.Namespace) -> int:
    url = video.hls_url or video.dash_url
    if not url:
        raise VimeoError(f"no stream URL found for {video.id}")
    binary = _yt_dlp_binary()
    if not binary:
        raise VimeoError("yt-dlp not found in PATH")
    command = [binary, "--referer", video.page_url, "-F", *_auth_args(args), url]
    if args.print_cmd:
        print(" ".join(command))
        return 0
    return subprocess.call(command)


def _normalise_url(target: str) -> str:
    """Turn a bare id / scheme-less URL into something yt-dlp accepts."""
    value = target.strip()
    if value.isdigit():
        return f"https://vimeo.com/{value}"
    if "://" not in value:
        return f"https://{value.lstrip('/')}"
    return value


def delegate_to_yt_dlp(target: str, args: argparse.Namespace, reason: str) -> int:
    """Fallback: let yt-dlp handle the URL directly (needs a logged-in session)."""
    binary = _yt_dlp_binary()
    if not binary:
        raise VimeoError("yt-dlp not found in PATH")
    if not (args.cookies or args.cookies_from_browser or args.username):
        raise VimeoError(
            f"{reason}\n\n"
            "This usually means the video is private, password protected or needs a Vimeo\n"
            "login. You can retry with a session:\n"
            "  vimeo-dlp --cookies-from-browser firefox URL\n"
            "  vimeo-dlp --username you@example.com URL\n"
            "or, for a password protected video:\n"
            "  vimeo-dlp --video-password SECRET URL"
        )

    template = os.path.join(args.directory, args.output or "%(title)s [%(id)s].%(ext)s")
    command = [
        binary,
        *(["--video-password", args.video_password] if args.video_password else []),
        "-f",
        args.format,
        "-o",
        template,
        *_auth_args(args),
        *args.yt_dlp_arg,
        _normalise_url(target),
    ]
    _log(f"  player config unavailable ({reason}); delegating to yt-dlp with your session",
         quiet=args.quiet)
    if args.print_cmd:
        print(" ".join(command))
        return 0
    return subprocess.call(command)


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vimeo-dlp",
        description=(
            "Download public Vimeo videos and transcripts without a Vimeo account. "
            "Resolves the player config itself, then hands the signed HLS URL to yt-dlp."
        ),
        epilog=(
            "examples:\n"
            "  vimeo-dlp https://vimeo.com/1228213141\n"
            "  vimeo-dlp -t https://vimeo.com/1228213141\n"
            "  vimeo-dlp -a -l en,fr https://vimeo.com/1228213141\n"
            "  vimeo-dlp -i 1228213141\n"
            "  vimeo-dlp --urls 1228213141\n"
            "  vimeo-dlp URL -- --download-sections '*0:00-0:30'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    action = parser.add_argument_group("actions")
    action.add_argument("-t", "--transcript", action="store_true",
                        help="download subtitles only (.vtt, .srt, .txt)")
    action.add_argument("-a", "--all", action="store_true",
                        help="download the video AND its transcripts")
    action.add_argument("-i", "--info", action="store_true",
                        help="print metadata and available tracks, download nothing")
    action.add_argument("--urls", action="store_true",
                        help="print the signed HLS/DASH/subtitle URLs and exit")
    action.add_argument("-F", "--list-formats", action="store_true",
                        help="list the video formats (runs yt-dlp -F)")

    output = parser.add_argument_group("output")
    output.add_argument("-d", "--dir", dest="directory", default=".",
                        help="output directory (default: current directory)")
    output.add_argument("-o", "--output", help="yt-dlp output template for the video")
    output.add_argument("-f", "--format", default=DEFAULT_FORMAT,
                        help=f"yt-dlp format selection (default: {DEFAULT_FORMAT!r})")
    output.add_argument("-l", "--lang", default=None,
                        help="comma separated subtitle languages, e.g. 'en,fr' "
                             "(default: English if available, else the first track)")
    output.add_argument("--no-timestamps", action="store_true",
                        help="skip the paragraph- and cue-level timestamped transcripts")

    auth = parser.add_argument_group("authentication (only needed for private videos)")
    auth.add_argument("--cookies", help="Netscape cookies file passed to yt-dlp")
    auth.add_argument("--cookies-from-browser", help="e.g. 'firefox', 'brave', or "
                                                    "'brave:/path/to/profile'")
    auth.add_argument("-u", "--username", help="Vimeo account email")
    auth.add_argument("-p", "--password", help="Vimeo account password (asked if omitted)")
    auth.add_argument("--video-password", help="password of a password protected video")

    misc = parser.add_argument_group("misc")
    misc.add_argument("--no-impersonate", action="store_true",
                      help="do not use a browser TLS fingerprint (curl_cffi)")
    misc.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    misc.add_argument("--yt-dlp-arg", action="append", default=[], metavar="ARG",
                      help="extra argument forwarded to yt-dlp (repeatable). Anything "
                           "after a literal '--' is forwarded too.")
    misc.add_argument("--print-cmd", action="store_true",
                      help="print the yt-dlp command instead of running it")
    misc.add_argument("-q", "--quiet", action="store_true", help="only print results")
    misc.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("targets", nargs="+", metavar="URL|ID")

    return parser


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run_target(target: str, args: argparse.Namespace) -> int:
    langs = [part.strip() for part in args.lang.split(",")] if args.lang else None
    impersonate = not args.no_impersonate

    try:
        video = resolve(target, impersonate=impersonate, timeout=args.timeout)
    except VimeoError as exc:
        if args.info or args.urls:
            raise
        return delegate_to_yt_dlp(target, args, str(exc))

    _log(f"{video.title or video.id} ({video.id})", quiet=args.quiet)

    if args.info:
        try:
            details = fetch_api_details(
                video.id, impersonate=impersonate, timeout=args.timeout
            )
        except (VimeoError, ValueError):
            details = None
        if details:
            privacy = details.get("privacy") or {}
            if isinstance(privacy, dict):
                video.download_allowed = privacy.get("download")
        show_info(video)
        return 0
    if args.urls:
        show_urls(video)
        return 0
    if args.list_formats:
        return list_formats(video, args)

    status = 0
    if args.transcript or args.all:
        write_transcripts(video, args, langs)
    if args.all or not args.transcript:
        status = download_video(video, args)
    return status


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    passthrough: List[str] = []
    if "--" in raw:
        split = raw.index("--")
        passthrough, raw = raw[split + 1 :], raw[:split]

    parser = build_parser()
    args = parser.parse_args(raw)
    if passthrough:
        args.yt_dlp_arg = list(args.yt_dlp_arg) + passthrough

    os.makedirs(args.directory, exist_ok=True)

    status = 0
    for target in args.targets:
        try:
            status = run_target(target, args) or status
        except VimeoError as exc:
            print(f"vimeo-dlp: error: {exc}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
