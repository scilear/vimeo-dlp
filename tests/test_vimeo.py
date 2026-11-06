import pytest

from vimeo_dlp.vimeo import (
    VimeoError,
    build_video,
    extract_json_object,
    parse_target,
    select_tracks,
    slugify,
)

CONFIG = {
    "video": {
        "id": 1228213141,
        "title": "OTA-Webinar-2026-09-18 / Risk-Free Transformers",
        "duration": 3137,
        "width": 1280,
        "height": 720,
        "fps": 25,
        "privacy": {"view": "anybody", "embed": "public", "download": False},
        "owner": {"name": "Charles Karow"},
    },
    "request": {
        "files": {
            "hls": {
                "default_cdn": "akfire",
                "cdns": {
                    "akfire": {"avc_url": "https://cdn.example/master.m3u8"},
                    "backup": {"avc_url": "https://backup.example/master.m3u8"},
                },
            },
            "dash": {
                "default_cdn": "akfire",
                "cdns": {"akfire": {"url": "https://cdn.example/playlist.json"}},
            },
        },
        "text_tracks": [
            {
                "lang": "en-x-autogen",
                "url": "https://captions.example/en.vtt",
                "label": "English (auto-generated)",
                "provenance": "ai_generated",
                "default": True,
            },
            {
                "lang": "fr",
                "url": "https://captions.example/fr.vtt",
                "label": "Français",
            },
        ],
    },
}


@pytest.mark.parametrize(
    "target,expected",
    [
        ("https://vimeo.com/1228213141", "1228213141"),
        ("https://vimeo.com/1228213141/abc123def", "1228213141"),
        ("https://player.vimeo.com/video/1228213141?app_id=58479", "1228213141"),
        ("https://vimeo.com/channels/staffpicks/76979871", "76979871"),
        ("https://vimeopro.com/user/showreel/video/123456789", "123456789"),
        ("1228213141", "1228213141"),
        ("  https://vimeo.com/123456789?share=copy  ", "123456789"),
    ],
)
def test_parse_target(target, expected):
    assert parse_target(target)[0] == expected


def test_parse_target_keeps_unlisted_hash():
    video_id, unlisted_hash = parse_target("https://vimeo.com/123456789/abcdef0123")
    assert video_id == "123456789"
    assert unlisted_hash == "abcdef0123"


def test_parse_target_rejects_garbage():
    with pytest.raises(VimeoError):
        parse_target("https://example.com/not-a-video")


def test_extract_json_object_is_brace_safe():
    html = 'before window.playerConfig = {"a": {"b": "}"}, "c": [1, {"d": "\\\\"}]} after'
    start = html.index("{")
    assert extract_json_object(html, start) == '{"a": {"b": "}"}, "c": [1, {"d": "\\\\"}]}'


def test_slugify():
    assert slugify("OTA Webinar 2026 / Risk-Free") == "OTA-Webinar-2026-Risk-Free"
    assert slugify("!!!") == "video"


def test_build_video():
    video = build_video("1228213141", CONFIG)
    assert video.title.startswith("OTA-Webinar")
    assert video.hls_url == "https://cdn.example/master.m3u8"
    assert video.dash_url == "https://cdn.example/playlist.json"
    assert video.owner == "Charles Karow"
    assert video.downloadable_by_owner is False
    assert len(video.text_tracks) == 2
    assert video.text_tracks[0].provenance == "ai_generated"
    assert video.text_tracks[0].is_default


def test_select_tracks_defaults_to_english():
    tracks = build_video("1", CONFIG).text_tracks
    assert [t.lang for t in select_tracks(tracks, None)] == ["en-x-autogen"]


def test_select_tracks_explicit_language():
    tracks = build_video("1", CONFIG).text_tracks
    assert [t.lang for t in select_tracks(tracks, ["fr"])] == ["fr"]
    assert len(select_tracks(tracks, ["en", "fr"])) == 2


def test_select_tracks_unknown_language_falls_back():
    tracks = build_video("1", CONFIG).text_tracks
    assert select_tracks(tracks, ["de"]) == tracks


def test_build_video_without_streams():
    video = build_video("42", {"video": {"id": 42}, "request": {}})
    assert video.hls_url is None
    assert video.text_tracks == []
    assert video.slug == "42"
