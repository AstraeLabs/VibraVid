# Adding a New Service

## Core flow

### Directory layout

```
VibraVid/services/<name>/
├── __init__.py     # CLI entry point: search(), title_search(), --url resolution
├── client.py       # auth + low-level HTTP
├── scrapper.py     # metadata (title/season/episode info)
└── downloader.py   # builds DASH_Downloader / HLS_Downloader and starts it
```

The split isn't enforced: only `__init__.py` with a `search()` function is required.

### Registration (auto-discovery)

`VibraVid/services/_base/site_loader.py` scans `VibraVid/services/*/__init__.py` at startup. Two module-level names are **required**:

```python
indice = 900            # position in the CLI/GUI site list; pick a free number, the loader renumbers on the next run
_useFor = "Film_Serie"  # Anime, Film_Serie, Live, Serie, Song or Tor
```

To keep a service out of the CLI, TUI and GUI while you build it, list its folder name in `hidden_services` ([configuration](configuration.md#default)); it is not loaded at all until you remove it.

The folder name must be a valid Python identifier if any code references it with a literal import (`from VibraVid.services.<name>.scrapper import X`). The loader itself works with any name; for other names resolve submodules dynamically with `resolve_service_submodule(module_name, submodule)` (`VibraVid/services/_base/site_loader.py`), see the GUI adapter below.

#### Loading services from a remote repository

An `imp_service` entry can be a GitHub/Gitea repository URL laid out like `VibraVid/services/` (one subfolder per service, each `__init__.py` defining `indice`/`_useFor`). Private repos, `#ref` pinning and caching: see [`imp_service`](configuration.md#default).

### CLI options (`--url`, etc.)

Expose `register_cli_args(parser) -> list[str]`; the CLI adds the flags and stores their values in `context_tracker.site_options`, keyed by argparse `dest`:

```python
def register_cli_args(parser) -> list:
    group = parser.add_argument_group("MyService options (-i <n>)")
    group.add_argument("--url", dest="url", default=None, help="Direct title URL.")
    return ["url"]
```

Read it in `search()`:

```python
def search(string_to_search=None, get_onlyDatabase=False, direct_item=None, selections=None, scrape_serie=None):
    if direct_item is None and not get_onlyDatabase:
        url = (context_tracker.site_options or {}).get("url")
        if url:
            direct_item = _resolve_url_to_item(url)
    return base_search(..., direct_item=direct_item, ...)
```

Users can set a default for these options with `args = "--url ..."` in the service's `login.toml` section ([details](configuration.md#default-options-for-a-service-args)). Nothing to do in the service: `context_tracker.site_options` already contains them, and a command-line flag wins.

`base_search` / `base_process_search_result` (`VibraVid/services/_base/site_search_manager.py`) handle season/episode selection, the results table and dispatch to your `download_film` / `download_series`. Reuse them.

### Authentication (`client.py`)

No fixed pattern (credential logins, static tokens, session cookies, sometimes several per site). What generalizes:

- **Cache the session on disk** if login is costly (rate limits, multi-step handshake): `VibraVid.utils.disk_cache` (`load`/`save`/`is_fresh`), keyed by service name, with an `expiry` timestamp. Otherwise every CLI run re-authenticates and backends may throttle or block.
- **A fresh HTTP client per request drops cookies** from earlier responses. Capture the session cookie after login (`dict(http_client.cookies)`) and pass `cookies=...` to every later `create_client(...)`.
- **A "web session" and an "app/API session" can be unrelated logins** on the same domain. If an endpoint returns empty/short data despite a valid session, check whether it belongs to a different auth path.
- For IP/region use `VibraVid.utils.http_client.get_my_location()` (cached in `.cache/ip.json`).

### Metadata (`scrapper.py`)

Fill `VibraVid.services._base.object.Episode`/`Season` (CLI) or the GUI's `Entries`/`Episode`/`Season` dataclasses (`GUI/searchapp/api/base.py`) with everything the site gives you:

- **`image` on search results and episodes**: the GUI hero background and episode thumbnails fall back to `Entries.poster`/`Episode.image`; empty is a visible regression.
- **`slug` must be a human-readable title slug, never an opaque content ID**: TMDB year/poster lookups use it as the search key and silently miss otherwise.
- **Prefer the site's own release year over a TMDB guess**; the TMDB slug/name lookup is only a fallback.
- **If the year is unknown, use the string `"9999"`**: `EntriesManager.add()` treats it as the trigger for the TMDB year lookup (current year if no key or no match). An empty year skips the fallback (`VibraVid/services/_base/object.py`).
- **TV/series services: add the module name to `_TV_MATCH_SITES`** in `VibraVid/services/_base/tmdb_artwork.py`. Series/episode TMDB matching (artwork, season names) is gated by it; without it `resolve_series_tmdb_id()` returns `None` even with a key. Movies have no allowlist.

### Building the download (`downloader.py`)

- If a license/session token is available **before** the manifest is parsed, pass `license_url` + `license_headers` to `DASH_Downloader`/`HLS_Downloader` instead of a `license_request_fn` callback. The CDM pipeline (`VibraVid/core/drm/widevine.py` / `playready.py`) already POSTs with your headers; use a callback only when the request needs per-challenge server-side computation.
- **Always pass a non-empty `license_url`, even when a callback does the request.** The vault stores keys by `license_url`; `None`/empty makes the save fail with HTTP 400 and the key is lost for reuse (the download still succeeds).
- If DRM scheme limits quality (one scheme tops out below the other), choose `drm_preference` from the user's target quality instead of hardcoding.

Non-standard license flows, forced tracks, chapters, status line, custom manifests and music services: [Advanced / optional features](#advanced-optional-features).

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

Auto-discovered like the CLI module (`GUI/searchapp/api/__init__.py` scans the package); no registration and no logo asset needed. `search()` and `get_series_metadata()` already call your CLI-side `search()`/scrapper; override `_build_entry`/`_map_episode` only if the default mapping doesn't fit.

If `<name>` isn't a valid identifier, resolve the submodule dynamically (see `GUI/searchapp/api/appletv.py`):

```python
from VibraVid.services._base.site_loader import resolve_service_submodule

class MyService(GenericStreamingAPI):
    site_name = "3dservice"

    def _build_scraper(self, media_item: Entries):
        scrapper = resolve_service_submodule("3dservice", "scrapper")
        return scrapper.GetSerieInfo(media_item.id)
```

To pin a position in the GUI site list instead of being appended, add the module name to `_PREFERRED_ORDER` in `GUI/searchapp/api/__init__.py` (cosmetic only). The category comes from `_useFor`.

## Advanced / optional features

None of this is needed for a basic service.

### License/DRM patterns

Beyond the base `license_url` / `license_headers` / `license_request_fn` case ([Building the download](#building-the-download-downloaderpy)):

**Dynamic query string.** Bake computed params into `license_url` instead of `license_headers`:

```python
license_url = (
    f"https://license.example.com/widevine"
    f"?AccountId={account_id}&ContentId={content_id}&SubContentType=Default"
)
dl = DASH_Downloader(mpd_url, license_url=license_url, license_headers={"Authorization": f"Bearer {token}"})
```

**Multiple outputs.** When the license needs URL, params and headers together, return a tuple and unpack it in `downloader.py`:

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

**Stateful exchange.** For a lease that must be renewed/retried, pass a closure as `license_request_fn`:

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
    license_url=f"https://license.example.com/lease/{asset_id}",  # still required
    license_request_fn=_make_license_request_fn(asset_id, session_token),
)
```

**Direct DRM manager access.** Last resort, only if you must resolve keys outside the downloader's DRM flow:

```python
keys = dl.drm_manager.get_wv_keys(pssh, license_url, license_certificate, headers, key=None)
```

**Which CDM the service will use.** The user picks it with `cdm` ([Configuration](configuration.md)); never open a `.wvd` / `.prd` yourself. To know what you're dealing with (e.g. Chrome vs Android identity), ask the core:

```python
from VibraVid.setup import resolve_service_cdm_info

widevine = resolve_service_cdm_info("myservice", height=1080).get("widevine")
# None                       -> no Widevine CDM
# {"remote": False, "path", "type": "CHROME" | "ANDROID", "security_level", ...}   local .wvd
# {"remote": True, "name", "type": "CHROME" | "ANDROID", "security_level"}         remote CDM
# {"path", "error"}          -> the .wvd could not be read
```

`height` is the quality you are about to request; it selects the `min_height` / `max_height` rule.

### Custom filter override

To force a track (e.g. an audio language) beyond the config filters, set `custom_filters` on the downloader instead of adding a constructor argument:

```python
dl = DASH_Downloader(...)
dl.custom_filters = {"audio": config_manager.config.get("DOWNLOAD", "select_audio")}
dl.start()
```

### Custom ad-skip (`ad_decision_fn`)

For sites that stitch ads (SSAI bumpers/creatives) into the manifest, keep the ad knowledge in the service. `DASH_Downloader` / `HLS_Downloader` / `ISM_Downloader` take a decision callback as `ad_decision_fn`; the pipeline applies it symmetrically to video and audio (no desync), and "keep everything" is the answer on any doubt:

```python
# VibraVid/services/<name>/ads.py (stdlib only, no core imports, unit-testable)
def my_ad_decision(manifest_kind: str, content: str | None, ctx: dict | None = None) -> dict:
    if manifest_kind == "dash":
        ...  # parse the MPD, find ad Periods
        return {"drop_period_idx": [1, 3], "reason": "video-only bumpers"}
    if manifest_kind == "hls":
        ...  # parse the child playlist, find ad ranges/blocks
        return {"drop_seg_idx": [133, 134], "keep_init_urls": ["https://cdn.../init-main.mp4"]}
    if manifest_kind == "ism":
        ...  # scan StreamIndex Name/Url for creative markers
        return {"drop_url_substrings": ["preroll"]}
    return {}
```

Decision shapes (all optional; `{}` keeps everything):

- DASH: `{"drop_period_idx": [...], "reason": str}`, dropped from **every** stream; same-language audio continuations split across periods are rejoined.
- HLS: `{"keep_init_urls": [...]}` (MAP-block level) and/or `{"drop_seg_idx": [...]}` (playlist-order indices, per rendition).
- ISM: `{"drop_url_substrings": [...]}` (matched against segment URLs) and/or `{"drop_seg_idx": [...]}` (timeline indices).

Pass it only on the stitched path:

```python
from .ads import my_ad_decision

dl = DASH_Downloader(mpd_url, ..., ad_decision_fn=my_ad_decision)
dl.start()
```

### Batch downloads (albums, season packs)

So one failed item doesn't abort the batch, catch per-item errors and report them with `context_tracker.report_download_error(message)` / `report_download_success()` (`VibraVid/core/ui/tracker.py`); GUI/CLI then show a partial-success summary:

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

Pass chapter markers as `chapters=` to `DASH_Downloader` / `HLS_Downloader`; each is `{"name": str, "seconds": int}`.

**Known up front:**

```python
chapters = [
    {"name": "Intro", "seconds": 0},
    {"name": "Main content", "seconds": 45},
    {"name": "Credits", "seconds": 1620},
]
dl = DASH_Downloader(mpd_url, chapters=chapters or None)
```

**Arriving during a live/long download:** pass one mutable list and keep appending to that same object from a background thread; the downloader reads it once, at final mux time.

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

To show a changing status above the progress bars (connection state, a counter), use the shared status line instead of printing lines that scroll past the bars:

```python
from VibraVid.core.ui.bar_manager import get_bar_manager

bar_manager = get_bar_manager(download_id)
if bar_manager:
    bar_manager.set_status_text("[cyan]your rich-markup status text")
```

Call it whenever the state changes (no built-in polling). The text is also logged as `[msg_room]`, so don't add your own `logger.info` for it.

### Custom manifest format

If the site has no standard DASH/HLS manifest, don't write a bespoke downloader: build a `vibravid_manifest` from the resolved tracks (format: [Custom JSON manifest](cli.md#custom-json-manifest); parser: `VibraVid/core/manifest/custom.py`) and hand it to the existing downloaders:

```python
manifest = {"vibravid_manifest": True, "base_url": "https://cdn.example.com/movie/", "duration": 5400, "tracks": [...]}
dl = DASH_Downloader(json.dumps(manifest))
```

### Search backed by TMDB

If the site has no usable search (or too sparse to build listings and season/episode structure), use TMDB as the search backend. It needs a TMDB API key; without one the service returns zero results silently, so don't use it as a hidden fallback.

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
        results.append(Entries(id=show["id"], name=show["name"], slug=show["name"],
                               type="tv", year=show.get("first_air_date", "")[:4]))
    return results
```

For series, build seasons/episodes in `GetSerieInfo` from `tmdb._make_request(f"tv/{id}")` and `tv/{id}/season/{n}`. Resolving a chosen result to a playable item/URL on the site stays site-specific.

### Music services (lyrics & tagging)

For audio-only services the final step is `VibraVid.core.muxing.helper.audio.process_song(...)`, not video muxing.

- **Lyrics**: `VibraVid.provider.musiclyric.get_lyrics(title, artist, album, duration_seconds)` has a multi-provider fallback chain and converts synced TTML lyrics to LRC.
- **Tagging**: pass `track_number`, `cover_url`, `album_artist` and `lyrics` to `process_song(...)` when the site exposes them.

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

TMDB poster/still embedding is separate and automatic once `image`/`slug`/`year` are filled per [Metadata](#metadata-scrapperpy).

Users can install and remove your service from the GUI: see [Custom service upload](gui.md#custom-service-upload).
