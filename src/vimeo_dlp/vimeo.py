"""Resolve public Vimeo videos without an account.

yt-dlp's Vimeo extractor refuses to run anonymously ("The web client only works
when logged-in").  The public player page, however, ships the whole config in
``window.playerConfig``: signed HLS/DASH URLs plus the subtitle tracks.

This module only does that one job -- resolve a video id into stream URLs.
Downloading is delegated to yt-dlp.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

VIMEO_HOME = "https://vimeo.com"
PLAYER_URL = "https://player.vimeo.com/video/{video_id}?app_id=58479"
CONFIG_MARKERS = ("window.playerConfig =", "window.playerConfig=")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# vimeo.com/<id>, vimeo.com/<id>/<unlisted_hash>, player.vimeo.com/video/<id>,
# vimeopro.com/<user>/<album>/video/<id>, channels/<name>/<id>, ...
_URL_PATTERNS = (
    re.compile(r"player\.vimeo\.com/video/(?P<id>\d+)(?:[/?](?P<hash>[0-9a-f]{6,}))?"),
    re.compile(r"vimeo(?:pro)?\.com/(?:.*?/)?(?:video/)?(?P<id>\d{6,})(?:[/?](?P<hash>[0-9a-f]{6,}))?"),
    re.compile(r"^/?videos?/(?P<id>\d+)$"),
)


class VimeoError(RuntimeError):
    """Raised when a video cannot be resolved."""


@dataclass
class TextTrack:
    lang: str
    url: str
    label: str = ""
    kind: str = "subtitles"
    provenance: str = ""
    is_default: bool = False


@dataclass
class Video:
    """Everything we can learn about a video from the player config."""

    id: str
    title: str = ""
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    owner: str = ""
    privacy: str = ""
    embed_permission: str = ""
    download_allowed: Optional[bool] = None
    hls_url: Optional[str] = None
    dash_url: Optional[str] = None
    progressive: List[Dict[str, Any]] = field(default_factory=list)
    text_tracks: List[TextTrack] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def page_url(self) -> str:
        return f"{VIMEO_HOME}/{self.id}"

    @property
    def slug(self) -> str:
        return slugify(self.title) if self.title else self.id

    @property
    def downloadable_by_owner(self) -> Optional[bool]:
        """Whether the uploader enabled the "Download" button (needs the API)."""
        return self.download_allowed


def slugify(text: str, max_length: int = 120) -> str:
    slug = re.sub(r"[^\w\-]+", "-", text, flags=re.UNICODE).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:max_length].rstrip("-")) or "video"


def parse_target(target: str) -> Tuple[str, Optional[str]]:
    """Return ``(video_id, unlisted_hash)`` for any Vimeo URL or bare id."""
    value = target.strip()
    if value.isdigit():
        return value, None
    for pattern in _URL_PATTERNS:
        match = pattern.search(value)
        if match:
            return match.group("id"), match.groupdict().get("hash")
    # Last resort: an album/embed URL with a trailing id.
    match = re.search(r"(\d{6,})", value)
    if match:
        return match.group(1), None
    raise VimeoError(f"Not a Vimeo video URL or id: {target!r}")


def extract_json_object(text: str, start: int) -> str:
    """Slice a balanced ``{...}`` object out of ``text``, ignoring braces in strings."""
    if start >= len(text) or text[start] != "{":
        raise VimeoError("internal error: config does not start with '{'")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise VimeoError("unterminated player config in player page")


class _Http:
    """Minimal HTTP client: browser TLS fingerprint when curl_cffi is available."""

    def __init__(self, impersonate: bool = True, timeout: int = 30) -> None:
        self.timeout = timeout
        self._session = None
        self._using_impersonation = False
        if impersonate:
            try:
                from curl_cffi import requests as curl_requests  # type: ignore

                self._session = curl_requests.Session(impersonate="chrome")
                self._using_impersonation = True
            except Exception:  # pragma: no cover - optional dependency
                self._session = None
        if self._session is None:
            import http.cookiejar

            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            )

    @property
    def impersonating(self) -> bool:
        return self._using_impersonation

    def get(
        self,
        url: str,
        referer: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> str:
        request_headers = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
        if referer:
            request_headers["Referer"] = referer
        request_headers.update(headers or {})
        try:
            if self._session is not None:
                response = self._session.get(url, headers=request_headers, timeout=self.timeout)
                status = response.status_code
                body = response.text
            else:
                request = urllib.request.Request(url, headers=request_headers)
                with self._opener.open(request, timeout=self.timeout) as handle:
                    status = handle.status
                    body = handle.read().decode("utf-8", "replace")
        except Exception as exc:  # network level failure
            raise VimeoError(f"HTTP request failed for {url}: {exc}") from exc
        if status >= 400:
            raise VimeoError(f"HTTP {status} for {url}")
        return body


def fetch_player_config(
    video_id: str,
    unlisted_hash: Optional[str] = None,
    *,
    impersonate: bool = True,
    timeout: int = 30,
    http: Optional[_Http] = None,
) -> Dict[str, Any]:
    """Load ``window.playerConfig`` from the public player page."""
    client = http or _Http(impersonate=impersonate, timeout=timeout)
    page = f"{VIMEO_HOME}/{video_id}" + (f"/{unlisted_hash}" if unlisted_hash else "")
    try:
        # Sets the __cf_bm cookie the player needs; failure here is not fatal.
        client.get(page)
    except VimeoError:
        pass

    player_url = PLAYER_URL.format(video_id=video_id)
    if unlisted_hash:
        player_url += f"&h={unlisted_hash}"
    try:
        html = client.get(player_url, referer=page)
    except VimeoError as exc:
        raise VimeoError(
            f"Could not load the Vimeo player page for {video_id} ({exc}).\n"
            "The video may be private, password protected or geo-blocked."
        ) from exc

    for marker in CONFIG_MARKERS:
        index = html.find(marker)
        if index == -1:
            continue
        brace = html.find("{", index + len(marker))
        if brace == -1:
            continue
        try:
            return json.loads(extract_json_object(html, brace))
        except (json.JSONDecodeError, VimeoError) as exc:
            raise VimeoError(f"Could not parse the player config for {video_id}") from exc

    raise VimeoError(
        f"No player config found for {video_id}.\n"
        "The video is probably private, password protected or requires a login."
    )


def _best_cdn_url(container: Any, key: str = "url") -> Optional[str]:
    """Pick the default CDN URL out of an HLS/DASH container."""
    if not isinstance(container, dict):
        return None
    cdns = container.get("cdns") or {}
    default = container.get("default_cdn")
    candidates = []
    if isinstance(default, str) and default in cdns:
        candidates.append(cdns[default])
    candidates.extend(cdns.values())
    for entry in candidates:
        if isinstance(entry, dict) and entry.get(key):
            return entry[key]
    return None


def build_video(video_id: str, config: Dict[str, Any]) -> Video:
    """Turn a raw player config into a :class:`Video`."""
    info = config.get("video") or {}
    request = config.get("request") or {}
    files = request.get("files") or {}

    hls = files.get("hls") or {}
    dash = files.get("dash") or {}

    tracks = []
    for track in request.get("text_tracks") or []:
        if not isinstance(track, dict) or not track.get("url"):
            continue
        tracks.append(
            TextTrack(
                lang=str(track.get("lang") or "und"),
                url=track["url"],
                label=str(track.get("label") or ""),
                kind=str(track.get("kind") or "subtitles"),
                provenance=str(track.get("provenance") or ""),
                is_default=bool(track.get("default")),
            )
        )

    progressive = []
    for entry in files.get("progressive") or []:
        if isinstance(entry, dict) and entry.get("url"):
            progressive.append(entry)

    owner = ""
    owner_info = info.get("owner") or {}
    if isinstance(owner_info, dict):
        owner = owner_info.get("name") or ""

    privacy_raw = info.get("privacy")
    if isinstance(privacy_raw, dict):
        privacy_view = str(privacy_raw.get("view") or "")
        download_allowed: Optional[bool] = privacy_raw.get("download")
    else:
        privacy_view = str(privacy_raw or "")
        download_allowed = None

    return Video(
        id=str(info.get("id") or video_id),
        title=str(info.get("title") or ""),
        duration=info.get("duration"),
        width=info.get("width"),
        height=info.get("height"),
        fps=info.get("fps"),
        owner=owner,
        privacy=privacy_view,
        embed_permission=str(info.get("embed_permission") or ""),
        download_allowed=download_allowed,
        hls_url=_best_cdn_url(hls, "avc_url") or _best_cdn_url(hls, "url"),
        dash_url=_best_cdn_url(dash, "url"),
        progressive=progressive,
        text_tracks=tracks,
        raw=config,
    )


def resolve(
    target: str,
    *,
    impersonate: bool = True,
    timeout: int = 30,
) -> Video:
    """Full pipeline: URL/id -> :class:`Video`."""
    video_id, unlisted_hash = parse_target(target)
    config = fetch_player_config(
        video_id, unlisted_hash, impersonate=impersonate, timeout=timeout
    )
    return build_video(video_id, config)


def fetch_api_details(
    video_id: str,
    *,
    impersonate: bool = True,
    timeout: int = 15,
) -> Dict[str, Any]:
    """Best effort metadata from api.vimeo.com (anonymous viewer token).

    The player config says *whether* a video is private, but only the API
    exposes ``privacy.download``, i.e. whether the owner allowed downloads.
    """
    client = _Http(impersonate=impersonate, timeout=timeout)
    viewer = json.loads(client.get(f"{VIMEO_HOME}/_next/viewer"))
    token = viewer.get("jwt")
    if not token:
        raise VimeoError("no anonymous viewer token")
    body = client.get(
        f"https://api.vimeo.com/videos/{video_id}"
        "?fields=name,description,duration,privacy,stats,created_time",
        headers={
            "Authorization": f"jwt {token}",
            "Accept": "application/vnd.vimeo.*+json; version=3.4.10",
        },
    )
    return json.loads(body)


def fetch_text(url: str, *, impersonate: bool = True, timeout: int = 30) -> str:
    """Fetch a small text resource (used for subtitle tracks)."""
    return _Http(impersonate=impersonate, timeout=timeout).get(url)


def select_tracks(tracks: List[TextTrack], langs: Optional[List[str]]) -> List[TextTrack]:
    """Filter tracks by language prefix/substring; default to English if present."""
    if not tracks:
        return []
    if langs:
        wanted = [lang.lower() for lang in langs]
        chosen = [t for t in tracks if any(w in t.lang.lower() for w in wanted)]
        return chosen or tracks
    english = [t for t in tracks if t.lang.lower().startswith("en")]
    return english or tracks[:1]
