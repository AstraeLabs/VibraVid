# Adding a New Service

## Core flow

Everything in this section applies to every service, regardless of site or media type.

### Directory layout

```
VibraVid/services/<name>/
├── __init__.py     # CLI entry point: search(), title_search(), --url resolution
├── client.py       # auth + low-level HTTP
├── scrapper.py     # metadata fetching (title/season/episode info)
└── downloader.py   # builds DASH_Downloader / HLS_Downloader and starts it
```

This split isn't enforced by the loader — only `__init__.py` with a `search()` function is
required

### Registration (auto-discovery)

`VibraVid/services/_base/site_loader.py` scans `VibraVid/services/*/​__init__.py` at startup.
Two module-level names are **required**:

```python
indice = 900          # sort position in the CLI/GUI site list — pick a free number, the
                       # loader renumbers everything to consecutive indices on the next run
_useFor = "Film_Serie"  # one of: Anime, Film_Serie, Serie, Song, Tor
```

A module that defines `_hide = True` is loaded but excluded from CLI/GUI/ARR listings —
useful while a service is still being built.

#### Loading services from a remote repository

`imp_service` (in `Conf/config.json`, `DEFAULT.imp_service`) doesn't only accept local folder
paths — an entry can also be an **http(s) URL to a GitHub or Gitea repository** laid out the
same way as `VibraVid/services/` (one subfolder per service, each with its own `__init__.py`
defining `indice`/`_useFor`).

```json
"imp_service": ["default", "https://git.example.com/me/my-vibravid-sites"]
```

For a private repo, put credentials in the URL userinfo —
`https://user:token@host/owner/repo` — and pin a branch/tag with a `#ref` suffix
(`.../owner/repo#dev`); otherwise the repo's default branch is used.


### CLI options (`--url`, etc.)

Expose a `register_cli_args(parser) -> list[str]` function; the CLI calls it to add
site-specific flags and stores their values in `context_tracker.site_options`, keyed by
argparse `dest`:

```python
def register_cli_args(parser) -> list:
    group = parser.add_argument_group("MyService options (-i <n>)")
    group.add_argument("--url", dest="url", default=None, help="Direct title URL.")
    return ["url"]
```

Read it back in `search()`:

```python
def search(string_to_search=None, get_onlyDatabase=False, direct_item=None, selections=None, scrape_serie=None):
    if direct_item is None and not get_onlyDatabase:
        url = (context_tracker.site_options or {}).get("url")
        if url:
            direct_item = _resolve_url_to_item(url)
    return base_search(..., direct_item=direct_item, ...)
```

`base_search`/`base_process_search_result` (in `VibraVid/services/_base/site_search_manager.py`)
handle the rest — season/episode selection, table display, and dispatch to your
`download_film`/`download_series` functions. Reuse them rather than reimplementing the flow.

### Authentication (`client.py`)

There is no fixed auth pattern — services in this codebase use credential logins, static
tokens, and captured session cookies, sometimes more than one at once for different parts of
the same site. What generalizes:

- **Cache the session on disk** if login has any real cost (rate limits, a multi-step
  handshake). Use `VibraVid.utils.disk_cache` (`load`/`save`/`is_fresh`) keyed by service name,
  with an `expiry` timestamp. Skipping this
  means every CLI invocation re-authenticates, and some backends will start throttling or
  blocking logins after a handful of runs in quick succession.
- **A fresh HTTP client per request drops cookies set by a previous response.** If auth relies
  on a session cookie, capture it after login (`dict(http_client.cookies)`) and pass it
  explicitly (`cookies=...`) into every subsequent `create_client(...)` call — don't assume a
  cookie jar persists across separate `create_client` context managers.
- **A "web session" and an "app/API session" can be two unrelated login systems** even on the
  same domain. If an endpoint keeps returning empty/short-form data despite a valid-looking
  session, check whether it actually belongs to a different auth path than the one you're
  authenticating against — don't assume all endpoints on one domain share one cookie.
- For IP/region detection, reuse `VibraVid.utils.http_client.get_my_location()` (already
  cached to `.cache/ip.json`) instead of adding another geolocation call.

### Metadata (`scrapper.py`)

Populate `VibraVid.services._base.object.Episode`/`Season` (CLI path) or the GUI's
`Entries`/`Episode`/`Season` dataclasses (`GUI/searchapp/api/base.py`) with what the site
actually gives you — don't leave `image`/`slug`/`year` empty if the raw API response already
has them:

- **`image` on both search results and episodes** — the GUI's series "hero" background and
  episode thumbnails fall back to whatever `Entries.poster`/`Episode.image` are set to; an
  empty field there is a visible regression, not a cosmetic one.
- **`slug` must be a real, human-readable title slug — never an opaque content ID.** Several
  downstream lookups (TMDB year/poster resolution in particular) use `slug` as a search key;
  passing a content ID through as `slug` makes every such lookup silently miss.
- **Prefer the site's own release date/year field over a TMDB guess.** TMDB slug/name lookup
  is a *fallback* for when the site doesn't already carry the year — check the raw metadata
  response for the real field first.
- **If you genuinely can't determine the year, set it to the literal string `"9999"`** rather
  than leaving it blank — that's the sentinel `EntriesManager.add()` checks for to trigger the
  TMDB slug/name year lookup described above (falling back to the current year if no TMDB key is
  configured or the lookup fails); an empty year skips this fallback entirely (see
  `VibraVid/services/_base/object.py`).
- **If your service is for TV/series content, add its module name to `_TV_MATCH_SITES`** in
  `VibraVid/services/_base/tmdb_artwork.py` — TMDB series/episode matching (artwork, season
  names) is gated by this allowlist, so without it `resolve_series_tmdb_id()` silently returns
  `None` for your service even with a valid TMDB key configured. Movie matching has no such
  allowlist.

### Building the download (`downloader.py`)

- If a license/session token is obtainable **up front** (before the manifest is even parsed),
  prefer passing `license_url` + `license_headers` to `DASH_Downloader`/`HLS_Downloader` over a
  per-challenge `license_request_fn` callback. The CDM pipeline
  (`VibraVid/core/drm/widevine.py` / `playready.py`) already does a plain POST with your headers
  — a callback is only needed when the license request itself requires per-challenge
  server-side computation that can't be front-loaded.
- **Always pass a real, non-empty `license_url`, even when a callback does the actual license
  HTTP request itself.** The vault key-storage step keys stored keys by `license_url` — leaving
  it `None`/unset (which becomes an empty string downstream) makes that save call fail with an
  HTTP 400, silently losing the key for future reuse even though the download itself succeeds.
- If the service exposes a quality ceiling gated by DRM scheme (e.g. one scheme tops out well
  below the other), pick `drm_preference` from the user's configured target quality rather than
  hardcoding one scheme.

For anything beyond this base case — non-standard license flows, forcing a track, chapters, a
live status line, a custom manifest format, or a music/audio service — see
[Advanced / optional features](#advanced--optional-features) below.

### GUI adapter

Add `GUI/searchapp/api/<name>.py` subclassing `GenericStreamingAPI` (`GUI/searchapp/api/generic.py`):

```python
from VibraVid.services.<name>.scrapper import GetSerieInfo
from .base import Entries
from .generic import GenericStreamingAPI

class MyService(GenericStreamingAPI):
    site_name = "<name>"
    log_label = "MyService"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.id)
```

This is auto-discovered the same way as the CLI module (`GUI/searchapp/api/__init__.py` scans
the package directory) — no separate registration step, and no logo/icon asset to add anywhere:
the GUI has no per-site static image requirement. `GenericStreamingAPI.search()` and
`get_series_metadata()` already call your CLI-side `search()`/scrapper for you; only override
`_build_entry`/`_map_episode` if the default field mapping doesn't fit.

If you want your service to show up in a specific position in the GUI site list instead of just
being appended after the known ones, add its module name to `_PREFERRED_ORDER` in
`GUI/searchapp/api/__init__.py` — this is purely cosmetic ordering, not required for the service
to work. The category shown for it (Film_Serie/Anime/Serie/Song/Tor) comes straight from the
`_useFor` you already set in the CLI module's `__init__.py` — nothing extra to set on the GUI
side for that.

## Advanced / optional features

None of this is needed for a basic service. Jump to whichever subsection matches something your
site actually requires.

### License/DRM patterns

Beyond the base `license_url`/`license_headers`/`license_request_fn` case described in
[Building the download](#building-the-download-downloaderpy):

**Dynamic query string.** If your license endpoint needs a computed query string on top of the
base URL, build `license_url` as an f-string with the params baked in rather than trying to
express them as `license_headers`:

```python
license_url = (
    f"https://license.example.com/widevine"
    f"?AccountId={account_id}&ContentId={content_id}&SubContentType=Default"
)
dl = DASH_Downloader(mpd_url, license_url=license_url, license_headers={"Authorization": f"Bearer {token}"})
```

**Multiple outputs.** If resolving the license needs more than one piece of data (the URL itself
plus separate params and/or headers that must travel together), have your resolver return them
as a tuple and unpack it in `downloader.py` rather than forcing everything into one string:

```python
def generate_license_url(tracking_info: dict) -> tuple[str, dict, dict]:
    license_url = f"https://license.example.com/{tracking_info['asset_id']}"
    license_params = {"sessionId": tracking_info["session_id"]}
    license_headers = {"X-Auth-Token": tracking_info["token"]}
    return license_url, license_params, license_headers

license_url, license_params, license_headers = generate_license_url(tracking_info)
if license_params:
    license_url += "?" + urlencode(license_params)
dl = DASH_Downloader(mpd_url, license_url=license_url, license_headers=license_headers)
```

**Stateful exchange.** If the license server requires a lease that must be renewed/retried
rather than a one-shot POST, build a closure capturing whatever per-title state it needs and
pass that closure as `license_request_fn` instead of a static `license_url`/`license_headers`
pair:

```python
def _make_license_request_fn(asset_id: str, session_token: str):
    def _request(challenge: bytes, headers: dict) -> bytes:
        resp = client.post(
            f"https://license.example.com/lease/{asset_id}",
            content=challenge,
            headers={**headers, "X-Session-Token": session_token},
        )
        resp.raise_for_status()
        return resp.content
    return _request

dl = DASH_Downloader(
    mpd_url,
    license_url=f"https://license.example.com/lease/{asset_id}",  # still required, see note above
    license_request_fn=_make_license_request_fn(asset_id, session_token),
)
```

**Direct DRM manager access.** If none of the above fit and you truly need to resolve keys
yourself outside the downloader's own DRM flow, call the DRM manager directly instead of routing
through `license_url`/`license_request_fn` — but treat this as a last resort, not the default:
prefer the generic downloader-driven flow whenever the site's license endpoint behaves like a
normal POST.

```python
keys = dl.drm_manager.get_wv_keys(pssh, license_url, license_certificate, headers, key=None)
```

### Custom filter override

If you need to force a specific track (e.g. a given audio language) beyond what the standard
config-driven filters already select, set `custom_filters` directly on the downloader instance
after constructing it, rather than adding a new constructor argument:

```python
dl = DASH_Downloader(...)
dl.custom_filters = {"audio": config_manager.config.get("DOWNLOAD", "select_audio")}
dl.start()
```

### Batch downloads (albums, season packs)

If your service downloads multiple items in one run (an album, a season pack) and you don't want
one failed item to abort the whole batch, catch the per-item error yourself and report it via
`context_tracker.report_download_error(message)` / `report_download_success()`
(`VibraVid/core/ui/tracker.py`) instead of letting the exception propagate — this lets the
GUI/CLI show a partial-success summary at the end rather than failing the entire run on one bad
track:

```python
from VibraVid.core.ui.tracker import context_tracker

for item in items_to_download:
    try:
        download_track(item)
    except Exception as exc:
        logger.error(f"Track '{item.name}' failed: {exc}")
        context_tracker.report_download_error(f"{item.name}: {exc}")
        continue
    context_tracker.report_download_success()
```

### Chapters

If the site exposes chapter/scene markers, pass them as `chapters=` to `DASH_Downloader`/
`HLS_Downloader` so they land in the muxed output — don't leave this out just because the base
flow doesn't require it. Each chapter is a `{"name": str, "seconds": int}` dict.

**Markers known up front**: compute the chapter list (name + start-time pairs) before starting
the download and pass it once:

```python
chapters = [
    {"name": "Intro", "seconds": 0},
    {"name": "Main content", "seconds": 45},
    {"name": "Credits", "seconds": 1620},
]
dl = DASH_Downloader(mpd_url, chapters=chapters or None)
```

**Markers that arrive during a live/long-running download**: pass a single mutable list into
the downloader up front, then keep appending to that *same list object* from a background
polling thread as new markers show up — the downloader only reads the list once, at final mux
time, so anything appended before then is picked up automatically:

```python
chapters: list = []
dl = DASH_Downloader(mpd_url, chapters=chapters)
threading.Thread(target=_watch_and_append, args=(chapters, record_start), daemon=True).start()
dl.start()

def _watch_and_append(chapters: list, record_start: float) -> None:
    last_seen = None
    while not stop_flag.wait(POLL_INTERVAL):
        marker = poll_current_marker()
        if marker and marker != last_seen:
            last_seen = marker
            chapters.append({"name": marker, "seconds": int(time.time() - record_start)})
```

### Live status line (long-running/live downloads)

If your download can run for a long time (in particular, live recordings) and you want to
surface a status that changes over time — connection state, a counter, anything worth showing
above the progress bars — use the shared status-line mechanism instead of printing ad-hoc lines
that would scroll past the bars:

```python
from VibraVid.core.ui.bar_manager import get_bar_manager

bar_manager = get_bar_manager(download_id)
if bar_manager:
    bar_manager.set_status_text("[cyan]your rich-markup status text")
```

Call `set_status_text` again whenever the state changes — there's no polling interval built in,
the caller decides when and how often to update it. The text also lands in the log file
automatically (tagged `[msg_room]`), so don't add your own `logger.info` call for the same
status text.

### Custom manifest format

If the site doesn't expose a standard DASH/HLS manifest — only raw per-track data you'd have to
assemble yourself — don't write a bespoke downloader from scratch. Build a `vibravid_manifest`
JSON structure from the resolved track data (`VibraVid/core/manifest/custom.py` parses it) and
hand that to the existing downloader machinery instead:

```python
manifest = {
    "vibravid_manifest": True,
    "base_url": "https://cdn.example.com/movie/",
    "duration": 5400,
    "tracks": [
        {
            "type": "video", "id": "v0", "codecs": "avc1.640028",
            "bitrate": 5000000, "width": 1920, "height": 1080, "fps": "24",
            "init": {"url": "init_video.mp4"},
            "segments": [{"url": "seg_video_1.m4s"}, {"url": "seg_video_2.m4s"}],
        },
        {
            "type": "audio", "id": "a0", "codecs": "mp4a.40.2",
            "bitrate": 128000, "language": "en", "channels": "2",
            "init": {"url": "init_audio.mp4"},
            "segments": [{"url": "seg_audio_1.m4s"}, {"url": "seg_audio_2.m4s"}],
        },
    ],
}
dl = DASH_Downloader(json.dumps(manifest))
```

### Search backed by TMDB

If the site itself has no usable search endpoint (or its search results are too sparse to build
a good listing/season-episode structure from), you don't have to scrape one — use TMDB directly
as the search backend instead. Note this needs a configured TMDB API key; without one this kind
of service silently returns zero results, so don't choose this pattern as a silent fallback the
user has no way to notice is broken.

```python
from VibraVid.provider.tmdb import tmdb

def search(query: str) -> list[Entries]:
    results = []
    for movie in tmdb.search_movies(query):
        results.append(Entries(
            id=movie["id"], name=movie["title"], slug=movie["title"],
            type="movie", year=movie.get("release_date", "")[:4],
            image=f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}" if movie.get("poster_path") else None,
        ))
    for show in tmdb.search_series(query):
        results.append(Entries(
            id=show["id"], name=show["name"], slug=show["name"],
            type="tv", year=show.get("first_air_date", "")[:4],
        ))
    return results

class GetSerieInfo:
    def __init__(self, tmdb_id: str):
        self.tmdb_id = tmdb_id
        self.seasons_manager = SeasonManager()

    def _load(self):
        details = tmdb._make_request(f"tv/{self.tmdb_id}") or {}
        for raw_season in details.get("seasons", []):
            self.seasons_manager.add(Season(number=raw_season["season_number"], tmdb_id=raw_season["id"]))

    def getEpisodeSeasons(self, season_number: int) -> list[Episode]:
        season = tmdb._make_request(f"tv/{self.tmdb_id}/season/{season_number}") or {}
        return [Episode(number=e["episode_number"], name=e["name"]) for e in season.get("episodes", [])]
```

Only the final step — resolving a chosen TMDB result back to a real playable item/URL on the
site — stays site-specific and isn't shown here.

### Music services (lyrics & tagging)

If the service is audio-only (music, not film/series), the final processing step isn't video
muxing — it's `VibraVid.core.muxing.helper.audio.process_song(...)`. Route the downloaded file
through that instead of reimplementing audio post-processing.

- **Lyrics**: call `VibraVid.provider.musiclyric.get_lyrics(title, artist, album,
  duration_seconds)` rather than writing your own lyrics fetch — it already handles a
  multi-provider fallback chain and converts synced lyrics from TTML into LRC when that's the
  format returned.
- **Tagging**: populate `track_number`, `cover_url`, `album_artist`, and `lyrics` when the site
  exposes them and pass them into `process_song(...)` — same principle as `image`/`slug`/`year`
  above: don't leave a field empty just because the base flow doesn't require it.

```python
lyrics_result = None
try:
    lyrics_result = get_lyrics(title=title, artist=artist, album=album, duration_seconds=duration)
except Exception:
    logger.error(f"lyrics lookup crashed for {title!r}")

final_path = process_song(
    file_path=downloaded_path,
    title=title,
    artist=artist,
    album=album,
    track_number=track_number,
    cover_url=cover_url,
    album_artist=album_artist,
    lyrics=(lyrics_result or {}).get("lyrics"),
)
```

Note: TMDB poster/still art embedding into the final file is separate and needs no extra work
here — it's zero-touch as long as `image`/`slug`/`year` are populated per the
[Metadata](#metadata-scrapperpy) section above.