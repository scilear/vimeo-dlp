# vimeo-dlp

Download **public Vimeo videos and their transcripts without a Vimeo account**.

`yt-dlp` alone stopped working on Vimeo: since 2025 its extractor answers

```
ERROR: [vimeo] 1228213141: The web client only works when logged-in.
```

…even for videos that are perfectly public. `vimeo-dlp` fills that one gap. It
reads the public player page, extracts `window.playerConfig` (signed HLS/DASH
URLs + subtitle tracks), then hands the signed URL to `yt-dlp` for the actual
download. No account, no cookies, no browser.

## Install

```bash
git clone https://github.com/scilear/vimeo-dlp.git
cd vimeo-dlp
python3 -m venv ~/.local/share/vimeo-dlp/venv
~/.local/share/vimeo-dlp/venv/bin/pip install -e .
ln -sf ~/.local/share/vimeo-dlp/venv/bin/vimeo-dlp ~/.local/bin/vimeo-dlp
```

Requires Python ≥ 3.10, [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) and
[`ffmpeg`](https://ffmpeg.org) in `PATH` for the video download.

## Usage

```bash
vimeo-dlp https://vimeo.com/1228213141          # download the video
vimeo-dlp -t https://vimeo.com/1228213141       # transcripts only
vimeo-dlp -a https://vimeo.com/1228213141       # video + transcripts
vimeo-dlp -i 1228213141                         # metadata, download nothing
vimeo-dlp --urls 1228213141                     # print the signed URLs
vimeo-dlp -F 1228213141                         # list formats (yt-dlp -F)
vimeo-dlp URL -- --download-sections '*0:00-0:30'   # forward flags to yt-dlp
```

A bare id, a `player.vimeo.com/video/<id>` URL, an unlisted link with its hash,
a channel URL or a `vimeopro.com` URL all work.

### Transcripts

`-t` writes four files per subtitle track:

| File | Content |
|---|---|
| `<slug>.<lang>.vtt` | the original WebVTT |
| `<slug>.<lang>.srt` | SRT subtitles for any player |
| `<slug>.<lang>.txt` | readable transcript, reflowed into paragraphs |
| `<slug>.<lang>.timestamps.txt` | paragraphs prefixed with `[hh:mm:ss]` |
| `<slug>.<lang>.cues.txt` | one line per cue with its timestamp |

Default language is English when available, otherwise the first track.
Pick others with `-l en,fr` (substring match, so `en` also selects `en-x-autogen`).
`--no-timestamps` drops the two timestamped variants.

### Options

```
-d, --dir DIR              output directory (default: .)
-o, --output TEMPLATE      yt-dlp output template for the video
-f, --format SELECTOR      yt-dlp format selection (default: bv*+ba/b)
-l, --lang LIST            comma separated subtitle languages
--no-impersonate           do not use a browser TLS fingerprint
--print-cmd                print the yt-dlp command instead of running it
--yt-dlp-arg ARG           extra argument forwarded to yt-dlp (repeatable)
URL -- ARGS...             everything after a literal '--' goes to yt-dlp
-q, --quiet                only print results
```

## Videos that need a login

Private, password protected or geo-blocked videos have no public player
config. In that case `vimeo-dlp` falls back to plain `yt-dlp` — but only if you
give it a session:

```bash
vimeo-dlp --cookies-from-browser firefox URL
vimeo-dlp --cookies-from-browser "brave:$HOME/.config/BraveSoftware/Brave-Browser" URL
vimeo-dlp --cookies ~/vimeo-cookies.txt URL
vimeo-dlp --username you@example.com URL     # password asked interactively
vimeo-dlp --video-password SECRET URL
```

Tip: `--cookies FILE` both reads and rewrites the cookie jar, so a single
`--username` login persists for later runs.

## How it works

```
https://vimeo.com/<id>                    -> sets the __cf_bm cookie
https://player.vimeo.com/video/<id>      -> HTML containing window.playerConfig
    request.files.hls.cdns[default].avc_url   -> signed HLS master playlist
    request.text_tracks[]                     -> signed .vtt URLs
```

Two details matter:

1. **TLS fingerprint.** `player.vimeo.com` answers `403` with a “Sorry” page to
   plain `curl`/`urllib`. The tool impersonates Chrome through
   [`curl_cffi`](https://github.com/lexiforest/curl_cffi); `--no-impersonate`
   disables it, mostly for debugging.
2. **Expiry.** CDN URLs are signed and expire after roughly an hour. That is
   why they are resolved on every run instead of being cached.

`vimeo-dlp` deliberately does not reimplement a downloader: it only resolves
URLs, and `yt-dlp` does the rest (format selection, HLS, muxing via `ffmpeg`).

## Notes and limits

- Resolution is whatever the uploader transcoded — usually ≤ 1080p, often 720p.
  If the owner disabled downloads (`privacy.download = false`), the original
  source file is not available even with an account.
- Vimeo occasionally changes its player markup. If extraction breaks, open an
  issue with the URL.
- Respect the uploader's rights and Vimeo's terms of service. This tool is for
  content you are allowed to download (talks, webinars, courses you bought,
  your own uploads…).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT
