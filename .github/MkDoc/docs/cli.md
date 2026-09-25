# CLI Usage

!!! tip
    TMDB-based filename tokens (`%(tmdb_id)`, `%(original_title)`, etc.) and poster embedding
    require a TMDB API key — see [TMDB API Key](configuration.md#tmdb-api-key) for setup.

## Basic Commands

```bash
# Show help and available sites
python manual.py -h

# Search and download
python manual.py -i streamingcommunity --search "interstellar"

# Auto-download the first result (--item 0 picks the first result, 0-based)
python manual.py -i streamingcommunity --search "interstellar" --item 0

# Select a specific result by index (0-based) instead of the first
python manual.py -i streamingcommunity --search "interstellar" --item 2

# Use a site by its index number
python manual.py -i 0 --search "interstellar"

# Skip TS/CAM releases (StreamingCommunity only)
python manual.py -i streamingcommunity --search "interstellar" --skip-ts

# Disable the log file for this run
python manual.py -i streamingcommunity --search "interstellar" --no-log
```

## Series Selection

Use `--season` and `--episode` to skip interactive prompts:

```bash
# Specific episode
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season 1 --episode 3

# Range of episodes
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season 1 --episode "1-5"

# All episodes of a season
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season 1 --episode "*"

# All episodes of all seasons
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season "*"

# Multiple seasons
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season "1-3"
```

## Year Filter

```bash
# Exact year
python manual.py -i streamingcommunity --search "dune" --year 2021

# Year range
python manual.py -i streamingcommunity --search "batman" --year "1990-2015"
```

## Stream Track Overrides

```bash
# Video resolution
python manual.py -i streamingcommunity --search "interstellar" -sv 1080

# Audio language
python manual.py -i streamingcommunity --search "interstellar" -sa "eng"

# Subtitles
python manual.py -i streamingcommunity --search "interstellar" -ss "eng"

# Skip the whole download if the requested filter matches no track
python manual.py -i streamingcommunity --search "interstellar" -sa "deu" --skip-no-match

# Output container (overrides PROCESS.extension from config.json for this run)
python manual.py -i streamingcommunity --search "interstellar" --extension mp4
```

See [Stream Selection Filters](configuration.md#stream-selection-filters) for the full
`-sv`/`-sa`/`-ss` filter syntax (resolution, codec, bitrate, language, Dolby Vision companion).

## Console Behaviour Override

```bash
# Keep console open (loop mode)
python manual.py --close-console false

# Close console after download
python manual.py -i streamingcommunity --search "interstellar" --close-console true
```

## Proxy

```bash
# Use the configured proxy for everything (default scope)
python manual.py -i streamingcommunity --search "interstellar" --use_proxy

# Proxy only the downloads (Velora), scrape directly
python manual.py -i streamingcommunity --search "interstellar" --use_proxy --proxy-scope down

# Proxy only the scraping, download directly
python manual.py -i streamingcommunity --search "interstellar" --use_proxy --proxy-scope scrap
```

## Show Dependency Paths

```bash
python manual.py --dep
```

## Direct Download by URL (`--down`)

Download a stream directly from its URL, bypassing site search entirely. The stream type is
auto-detected (MP4 / HLS / DASH / ISM) or can be forced with `--type`.

```bash
# Simple MP4 / auto-detected stream
python manual.py --down "https://example.com/video.mp4" -o "./Video/clip.mp4"

# HLS with a known decryption key
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --key "<KID>:<KEY>" -o "./Video/movie.mkv"

# DASH with a DRM license server (Widevine)
python manual.py --down "https://example.com/manifest.mpd" --type dash \
  --license-url "https://example.com/wv/license" --drm widevine \
  --headers "Authorization: Bearer <token>" -o "./Video/movie.mkv"

# DASH with a DRM license server that also needs its own HTTP header (repeatable, like --headers)
python manual.py --down "https://example.com/manifest.mpd" --type dash \
  --license-url "https://example.com/wv/license" --drm widevine \
  --license-headers "Authorization: Bearer <token>" -o "./Video/movie.mkv"

# Grab just a clip: segments 10-50, or the 00:01:00-00:05:00 time range
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --max-segments "10-50" -o "./Video/clip.mkv"
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --max-time "00:01:00-00:05:00" -o "./Video/clip.mkv"
```

### Custom JSON Manifest

`--down` also accepts a local path or URL pointing at a **custom JSON manifest** — a plain
stream description for sources that don't expose a real HLS/DASH/ISM manifest. It's
auto-detected when the target is a `.json` file/URL containing the `"vibravid_manifest"`
marker, no `--type` needed.

```json
{
  "vibravid_manifest": true,
  "base_url": "https://cdn.example.com/movie/",
  "duration": 5410.0,
  "tracks": [
    {
      "type": "video", "id": "v1", "codecs": "avc1.640028",
      "width": 1920, "height": 1080, "bitrate": 4500000,
      "init": "init_video.mp4",
      "segments": {"template": "chunk_video_$Number$.m4s", "start": 1, "end": 1352, "duration": 4.0}
    },
    {
      "type": "audio", "id": "a1", "language": "eng", "codecs": "mp4a.40.2",
      "init": "init_audio.mp4",
      "segments": {"list": ["seg0.m4s", "seg1.m4s", "seg2.m4s"]}
    }
  ]
}
```

```bash
python manual.py --down "https://example.com/manifest.json" -o "./Video/movie.mkv"
python manual.py --down "./local_manifest.json" -o "./Video/movie.mkv"
```

Segment modes for a track's `segments` field:

| Mode | Fields | Behaviour |
|---|---|---|
| `list` | array of URLs, or objects with `url`/`size`/`duration`/`range` | explicit segment list |
| `template` | `template`, `start`, `step` (or `duration`), `end` (or `count`) | DASH-style `$Number$` / `$Time$` / `$RepresentationID$` placeholder expansion |
| `ranges` / `chunk` | `url` + explicit `ranges` array, or `chunk` + `size` to auto-split | byte-range segments carved out of a single file |

`init` accepts a URL string, an object (`url`, `range`), or inline base64 (`data`) for the
initialization segment. Placeholders support zero-padding, e.g. `$Number%05d$`; `$$` escapes
a literal `$`.

### Batch Replay from a TRACKS_JSON file (`--down-json`)

`--down-json` runs every entry's `cmd` from a `TRACKS_JSON` debug file (see
`debug_track_json` in [Configuration](configuration.md)) in sequence, instead of a single
`--down` invocation:

```bash
python manual.py --down-json "./debug/tracks_20260821.json"
```

### Attaching metadata to a direct download (`--meta-*`)

A plain `--down` has no title/type/season/episode context, so title-dependent hooks and the
Vault upload/lookup are skipped by default for it. Pass `--meta-*` flags to attach that context
so those still work:

```bash
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --key "<KID>:<KEY>" -o "./Video/Movie (2024)/movie.mkv" \
  --meta-title "Movie Name" --meta-type Film --meta-site streamingcommunity

# TV episode
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --key "<KID>:<KEY>" -o "./Video/Show/Season 01/episode.mkv" \
  --meta-title "Show Name" --meta-type TV --meta-season 1 --meta-episode 3
```

`--resolve-only` sets these automatically on the `--down` entry it produces, so this is mostly
needed when hand-building a `--down`/`--down-json` invocation yourself.

## Direct Download via yt-dlp (`--yt-dlp`)

Download from any site yt-dlp supports bypassing site search entirely — separate from `--down`, which is for direct HLS/DASH/ISM/MP4 URLs.

```bash
# Basic download
python manual.py --yt-dlp "https://www.youtube.com/watch?v=..." -o "./Video/clip.mp4"

# Pick a specific format instead of the default bestvideo+bestaudio/best
python manual.py --yt-dlp "https://www.youtube.com/watch?v=..." --format "best[height<=720]"

# List available formats and exit, or pick one interactively
python manual.py --yt-dlp "https://www.youtube.com/watch?v=..." --list-formats
python manual.py --yt-dlp "https://www.youtube.com/watch?v=..." --interactive-format

# Extract audio only
python manual.py --yt-dlp "https://www.youtube.com/watch?v=..." --extract-audio \
  --audio-format mp3 --audio-quality 0

# Subtitles
python manual.py --yt-dlp "https://www.youtube.com/watch?v=..." --write-subs --sub-langs it,en
python manual.py --yt-dlp "https://www.youtube.com/watch?v=..." --write-auto-subs

# Only the first N entries of a playlist
python manual.py --yt-dlp "https://www.youtube.com/playlist?list=..." --playlist-end 5
```

| Flag | Effect |
|---|---|
| `--yt-dlp <URL>` | Download this URL with yt-dlp instead of the site-search/`--down` flow |
| `--format <SPEC>` | yt-dlp format selector (default: `bestvideo+bestaudio/best`) |
| `--list-formats` | List available formats for the URL and exit |
| `--interactive-format` | Show available formats and prompt for the format ID before downloading |
| `--extract-audio` | Extract audio only |
| `--audio-format <FORMAT>` | Audio format for extraction (e.g. `mp3`, `m4a`, `wav`, `opus`) |
| `--audio-quality <QUALITY>` | Audio quality for extraction (e.g. `0`, `5`, `8k`) |
| `--playlist-end <N>` | Only process the first N entries of a playlist |
| `--sub-langs <LANGS>` | Comma-separated subtitle languages (e.g. `it,en`) |
| `--write-subs` | Write subtitles |
| `--write-auto-subs` | Write auto-generated subtitles |

## Advanced Options

| Flag | Effect |
|---|---|
| `--use-curl-cffi` | Download segments via curl_cffi (browser TLS impersonation) instead of Velora — for sites where individual segments are Cloudflare-protected |
| `--http-version {1.1,2,3}` | Force the HTTP protocol version used for requests instead of letting the client negotiate it |
| `-o`, `--output <PATH>` | Output file path for `--down`/`--yt-dlp` direct downloads |
| `--amazon-music-login` | Log in to Amazon Music (stores credentials for future runs) |
| `--amazon-music-logout` | Clear stored Amazon Music login credentials |
| `--no-vault-cache` | Bypass the DRM key vault cache; force a fresh CDM license request every run (for dynamic/time-sensitive tokens) |
| `--no-decrypt` | Debug switch: don't decrypt at all (neither the in-flight per-segment path nor the post-download pass) |
| `--no-livemux` | Disable the streaming-mux fast path for this run, always falling back to the normal post-download `join_media()` pass. On by default |
| `--livemux` | Force-enable the streaming-mux fast path for this run even if the current service does not opt in via `_live_mux = True`. Off by default |
| `--no-concurrent` | Download video, audio and subtitles sequentially instead of simultaneously for this run. Concurrent download is on by default |
| `--skip-no-match` | Skip the whole download if `-sv`/`-sa`/`-ss` matches no track, instead of falling back to the best available one |
| `--log-decryptor-output` | Write flux's own stdout+stderr lines to the log file as they run, tagged with the engine name (e.g. `[FLUX]`) instead of `[INFO]` |
| `--abc` | Anonymize printed KID:KEY pairs in the console/log, masking alternating characters with `?` |
| `--hls-method AES_128\|NONE` | Override the HLS segment encryption method, ignoring the manifest's own `#EXT-X-KEY` tag (or supplying one when it has none). `NONE` treats every segment as already clear; `AES_128` forces AES-128-CBC (pair with `--hls-key`/`--hls-iv`) |
| `--hls-key <HEX\|BASE64\|FILE>` | Raw AES-128 key to use instead of fetching `URI=` from the manifest's `#EXT-X-KEY` tag |
| `--hls-iv <HEX>` | IV to use instead of the manifest's `IV=0x...` (or the implicit per-segment IV) |
| `--skip-content-check` | Skip the preflight HEAD content-type check for MP4 direct downloads (`--type mp4`) — needed for single-use download URLs where a HEAD request consumes the link |
| `--skip-sanitize` | Use the `-o` output path verbatim (MP4/HLS/DASH/ISM direct downloads), skipping path sanitization (transliteration of non-ASCII characters) |
| `--no-manifest-info` | Don't print the parsed manifest/streams table |
| `--binary-update` | Check FFmpeg/Flux/Packager/dovi_tool/MKVToolNix/Velora against AstraeLabs/Binary and re-download whichever is outdated |
| `--resolve-only` | Resolve and cache the manifest (keys, license, playlist) without actually downloading — pairs with `--down-json`/the queue to download later without re-resolving |
| `--tui` | Launch the Textual terminal UI instead of the plain CLI flow |
| `-UP`, `--update` | Auto-update to the latest release (binary builds only) |
| `--version` | Print the installed version and exit |

```bash
# Resolve now, download later: cache the manifest/keys without downloading
python manual.py -i streamingcommunity --search "interstellar" --item 0 --resolve-only

# Launch the TUI instead of the classic prompt-driven flow
python manual.py --tui
```

```bash
# Manual AES-128 override, e.g. for a manifest missing its own #EXT-X-KEY tag
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --hls-method AES_128 \
  --hls-key "0011223344556677889900112233445566" \
  --hls-iv "00000000000000000000000000000001" \
  -o "./Video/clip.mkv"
```

## Download Queue (`--queue-*`)

```bash
# Queue instead of downloading now
python manual.py -i streamingcommunity --search "interstellar" --item 0 --queue-add
python manual.py --down "https://example.com/movie.mkv" -o "./Video/movie.mkv" --queue-add

# Inspect / manage the queue
python manual.py --queue-list
python manual.py --queue-remove <ID>
python manual.py --queue-clear

# Process every pending (or interrupted) item in order
python manual.py --queue-run
```

!!! note
    Only invocations that would already complete without any prompt can be queued; anything
    ambiguous (e.g. `--global`, or a site search with no `--item`) is rejected at
    enqueue time.

Items enqueued together share one auto-generated queue name (e.g. `20260723-152525`, shown
by `--queue-list`). Most queue commands accept that name to target just one batch instead of
every queue, and a run can be further narrowed to specific item ids:

```bash
# Target one specific batch
python manual.py --queue-run 20260723-152525
python manual.py --queue-list 20260723-152525
python manual.py --queue-clear 20260723-152525

# Restrict a run to specific item ids (as shown by --queue-list)
python manual.py --queue-run --queue-ids abc12345,def67890

# Interactively pick which queue to run from a numbered list
python manual.py --queue-select
```

## Global Search

```bash
# Global search
python manual.py --global -s "cars"

# Filter by category
python manual.py --category 1    # Anime
python manual.py --category 2    # Movies & Series
python manual.py --category 3    # Series only
python manual.py --category 4    # Movies only
```
