# Configuration

Settings live in `Conf/config.toml` (one section per block below). ARR settings: [ARR Integration](arr.md).

## Migrating from JSON

Upgrading from a version that used `config.json` and `login.json` does not load those files automatically. Run the local migration command in the new version:

```bash
python manual.py --migrate-config --dry-run
python manual.py --migrate-config
```

The installed `vibravid` command and `python -m VibraVid` accept the same options. To migrate another directory, use `--migrate-config /path/to/Conf`; otherwise the normal configuration directory is used, including `VIBRAVID_BASE_PATH`.

If the upgrade already created TOML files, add `--overwrite` to both commands. This explicitly rebuilds each migrated TOML file from local defaults and your JSON settings; review any edits made only in TOML first. Only files with a matching JSON source are migrated. Missing options retain the template defaults.

Migration preserves hooks, maps `extra_args` to `args`, renames `cs_chrome` to `netflix`, fixes the old NOW TV cookie key, and converts cookie/token objects to their TOML representation. Inline remote CDMs move to the named registry; the report flags their changed fallback behavior for review. Unknown settings are retained with a review message; removed options, incompatible types, null values, and conflicting renamed keys are reported without printing credential values.

The command keeps the JSON sources and creates a private `migration-<timestamp>-<unique suffix>` directory inside `Conf` with copies of every source and replaced TOML file. To undo a migration, stop VibraVid and restore the saved TOML files from that directory (or remove newly created TOML files). Keep backups private: they contain your credentials.

Migration runs before application startup, never executes hooks, and makes no network requests. It uses the local default templates shipped with the checkout, installed package, or Docker image. `--defaults-dir /path/to/templates` selects a separate local directory containing `config.toml` and `login.toml`, useful if your checkout's `Conf` files were edited or damaged. Restart the GUI/container after migration so it reloads the files. See [Docker migration](docker.md#migrating-existing-json-configuration) for container commands.

## TMDB API Key

Optional. A free [TMDB key](https://www.themoviedb.org/settings/api) unlocks the `%(tmdb_*)`, `%(original_title)`, `%(original_language)` and `%(imdb_id)` filename tokens, poster/backdrop artwork (`embed_poster`) and strict TMDB/TVDB matching in [ARR](arr.md). Without it the tokens are dropped from filenames and artwork/matching falls back to the site's own data. One key serves CLI, TUI, GUI and ARR.

Set the **`TMDB_API_KEY`** environment variable before running:

```bash
export TMDB_API_KEY=your_key_here        # PowerShell: $env:TMDB_API_KEY = "your_key_here"
python manual.py                         # CLI (python tui.py / python GUI/manage.py runserver 0.0.0.0:8000)
```

- **Docker / NAS**: set it in `.env` (see `.env.example`).
- **GUI**: no in-app field; the key is read at process start, so restart the server/container after changing it. Without it, search shows a "TMDB covers aren't active" hint.
- **Deprecated**: `[Provider] tmdb = "..."` in `Conf/login.toml` still works as a fallback (logs a warning) but will be removed.
- `login.toml` can hold secrets: don't commit your copy.

## Metadata providers

With `write_sidecars` ([DOWNLOAD](#download)) VibraVid writes the files Jellyfin, Emby and Kodi read (Plex ignores them) next to each download. Fields a provider lacks are left out.

| Media | Files |
|-------|-------|
| Film | `<name>.nfo`, `<name>-poster.jpg`, `<name>-fanart.jpg` |
| Episode | `<name>.nfo`, `<name>-thumb.jpg` (episode still) |
| Series | `tvshow.nfo`, `poster.jpg`, `fanart.jpg`, `season01-poster.jpg` in the show folder (parent of the season folder); written once, only missing parts are added later |

| Provider | Film | Episode | Series |
|----------|------|---------|--------|
| **TMDB** | title, original title, year, premiere date, rating + votes, tagline, plot, runtime, age rating (IT, else US), genres, keywords, countries, studios, collection, directors, writers, 15 actors (role, photo), YouTube trailer, poster, fanart | title, air date, plot, runtime, rating + votes, directors, writers, guest stars, still | title, original title, year, status, rating + votes, plot, age rating, runtime, genres, networks, 15 actors, poster, fanart, season posters |
| **IMDb** | same, no fanart (trailer is an IMDb page) | same, from the episode's own record | same, no fanart or season posters |
| **TVDB** | same, no rating/votes | title, air date, plot, runtime, still | no rating; poster, fanart, cast, networks, age rating, status |

`metadata_provider` ([DEFAULT](#default)) is one name or an ordered list; the first provider with a reliable match wins, unavailable ones (no key) are skipped.

| Provider | Key | Language | Notes |
|----------|-----|----------|-------|
| `tmdb` | `TMDB_API_KEY` ([above](#tmdb-api-key)) | Italian | Best matching; only one that remaps anime absolute numbering |
| `imdb` | none | English | Italian titles are found only if the site supplies a TMDB id **and** a TMDB key is set |
| `tvdb` | `TVDB_API_KEY` | Italian when translated | Needs a [TheTVDB key](https://thetvdb.com/api-information) |

```bash
python manual.py ... --metadata-provider tmdb,imdb   # default
python manual.py ... --metadata-provider imdb        # no key needed
```

**TVDB key**: the `TVDB_API_KEY` environment variable (wins; Docker/NAS: `.env`), or in `Conf/login.toml` (a supported location, unlike TMDB's):

```toml
[Provider]
tvdb = "your_key_here"
```

## Default options for a service (`args`)

Every service section in `Conf/login.toml` has an `args` key (empty by default), added to **every** download of that service and written like on the [command line](cli.md):

```toml
[nowtvit]
args = "--quality 1080 --range HDR10"

[chili]
args = "--country es"
```

## Pipeline switches for a service (`process`)

A service section of `Conf/login.toml` switches two parts of the download pipeline on or off for that service only, without touching the service itself. **Without a `process` line both are off** (the old `_live_mux` flag of the services is no longer read):

```toml
[disney]
process = { live_mux = true, live_decrypt = true }
```

| Switch | `true` | `false` or unset |
|--------|--------|------------------|
| `live_mux` | Streaming-mux fast path: the final `.mkv` is written while downloading | Tracks are muxed after the download |
| `live_decrypt` | In-flight (per-segment) decryption | Segments are decrypted after the download (slower). The streaming mux then only runs when no selected track is encrypted |

A switch missing from `process` falls back to the global one in `config.toml` (`PROCESS.use_live_mux`, `DOWNLOAD.use_live_decrypt`, both `false` by default), so services without a `login.toml` section (e.g. `raiplay`) follow `config.toml`. For a single run: `--livemux` forces the streaming mux, `--no-livemux` and `--no-live-decrypt` switch it off. Order: command line, then `process` in `login.toml`, then `config.toml`. A JSON string (`process = '{"live_mux": false}'`) or a `[disney.process]` table work too.

## DEFAULT

```toml
[DEFAULT]
log_level = "INFO"
close_console = true
show_message = true
fetch_domain_online = true
auto_check_update = true
auto_update_binaries = true
disable_scraper_cache = false
imp_service = ["default"]
hidden_services = []
installation = ""
wvd_dir = ""
prd_dir = ""
metadata_provider = "tmdb,imdb"
tui_images = "auto"
```

| Key | Default | Description |
|-----|---------|-------------|
| `close_console` | `true` | Close the console after the download |
| `log_level` | `"INFO"` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `show_message` | `true` | Show the startup banner and clear the console first |
| `fetch_domain_online` | `true` | Fetch the latest domains from GitHub |
| `disable_scraper_cache` | `false` | GUI only: disable the 15-minute per-title scraper cache |
| `imp_service` | `["default"]` | Where to load site modules from: `"default"` (built-ins), local directories (each module needs `__init__.py` defining `indice` and `_useFor`), or a GitHub/Gitea repo URL. Custom modules override built-ins of the same name |
| `hidden_services` | `[]` | Services to switch off completely, by folder name (e.g. `["aurora", "tubitv"]`): they are not loaded, so they disappear from the CLI menu and `-i`, the TUI and the GUI, and ARR / the watchlist skip them. The remaining services are renumbered without gaps, but the `indice` in the service files is never touched. The `VIBRAVID_HIDDEN_SITES` env var (comma-separated, see `docker-compose.yml`) adds to this list. Saved from the GUI Settings page it applies without a restart |
| `auto_check_update` | `true` | Check for a new VibraVid release at start (24 h cache): the CLI notice, the TUI System screen and the GUI banner. `false` makes no automatic request; `--check-update` and the Check for Updates buttons still check |
| `auto_update_binaries` | `true` | CLI only: every 2 days the managed binaries (the ones in the `installation` level) are checked against AstraeLabs/Binary and updated at start. `--binary-update` and the TUI/GUI buttons always check |
| `installation` | `""` | Binaries downloaded at setup. `""`: FFmpeg, Velora, flux. `"yt"`: + yt-dlp, deno. `"full"`: + dovi_tool, mkvtoolnix, yt-dlp, deno |
| `wvd_dir` | `""` | Folder searched first for Widevine `.wvd` devices (sub-folders included). Empty: `wvd` inside the binary directory (e.g. `C:\binary\VibraVid\wvd`, see [Local CDM devices](#local-cdm-devices)) |
| `prd_dir` | `""` | Same for PlayReady `.prd` devices. Empty: `prd` inside the binary directory (e.g. `C:\binary\VibraVid\prd`) |
| `metadata_provider` | `tmdb,imdb` | Sidecar providers, tried in order (`tmdb`, `imdb`, `tvdb`). CLI: `--metadata-provider` |
| `tui_images` | `auto` | TUI cover art: `auto`, `sixel`, `kitty` (real images, see [TUI](tui.md#cover-art)), `blocks` (drawn with characters, works anywhere) or `off` |

```toml
imp_service = ["default", "/home/user/my_custom_sites"]                         # local folder
imp_service = ["default", "https://github.com/owner/my-vibravid-sites"]         # repository
imp_service = ["default", "http://user:password@192.168.1.149:3000/owner/repo"] # private repo
imp_service = ["default", "https://github.com/owner/my-vibravid-sites#dev"]      # branch, tag or commit
```

## OUTPUT

```toml
[OUTPUT]
root_path = "Video"
movie_folder_name = "Movie"
serie_folder_name = "Serie"
anime_folder_name = "Anime"
music_folder_name = "Music"
live_folder_name = "Live"
movie_format = "%(title_name) (%(title_year))/%(title_name) (%(title_year)) [%(quality)]"
episode_format = "%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d) [%(quality)]"
song_format = "%(album)/%(track_number:02d). %(title)"
tag_format = "[VibraVid]"
```

- **`root_path`**: base directory. Windows: `C:\\MyLibrary\\Folder` or `\\\\MyServer\\Share`; Linux/macOS: `Desktop/MyLibrary/Folder`. Docker/NAS: set `VIBRAVID_OUTPUT_ROOT` (e.g. `/app/Video`) instead; it overrides this field at startup without touching `config.toml`.
- **`movie_folder_name`**, **`serie_folder_name`**, **`anime_folder_name`**, **`music_folder_name`**, **`live_folder_name`**: subfolder per content type (defaults `"Movie"`, `"Serie"`, `"Anime"`, `"Music"`, `"Live"`); all accept `%{site_name}`, e.g. `"Movie/%{site_name}"` -> `Movie/Crunchyroll`.

### Format tokens

Defaults: `movie_format` = `"%(title_name) (%(title_year))/%(title_name) (%(title_year))"`, `episode_format` = `"%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"` (e.g. `Breaking Bad/S01/Pilot S01E05.mkv`). `/` separates folders from the filename.

| Token | Movie | Episode | Description |
|-------|:-----:|:-------:|-------------|
| `%(title_name)`, `%(title_name_slug)` | x | | Movie title (slug) |
| `%(title_year)` | x | | Release year (omitted if unavailable) |
| `%(series_name)`, `%(series_name_slug)` | | x | Series name (slug) |
| `%(series_year)` | | x | Series release year |
| `%(season:FORMAT)`, `%(episode:FORMAT)` | | x | Season / episode number with padding |
| `%(episode_name)`, `%(episode_name_slug)` | | x | Episode title (sanitized / slug) |
| `%(absolute:FORMAT)` | | x | Absolute episode number, anime only (AnimeUnity/AnimeWorld) |
| `%(quality)`, `%(language)` | x | x | Video resolution, audio languages |
| `%(video_codec)`, `%(audio_codec)` | x | x | Codecs |
| `%(audio_flags)`, `%(sub_flags)` | x | x | Track flags, e.g. `DEFAULT`, `CC-SDH-FORCED` |
| `%(fps)`, `%(video_bitrate)` | x | x | Frame rate and average video bit rate of the muxed file, e.g. `23.976fps`, `1212kbps` |
| `%(hdr)`, `%(bit_depth)` | x | x | `HDR10`, `HDR10+`, `HLG` or `DV` (empty for SDR); video bit depth, e.g. `10bit` |
| `%(audio_channels)` | x | x | Channels of the first audio track, e.g. `2.0`, `5.1` |
| `%(original_title)`, `%(original_language)` | x | x | Original title / language code (e.g. `ja`) (TMDB key) |
| `%(tmdb_id)`, `%(tmdb_title)`, `%(imdb_id)` | x | x | TMDB id, TMDB title in the lookup language, IMDb id e.g. `tt0409591` (TMDB key) |
| `%(tmdb_episode_title)` | | x | TMDB episode title, from `tmdb_id` + season/episode (TMDB key) |
| `%(tmdb_season_number:FORMAT)` | | x | Real TMDB season number, mapped from absolute/flat numbers when needed (anime) (TMDB key) |
| `%(tmdb_season_name)` | | x | TMDB season name, e.g. `Season 2`, mapped the same way (TMDB key) |

**Padding** (`season`, `episode`, `absolute`, `tmdb_season_number`, `track_number`): `%(season:02d)` -> `01`, `%(season:03d)` -> `001`, `%(season:d)` -> `1`.

Tokens that can't be resolved (no TMDB key, `absolute` on non-anime) are removed together with any surrounding `[]`/`()`.

**`song_format`** (default `"%(album)/%(track_number:02d). %(title)"` -> `Discovery/01. One More Time.mp3`): `%(artist)`, `%(artist_slug)`, `%(album)`, `%(album_slug)`, `%(title)`, `%(title_slug)`, `%(year)` (omitted if unavailable), `%(track_number:FORMAT)`.

**`tag_format`** (default `"[VibraVid]"`): prefix written in the `title` and `comment` tags (`[VibraVid] Inception`), both by the final retag and the merge `-metadata` flags. `""` disables tagging; any other text replaces the prefix.

Recommended with a TMDB key:

```toml
movie_format = "%(title_name) (%(title_year)) [%(tmdb_id)]/%(title_name) (%(title_year)) [%(tmdb_title)] [%(quality)]"
episode_format = "%(series_name) [%(tmdb_id)]/S%(tmdb_season_number:02d) - %(tmdb_season_name)/%(episode_name) - %(tmdb_episode_title) S%(season:02d)E%(episode:02d) [%(tmdb_title)] [%(quality)]"
```

## DOWNLOAD

```toml
[DOWNLOAD]
skip_download = false
auto_select = true
use_curl_cffi_segments = false
delay_after_download = 0
thread_count = 5
max_speed_mbps = 0
segment_delay_seconds = 0
segment_delay_jitter_seconds = 0
subtitle_resolve_workers = 4
select_video = "best"
select_audio = "ita|eng"
select_subtitle = "ita|eng"
extract_embedded_cc = false
live_max_empty_polls = 8
max_token_refresh_rounds = 10
token_refresh_backoff_seconds = 4.0
token_refresh_stall_rounds = 3
embed_poster = false
write_sidecars = false
cleanup_tmp_folder = true
keep_tmp_on_error = false
temp_root = ""
```

| Key | Default | Description |
|-----|---------|-------------|
| `auto_select` | `true` | Select streams from the filters; `false` = interactive track selection |
| `delay_after_download` | `0` | Seconds to wait after each movie/episode |
| `skip_download` | `false` | Skip downloading and process existing files |
| `thread_count` | `5` | Concurrent segment requests per stream |
| `max_speed_mbps` | `0` | Rate cap in MB/s per stream (video/audio separately); `0` = unlimited. CLI: `--max-speed` |
| `subtitle_resolve_workers` | `4` | HLS subtitle renditions resolved concurrently; `1` = sequential |
| `extract_embedded_cc` | `false` | HLS only: extract embedded CEA-608/708 captions (`EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS`) into a subtitle track. Opt-in: needs decoding the whole video (extra time/CPU) |
| `cleanup_tmp_folder` | `true` | Remove temporary files afterwards |
| `keep_tmp_on_error` | `false` | Keep the temporary files (.dec, partial chunks, ...) when a download fails, for debugging |
| `temp_root` | `""` | Folder for the temporary files (segments, merge and decrypt leftovers). Empty = next to the output file. With the output on a NAS share, point it to a fast local disk: every step otherwise reads and writes the whole title over the network (about 8x the file size). Needs about 2x the size of one title. The `VIBRAVID_TEMP_DIR` environment variable overrides it (the Docker image sets it to `/app/tmp`) |
| `embed_poster` | `false` | Embed a poster/still: matching TMDB artwork, else the site's own |
| `write_sidecars` | `false` | Write `.nfo` + poster/thumb next to each download when the match is reliable (see [Metadata providers](#metadata-providers)). CLI: `--sidecars` / `--no-sidecars` |
| `use_live_decrypt` | `false` | In-flight (per-segment) decryption while downloading, for every service without its own `process` line in `login.toml`. `false` = decrypt after the download (per run: `--no-live-decrypt`) |
| `use_curl_cffi_segments` | `false` | Download segments via `curl_cffi` (TLS/JA3 impersonation) instead of Velora |
| `segment_delay_seconds` | `0` | Fixed delay before each segment request (CDN rate limits) |
| `segment_delay_jitter_seconds` | `0` | Random 0..N seconds added on top of the delay |
| `live_max_empty_polls` | `8` | Live HLS/DASH: consecutive polls without new segments (or failures) before the stream is considered ended |
| `max_token_refresh_rounds` | `10` | Retry rounds when segments fail mid-download (expired token -> 403, transient 503): fresh manifest/token, retry only failed segments |
| `token_refresh_backoff_seconds` | `4.0` | Wait = `backoff x round`, capped at 20s |
| `token_refresh_stall_rounds` | `3` | Give up after this many consecutive rounds without progress |

### Stream selection filters

`select_video`, `select_audio` and `select_subtitle` choose which tracks are downloaded. Audio and subtitle tracks are also written in the order the filter names their languages (`"ita|eng"` puts Italian first; the slots `"1ita|2eng"` rank by number), and the tracks it does not name go last. The first audio track is the default one.

**Video**

| Value | Description |
|-------|-------------|
| `"best"` / `"worst"` | Best / worst resolution |
| `"hybrid"` | Best HDR (non-DV) base + worst Dolby Vision companion |
| `"1080"` | Exact height (falls back to worst) |
| `"1080,H265"` | Height + codec |
| `"1080\|best"`, `"1080\|best,H265"` | Same, falling back to best |
| `"b=8000:f=best"` | Bitrate cap (kbps); useful when the top rendition isn't decryptable with your CDM level |
| `"b=1000-8000:f=best"`, `"b=1000-:f=best"` | Bitrate range / floor only |
| `"false"` | Skip video |

Native `key=value` filters combine constraints with `:`: `r=` resolution, `c=` codec, `b=` bitrate, `i=` manifest id (regex), `f=` fallback (`best`/`worst`/`all`), e.g. `"r=1080:c=hvc1:f=best"`.

**Audio**

| Value | Description | If not found |
|-------|-------------|--------------|
| `"best"` / `"worst"` | Best / worst bitrate per language | Best / worst across all |
| `"all"` | All tracks | Downloads all |
| `"default"` / `"non-default"` | Streams marked / not marked default | DROP |
| `"ita"` | Italian | DROP |
| `"ita\|it"` | Pipe-separated languages | DROP if none |
| `"ita,MP4A"` | Language + codec | DROP |
| `"ita\|best"`, `"ita\|best,AAC"` | Language (+ codec) with fallback | Falls back to best |
| `"b=64-192:f=best"` | Bitrate range (kbps) | Ignores range |
| `"1ita\|2eng"` | Numbered slots, exclusive priority | Skips the whole download |
| `"false"` | Skip audio | n/a |

Same native keys as video plus `l=` language, e.g. `"l=ita:c=aac:f=best"`.

**Subtitle**: `"all"`, `"default"`, `"non-default"`, `"ita|eng"` (languages), `"ita_forced"` (flags `forced`, `cc`, `sdh`), `"ita_forced|eng_cc"`, `"false"`.

## CODEC

```toml
[CODEC]
dv_auto = true
```

`dv_auto` (default `true`): when `select_video` picks a non-DV video and the title ships a DV variant, also pair the DV companion (at the cheapest quality) even without `select_video="hybrid"`. `false` = only pair with `hybrid`, and DV streams compete like any other track (`best` may pick a DV one).

## PROCESS

```toml
[PROCESS]
engine = "ffmpeg"
use_gpu = false
param_video = ["-c:v", "libx265", "-crf", "28", "-preset", "medium"]
param_audio = ["-c:a", "libopus", "-b:a", "128k"]
param_song = []
param_final = ["-c", "copy"]
subtitle_disposition_language = "it-it_forced"
merge_audio = true
merge_subtitle = true
force_subtitle = "auto"
mux_dtsx = false
extension = "mkv"
```

| Key | Default | Description |
|-----|---------|-------------|
| `engine` | `"ffmpeg"` | Muxer: `ffmpeg` or `mkvmerge` (needs a **full installation**) |
| `use_live_mux` | `false` | Streaming-mux fast path (writes the `.mkv` while downloading), for every service without its own `process` line in `login.toml` (per run: `--livemux` / `--no-livemux`) |
| `use_gpu` | `false` | Hardware acceleration, auto-detected: `cuda` (NVIDIA), `qsv` (Intel), `vaapi` (AMD) |
| `param_video` | H.265 | FFmpeg video encoding parameters |
| `param_audio` | Opus 128k | FFmpeg audio encoding parameters |
| `param_song` | `[]` | FFmpeg re-encode parameters for music tracks |
| `param_final` | `["-c", "copy"]` | Final FFmpeg parameters; when set, takes precedence over `param_video`/`param_audio` |
| `merge_audio`, `merge_subtitle` | `true` | Merge all audio / subtitle tracks into the output file |
| `subtitle_disposition_language` | `"it-it_forced"` | Subtitle track to mark default/forced |
| `mux_dtsx` | `false` | Keep DTS:X/Atmos-lossless audio instead of dropping it at selection (ffmpeg can't demux DTS:X: needs mkvmerge) |
| `extension` | `"mkv"` | Output container: `"mkv"` or `"mp4"` (CLI overrides per run) |
| `force_subtitle` | `"auto"` | Subtitle handling before remux (below) |

| `force_subtitle` | Behaviour |
|------------------|-----------|
| `"auto"` | Rename/convert by detected format; VTT is sanitized (unmatched `<` replaced) to avoid data loss when muxed as SRT |
| `"copy"` | Mux the original file as-is (no conversion, no VTT sanitization) |
| `"srt"` / `"vtt"` / `"ass"` | Force-convert all subtitles with FFmpeg (sanitization applied for `vtt`) |

Conversion logic: `VibraVid/core/processors/helper/ex_sub.py`.

## REQUESTS

```toml
[REQUESTS]
timeout = 15
max_retry = 4
verify = true
use_proxy = false
proxy_scope = "scrap+down"

[REQUESTS.proxy]
http = "http://localhost:8888"
https = "http://localhost:8888"
```

| Key | Default | Description |
|-----|---------|-------------|
| `timeout` | `15` | Request timeout (seconds) |
| `max_retry` | `4` | Retries for failed requests |
| `verify` | `true` | Verify TLS certificates (requests and segment downloads) |
| `use_proxy` | `false` | Enable the proxy |
| `proxy_scope` | `scrap+down` | `scrap` (VibraVid's HTTP client: search, metadata, manifests, licenses), `down` (Velora segment downloads) or `scrap+down`. Invalid values fall back to `scrap+down`. CLI: `--proxy-scope` |
| `proxy.http`, `proxy.https` | - | Proxy URL for HTTP / HTTPS targets (the key is the *target* scheme, not the proxy protocol) |

The proxy can be HTTP or SOCKS5. Use `socks5h://` to resolve DNS through the proxy (geo-restricted sites, no DNS leaks); `user:pass@` is supported:

```toml
[REQUESTS.proxy]
http  = "socks5h://localhost:1080"
https = "socks5h://user:pass@localhost:1080"
```

## DRM

```toml
[DRM]
use_cdm = true
cdm = "search"
bypass_vault_cache = false
log_engine_output = false

[DRM.vault.default]
url = "https://drm-db.server66.workers.dev"
token = ""
```

| Key | Default | Description |
|-----|---------|-------------|
| `use_cdm` | `true` | CDM key extraction; `false` = only vault lookups |
| `cdm` | `"search"` | Default CDM for services without their own (see [Choosing the CDM](#choosing-the-cdm-cdm)) |
| `bypass_vault_cache` | `false` | Skip the vault lookup and request a fresh license every run |
| `log_engine_output` | `false` | Log the decrypt engine's stdout/stderr (verbose). CLI: `--log-decryptor-output` |
| `vault` | - | External key store(s), queried before CDM extraction |

### Local vault

```toml
[DRM.vault.local]
enabled = true
```

Every key is also saved in a SQLite file per service, `.cache/services/<service>/vault.db` (table `keys`: `kid`, `key`, `license_url`, `pssh`, `created_at`, `synced_at`), and looked up there first. A `kid` + license + `pssh` already stored is never overwritten. Nothing is written while a remote request is served.

### Multiple / self-hosted DRM vaults

One `[DRM.vault.<name>]` table per vault, queried in declaration order until every KID is resolved.

```toml
[DRM.vault.myvault]
url = "https://drm.example.com"
token = "..."
protocol = "custom"

[DRM.vault.query_vault]
url = "https://keys.example.net/"
username = "user"
password = "..."
protocol = "query"
```

| `protocol` | Request | Writes |
|---|---|---|
| `custom` | `POST {url}/get-keys` / `POST {url}/save-keys` (the `default` vault's contract) | yes |
| `api` | `GET {url}/{service}/{kid}`, Bearer token | only with `save_keys` |
| `json` | `POST {url}` `{"method", "params", "token"}` (JSON-RPC) | only with `save_keys` |
| `query` | `GET {url}?service=&kid=&username=&password=` | no |

- `protocol` is optional: auto-detected once and cached in `.cache/services/vault/universal_<name>.json` (delete to re-detect).
- `default` (old name `vault_1`) is the built-in vault, logged as `claudio`. Resolved keys show `kid:key | *claudio [1]` (vault name + position).
- New keys are written back to `custom` vaults, and to `api`/`json` ones only with `save_keys = true` (default `false`).
- When flux proves a stored key wrong, the vault that returned it gets the corrected key (one key per KID, warning counter reset). The default vault keeps the old one in `drm_keys_replaced`.
- Vaults that publish a service list are queried under the matching service, then `generic`; others under `generic`.
- A vault answering `429` is skipped for the requested `retry_after`.
- Optional fields: `service` (force a namespace), `timeout` (seconds, default `10`).

`Conf/config.toml` is tracked by git: don't commit third-party vault tokens.

### Remote CDMs

Declare once under `[DRM.remote_cdm.<id>]`; the id is free text (not `search`/`default`) and is what you put in `cdm`.

```toml
[DRM.remote_cdm.my_widevine]
device_type = "ANDROID"
system_id = 22590
security_level = 3
host = "https://cdrm-project.com/remotecdm/widevine"
secret = "CDRM"
device_name = "public"

[DRM.remote_cdm.my_playready]
device_name = "public"
security_level = 3000
host = "https://cdrm-project.com/remotecdm/playready"
secret = "CDRM"
```

| Key | Widevine | PlayReady | Description |
|-----|:--------:|:---------:|-------------|
| `device_type` | x | | `"ANDROID"` or `"CHROME"` |
| `system_id` | x | | Widevine system ID (default `22590` for Android) |
| `security_level` | x | x | Widevine 1-3; PlayReady e.g. `3000` for SL3000 |
| `host` | x | x | Remote CDM server URL |
| `secret` | x | x | Authentication secret |
| `device_name` | x | x | Device registered on the remote service |

The system (Widevine/PlayReady) is inferred: `device_type` or `type = "custom_api_1"` = Widevine, a `device_name` starting with `SL` = PlayReady, no `device_type` = PlayReady. Set `system = "widevine"` / `"playready"` to force it.

### Hosted remote CDM API

A third-party hosted CDM (Widevine and PlayReady) over HTTP: same `[DRM.remote_cdm.<id>]` table with `type = "custom_api_1"`, plus `host` and `secret` from your provider.

```toml
[DRM.remote_cdm.hosted_widevine]
type = "custom_api_1"
host = "YOUR_PROVIDER_API_URL"
secret = "YOUR_PROVIDER_API_KEY"
device_name = "L3"
```

| Key | Default | Description |
|-----|---------|-------------|
| `type` | - | Must be `"custom_api_1"` (otherwise a standard pywidevine/pyplayready remote CDM) |
| `host`, `secret` | required | Provider API URL and key |
| `device_name` | `ChromeCDM` | Widevine: `ChromeCDM`, `L1`, `L2`, `L3`. PlayReady: `SL2`, `SL3` |
| `auth_header` | `x-api-key` | Header the provider expects the key in |
| `service_name` | - | Optional service tag |

### Choosing the CDM (`cdm`)

`cdm` picks the CDM that extracts keys. Set it in `[DRM]` of `config.toml` (default for all services) and/or in a service section of `login.toml` (that service only). Each system (Widevine, PlayReady) uses exactly one CDM at a time: a local device file or a remote CDM.

| `cdm` entry | Meaning |
|-------------|---------|
| `"search"` | First `.wvd` (Widevine) and `.prd` (PlayReady) in the binary folder ([Local CDM devices](#local-cdm-devices)) |
| ends with `.wvd` / `.prd` | That file, looked up in `wvd/` or `prd/` |
| `"default"` | `login.toml` only: use `[DRM]`'s `cdm` (`cdm = ""` means the same) |
| anything else | Id of a remote CDM from `DRM.remote_cdm` |

The value is one entry or a list with one entry per system. Per system the order is: service `cdm` in `login.toml`, then `[DRM]`, then `"search"` (a service naming only a `.wvd` keeps the `[DRM]` choice for PlayReady). A missing file or unknown id stops the download with an error listing the known ids. The CLI `--cdm` ([cli](cli.md)) wins over both for one run.

```toml
# config.toml: default for everyone
[DRM]
cdm = ["my_widevine", "my_playready.prd"]

# login.toml: this service uses a local Widevine file, PlayReady stays as in [DRM]
[mysite]
cdm = "mine.wvd"
```

**CDM by video quality**: a list entry can be a table limited to a height range, with `min_height` / `max_height` (inclusive, both optional) and a `cdm` (any entry above, or a list). Per system the first matching entry wins; an entry without limits always matches, so put it last. The height compared is the tallest selected video (0 for audio only), since one manifest uses one license session. No match falls through to the next level (`[DRM]`, then `"search"`).

```toml
[mysite]
cdm = [
  { min_height = 1080, cdm = "my_l1_remote" },
  { cdm = "device_l3.wvd" },
]
```

**Which CDM was used**: each key request prints one line: device file name (no path) with `SysID` (`.wvd`) or `Model` (`.prd`), or `remote` + device name, and why it was picked. The security level and full path go to the log file.

```
[+] device_l3.wvd | SysID: 22594 | why: search
[+] tv_sl3000.prd | Model: TPV AOC 50U6185 | why: config.toml [DRM]
[+] remote L1 | why: login.toml 'mysite', min_height 1080
```

| After `why:` | Meaning |
|--------------|---------|
| `--cdm` | Command line |
| `login.toml '<service>'` (+ `, min_height 1080` for quality rules) | The service's own `cdm` |
| `config.toml [DRM]` | `[DRM]`'s default `cdm` |
| `search` | First device file in the binary folder |

**Startup check**: every `cdm` (`--cdm`, each service in `login.toml`, `[DRM]`) is validated at startup regardless of height. A missing file, unknown remote id, reserved id (`search`, `default`) or malformed quality rule is warned right away. A wrong `--cdm` stops the run; mistakes in the files only warn (non-DRM downloads need no CDM).

!!! note
    `prefer_remote_cdm` and the inline `[DRM.widevine]` / `[DRM.playready]` blocks no longer exist: declare the remote CDM under `[DRM.remote_cdm.<id>]` and select it with `cdm`.

### Local CDM devices

Put device files in the binary directory (override with `VIBRAVID_BINARY_DIR`, e.g. `/home/user_name/.local/bin/binary`):

| Platform | Binary directory |
|----------|------------------|
| Windows | `C:\binary\VibraVid` |
| Linux | `~/.local/bin/binary/VibraVid` |
| macOS | `~/Applications/binary/VibraVid` |

- `tools/`: executables (ffmpeg, yt-dlp, velora, flux, ...) and `.versions.json`
- `wvd/`: **Widevine** `.wvd` files (pywidevine)
- `prd/`: **PlayReady** `.prd` files (pyplayready)

Downloaded binaries are only used from `tools/`; `.wvd`/`.prd` files left in the old flat location (`C:\binary`, `~/.local/bin/binary`, `~/Applications/binary`) are still found. With `cdm = "search"` the first `.wvd`/`.prd` is used; to keep several, name one, e.g. `cdm = "mine.wvd"`.
