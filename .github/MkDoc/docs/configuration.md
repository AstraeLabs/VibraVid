# Configuration

All settings live in `config.json`. The sections below cover each configuration block.
ARR automation settings are documented separately in the [ARR Integration](arr.md) guide.

## TMDB API Key

An optional [TMDB](https://www.themoviedb.org/) API key unlocks:

- The `%(tmdb_*)`, `%(original_title)`, `%(original_language)` and `%(imdb_id)` filename tokens
  (see [Movie Format](#movie-format) / [Episode Format](#episode-format) below).
- Poster/backdrop artwork (`embed_poster` in [DOWNLOAD](#download)).
- Strict TMDB/TVDB identity matching for the [ARR Integration](arr.md).

It is **not required** — without it these features degrade gracefully (tokens are dropped from
filenames, artwork/matching falls back to the site's own data).

The key is shared by every interface (CLI, TUI, GUI, ARR) — there is a single configuration
point, not a per-interface setting.

### Setting the key (current, supported method)

1. Create a key at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) (free).
2. Export it as the **`TMDB_API_KEY`** environment variable before running VibraVid:

   ```bash
   export TMDB_API_KEY=your_key_here
   python manual.py      # CLI
   python tui.py         # TUI
   python GUI/manage.py runserver 0.0.0.0:8000   # GUI
   ```

   On Windows (PowerShell): `$env:TMDB_API_KEY = "your_key_here"`.

- **Docker / NAS**: set `TMDB_API_KEY` in your `.env` file (see `.env.example`) — it's already
  wired through `docker-compose.yml` under the ARR Integration section and applies to the
  bundled GUI/ARR container.
- **GUI**: there is no in-app field for the key; it's read from the environment at process
  start, so after setting/changing it you need to restart the server (or recreate the
  container). If it's missing, search results show a "TMDB covers aren't active" hint.
- This is the **only** way to configure the key going forward — see the deprecated method below.

### Deprecated method: `Conf/login.json`

Older versions read the key from `Conf/login.json`, under a `Provider` section:

```json
{
  "Provider": {
    "tmdb": "your_key_here"
  }
}
```

This still works as a fallback if `TMDB_API_KEY` is not set, but it is **deprecated and will be
removed in a future release** — a warning is logged the first time it's used. Fresh installs'
`login.json` template does not include a `Provider` section at all, so this path only matters
for existing configs carried over from an older version; migrate to the `TMDB_API_KEY`
environment variable above.

## DEFAULT

```json
{
  "DEFAULT": {
    "debug_track_json": false,
    "log_level": "INFO",
    "close_console": true,
    "show_message": true,
    "fetch_domain_online": true,
    "auto_update_check": true,
    "disable_scraper_cache": false,
    "imp_service": ["default"],
    "installation": "",
    "get_me": false
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `close_console` | `true` | Automatically close the console after download completes |
| `debug_track_json` | `false` | Log a `TRACKS_JSON` payload with selected tracks, keys, and manifest metadata — useful for debugging stream selection |
| `log_level` | `"INFO"` | Logging verbosity. Accepts standard Python values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `show_message` | `true` | Show the startup banner and clear the console before printing it |
| `fetch_domain_online` | `true` | Automatically fetch the latest domains from GitHub |
| `auto_update_check` | `true` | Notify you at startup when a new VibraVid version is available |
| `disable_scraper_cache` | `false` | GUI only: the Django backend caches an already-instantiated site scraper per title for 15 minutes so repeat requests (e.g. opening the same series-detail page) don't re-scrape. |
| `imp_service` | `["default"]` | Service source paths to load site modules from. `"default"` loads all built-in sites. Add absolute paths to directories containing custom site modules — each must have `__init__.py` defining `indice` and `_useFor`. A GitHub/Gitea repository URL is also accepted: its archive is downloaded and cached under `.cache/imported_service/<host>__<owner>__<repo>__<ref>/`. The cache is trusted for 15 minutes; past that, only a cheap "latest commit" check is made and the archive is only re-downloaded if that commit changed. Custom modules take precedence over built-ins with the same name. |
| `installation` | `""` | Controls which bundled binaries are auto-downloaded at setup. `""` (base): FFmpeg, Velora, flux. `"yt"`: base + yt-dlp, deno. `"full"`: base + dovi_tool, mkvtoolnix, yt-dlp, deno |
| `get_me` | `false` | Resolve and print the account name in the login banner (e.g. `Login - Type: Account / User: name`) for services that support it.

**Custom `imp_service` example (local folder):**
```json
"imp_service": ["default", "/home/user/my_custom_sites"]
```

**Custom `imp_service` example (remote GitHub/Gitea repository):**
```json
"imp_service": ["default", "https://github.com/owner/my-vibravid-sites"]
```

**Custom `imp_service` example (private repository, with username/password or token):**
```json
"imp_service": ["default", "http://user:password@192.168.1.149:3000/owner/my-vibravid-sites"]
```
Embed the credentials directly in the URL userinfo, as `<scheme>://<username>:<password>@<host>/<owner>/<repo>` (GitHub: use a personal access token as the password; Gitea: username/password or a token both work). Pin a specific branch/tag with a `#ref` fragment, e.g. `https://host/owner/repo#dev`; otherwise the repository's default branch is used. Since credentials end up in plain text in `config.json`, prefer a scoped token over your account password, and keep `config.json` out of any git history you push (it's tracked in the VibraVid repo itself, so a fork/clone used for personal config should not be pushed upstream with real credentials in it).

## OUTPUT

```json
{
  "OUTPUT": {
    "root_path": "Video",
    "movie_folder_name": "Movie",
    "serie_folder_name": "Serie",
    "anime_folder_name": "Anime",
    "music_folder_name": "Music",
    "live_folder_name": "Live",
    "movie_format": "%(title_name) (%(title_year))/%(title_name) (%(title_year))",
    "episode_format": "%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)",
    "song_format": "%(album)/%(track_number:02d). %(title)"
  }
}
```

**`root_path`** — Base directory where videos are saved.
- Windows: `C:\\MyLibrary\\Folder` or `\\\\MyServer\\Share`
- Linux/macOS: `Desktop/MyLibrary/Folder`
- Docker / NAS: set the `VIBRAVID_OUTPUT_ROOT` environment variable instead of editing `config.json` — the value is applied at startup and overrides this field without touching the persisted config file. Example: `VIBRAVID_OUTPUT_ROOT=/app/Video` (container path matching the bind-mount target).

**`movie_folder_name`**, **`serie_folder_name`**, **`anime_folder_name`**, **`music_folder_name`**, **`live_folder_name`** — Subfolder names for each content type (defaults: `"Movie"`, `"Serie"`, `"Anime"`, `"Music"`, `"Live"`). All support the `%{site_name}` placeholder:

```
"Movie/%{site_name}"  ->  "Movie/Crunchyroll"
"Serie/%{site_name}"  ->  "Serie/Crunchyroll"
```

### Movie Format

**Default:** `"%(title_name) (%(title_year))/%(title_name) (%(title_year))"`

```
%(title_name) (%(title_year))/   ->  folder    Inception (2010)/
%(title_name) (%(title_year))    ->  filename  Inception (2010).mkv
```

| Variable | Description |
|----------|-------------|
| `%(title_name)` | Movie title |
| `%(title_name_slug)` | Movie title as slug |
| `%(title_year)` | Release year (omitted if unavailable) |
| `%(quality)` | Video resolution |
| `%(language)` | Audio languages |
| `%(video_codec)` | Video codec |
| `%(audio_codec)` | Audio codec |
| `%(audio_flags)` | Audio track flags, e.g. `DEFAULT` |
| `%(sub_flags)` | Subtitle track flags, e.g. `CC-SDH-FORCED` |
| `%(original_title)` | Original-language title (requires TMDB API key) |
| `%(original_language)` | Original language code, e.g. `ja` (requires TMDB API key) |
| `%(tmdb_id)` | TMDB ID (requires TMDB API key) |
| `%(tmdb_title)` | TMDB title in the configured lookup language (requires TMDB API key) |
| `%(imdb_id)` | IMDb ID, e.g. `tt0409591` (requires TMDB API key) |

### Episode Format

**Default:** `"%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"`

```
%(series_name)/     ->  series folder   Breaking Bad/
S%(season:02d)/     ->  season folder   S01/
%(episode_name)...  ->  filename        Pilot S01E05.mkv
```

| Variable | Description |
|----------|-------------|
| `%(series_name)` | Series name |
| `%(series_name_slug)` | Series name as slug |
| `%(series_year)` | Series release year |
| `%(season:FORMAT)` | Season number with inline padding (see below) |
| `%(episode:FORMAT)` | Episode number with inline padding (see below) |
| `%(episode_name)` | Episode title (sanitized) |
| `%(episode_name_slug)` | Episode title as slug |
| `%(absolute:FORMAT)` | Absolute episode number with inline padding — anime only (AnimeUnity/AnimeWorld) |
| `%(quality)` | Video resolution |
| `%(language)` | Audio languages |
| `%(video_codec)` | Video codec |
| `%(audio_codec)` | Audio codec |
| `%(audio_flags)` | Audio track flags, e.g. `DEFAULT` |
| `%(sub_flags)` | Subtitle track flags, e.g. `CC-SDH-FORCED` |
| `%(original_title)` | Original-language title (requires TMDB API key) |
| `%(original_language)` | Original language code, e.g. `ja` (requires TMDB API key) |
| `%(tmdb_id)` | TMDB ID (requires TMDB API key) |
| `%(tmdb_title)` | TMDB title in the configured lookup language (requires TMDB API key) |
| `%(tmdb_episode_title)` | TMDB episode title, resolved from `%(tmdb_id)` + season/episode number (requires TMDB API key) |
| `%(tmdb_season_number:FORMAT)` | Real TMDB season number, mapped from an absolute/flat episode number when needed (anime) — supports inline padding like `season`/`episode` (requires TMDB API key) |
| `%(tmdb_season_name)` | TMDB season name, e.g. `Season 2`, mapped the same way (requires TMDB API key) |
| `%(imdb_id)` | IMDb ID, e.g. `tt0409591` (requires TMDB API key) |

**Inline padding syntax (for `season`, `episode`, `absolute`, and `track_number` in Song Format below):**

| Token | Result (n=1) | Description |
|-------|-------------|-------------|
| `%(season:02d)` | `01` | Zero-pad to 2 digits |
| `%(season:03d)` | `001` | Zero-pad to 3 digits |
| `%(season:d)` | `1` | No padding |

> Tokens that cannot be resolved (e.g. TMDB tokens without an API key, or `%(absolute)` on non-anime services) are removed from the filename together with any surrounding `[]`/`()` wrapper, so they never leak as literal text.

### Song Format

**Default:** `"%(album)/%(track_number:02d). %(title)"`

```
%(album)/                        ->  folder    Discovery/
%(track_number:02d). %(title)    ->  filename  01. One More Time.mp3
```

| Variable | Description |
|----------|-------------|
| `%(artist)` | Artist name |
| `%(artist_slug)` | Artist name as slug |
| `%(album)` | Album name |
| `%(album_slug)` | Album name as slug |
| `%(title)` | Track title |
| `%(title_slug)` | Track title as slug |
| `%(year)` | Release year (omitted if unavailable) |
| `%(track_number:FORMAT)` | Track number with inline padding (see above) |

### Recommended configuration (with a TMDB API key)

```json
"movie_format": "%(title_name) (%(title_year)) [%(tmdb_id)]/%(title_name) (%(title_year)) [%(tmdb_title)] [%(quality)]",
"episode_format": "%(series_name) [%(tmdb_id)]/S%(tmdb_season_number:02d) - %(tmdb_season_name)/%(episode_name) - %(tmdb_episode_title) S%(season:02d)E%(episode:02d) [%(tmdb_title)] [%(quality)]",
```

## DOWNLOAD

```json
{
  "DOWNLOAD": {
    "skip_download": false,
    "auto_select": true,
    "use_curl_cffi_segments": false,
    "delay_after_download": 0,
    "thread_count": 10,
    "segment_delay_seconds": 0,
    "segment_delay_jitter_seconds": 0,
    "subtitle_resolve_workers": 4,
    "select_video": "best",
    "select_audio": "it|en",
    "select_subtitle": "it|en",
    "extract_embedded_cc": false,
    "live_max_empty_polls": 8,
    "max_token_refresh_rounds": 10,
    "token_refresh_backoff_seconds": 4.0,
    "token_refresh_stall_rounds": 3,
    "embed_poster": false,
    "cleanup_tmp_folder": true
  }
}
```

### Performance Settings

| Key | Default | Description |
|-----|---------|-------------|
| `auto_select` | `true` | Automatically select streams based on filters. When `false`, enables interactive track selection before download |
| `delay_after_download` | `0` | Delay (seconds) applied after each movie or episode download |
| `skip_download` | `false` | Skip the download step and process existing files |
| `thread_count` | `10` | Number of concurrent segment requests for a single stream |
| `subtitle_resolve_workers` | `4` | Number of HLS subtitle renditions resolved/downloaded concurrently. `1` restores the original strictly-sequential behaviour |
| `extract_embedded_cc` | `false` | HLS only: extract embedded CEA-608/708 closed captions (`EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS`, no separate subtitle file) from the downloaded video into a subtitle track. Opt-in because it requires decoding the whole video, adding extra time/CPU per download |
| `cleanup_tmp_folder` | `true` | Remove temporary files after download |
| `embed_poster` | `false` | Embed a poster/still into the downloaded file: the matching TMDB artwork if found, otherwise the site's own poster/still as a fallback |


### Segment Throttling, Live Streams & Token Refresh


| Key | Default | Description |
|-----|---------|-------------|
| `use_curl_cffi_segments` | `false` | Download segments through `curl_cffi` (TLS/JA3 fingerprint impersonation) instead of the default Velora HTTP backend. |
| `segment_delay_seconds` | `0` | Fixed delay inserted before each segment request — throttles download speed to stay under a CDN's rate limit |
| `segment_delay_jitter_seconds` | `0` | Random jitter (0 to this value, seconds) added on top of `segment_delay_seconds` so requests aren't perfectly periodic |
| `live_max_empty_polls` | `8` | Live HLS/DASH downloads only: number of consecutive polls with no new segments (or poll failures) before VibraVid concludes the live stream has ended and stops |
| `max_token_refresh_rounds` | `10` | Maximum retry rounds when segments fail mid-download (e.g. the CDN manifest token expired -> HTTP 403, or a transient 503) — VibraVid re-requests a fresh manifest/token and retries just the failed segments |
| `token_refresh_backoff_seconds` | `4.0` | Base backoff between token-refresh rounds; actual wait is `backoff × round number`, capped at 20s |
| `token_refresh_stall_rounds` | `3` | Give up early if this many consecutive refresh rounds make no progress (same or more segments still failing), instead of exhausting all `max_token_refresh_rounds` |

### Stream Selection Filters

Use `select_video`, `select_audio`, and `select_subtitle` to control which tracks are downloaded.

**Video (`select_video`):**

| Value | Description |
|-------|-------------|
| `"best"` | Best available resolution |
| `"worst"` | Worst available resolution |
| `"1080"` | Exact height (falls back to worst if not found) |
| `"1080,H265"` | Height + codec constraint |
| `"1080\|best"` | Height with fallback to best |
| `"1080\|best,H265"` | Height + codec with fallback to best |
| `"b=8000:f=best"` | Bitrate cap (kbps) — best within range. Useful when the highest-bitrate rendition isn't decryptable with your DRM device/security level |
| `"b=1000-8000:f=best"` | Bitrate range (kbps) — best within range |
| `"b=1000-:f=best"` | Bitrate floor only (no upper bound) — best within range |
| `"false"` | Skip video |

Native `key=value` filters combine multiple constraints (resolution, codec, bitrate, id, fallback) in one string, joined by `:` — keys are `r=` (resolution), `c=` (codec), `b=` (bitrate), `i=` (manifest id, regex), `f=` (fallback: `best`/`worst`/`all`), e.g. `"r=1080:c=hvc1:f=best"`.

**Audio (`select_audio`):**

| Value | Description | If not found |
|-------|-------------|--------------|
| `"best"` | Best bitrate per language | Selects best across all |
| `"worst"` | Worst bitrate per language | Selects worst across all |
| `"all"` | All audio tracks | Downloads all |
| `"default"` | Streams marked as default | DROP |
| `"non-default"` | Streams NOT marked as default | DROP |
| `"ita"` | Italian audio | DROP |
| `"ita\|it"` | Pipe-separated language codes | DROP if none found |
| `"ita,MP4A"` | Language + codec | DROP if combination not found |
| `"ita\|best"` | Language with fallback to best | Fallback to best |
| `"ita\|best,AAC"` | Language + codec with fallback | Fallback to best |
| `"b=64-192:f=best"` | Bitrate range (kbps) — best within range | Ignores range if no match |
| `"1ita\|2eng"` | Numbered slots — exclusive priority | Skips the whole download |
| `"false"` | Skip audio | — |

Same native keys as video, plus `l=` for language, e.g. `"l=ita:c=aac:f=best"` (language + codec + fallback).

**Subtitle (`select_subtitle`):**

| Value | Description |
|-------|-------------|
| `"all"` | All subtitles |
| `"default"` | Streams marked as default |
| `"non-default"` | Streams NOT marked as default |
| `"ita\|eng"` | Pipe-separated language codes |
| `"ita_forced"` | Language with flag (`forced`, `cc`, `sdh`) |
| `"ita_forced\|eng_cc"` | Multiple languages with flags |
| `"false"` | Skip subtitles |

**Companion Dolby Vision (`select_video` only):**

Add `&dv=<quality>` to the video filter to also download a Dolby Vision companion alongside the main (non-DV) video. `<quality>` is `best`/`worst` (default `worst`) or an explicit height override (e.g. `&dv=720`):

| Value | Description |
|-------|-------------|
| `"best&dv"` | Best non-DV video + DV companion at worst quality |
| `"1080&dv=best"` | 1080p main video + DV companion at best quality, matched to 1080p when available |

When `<quality>` is `best`/`worst`, the companion is picked from DV streams at the **same resolution** as the main video (falling back to the nearest available resolution if none matches exactly). An explicit height override (`&dv=720`) bypasses this matching and always targets that height directly.

The DV track is muxed as an additional video track via mkvmerge.

## PROCESS (Post-Processing)

```json
{
  "PROCESS": {
    "engine": "ffmpeg",
    "use_gpu": false,
    "param_video": ["-c:v", "libx265", "-crf", "28", "-preset", "medium"],
    "param_audio": ["-c:a", "libopus", "-b:a", "128k"],
    "param_song": [],
    "param_final": ["-c", "copy"],
    "audio_order": [],
    "subtitle_order": [],
    "subtitle_disposition_language": "it-it_forced",
    "merge_audio": true,
    "merge_subtitle": true,
    "force_subtitle": "auto",
    "extension": "mkv"
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `engine` | `"ffmpeg"` | Muxing engine used to combine video, audio and subtitle tracks. `ffmpeg` or `mkvmerge` (`mkvmerge` requires a **full installation**) |
| `use_gpu` | `false` | Enable hardware acceleration. GPU type is auto-detected at runtime: `cuda` (NVIDIA), `qsv` (Intel), `vaapi` (AMD) |
| `param_video` | H.265/HEVC | FFmpeg video encoding parameters, e.g. `["-c:v", "libx265", "-crf", "28", "-preset", "medium"]` |
| `param_audio` | Opus 128k | FFmpeg audio encoding parameters, e.g. `["-c:a", "libopus", "-b:a", "128k"]` |
| `param_song` | `[]` | FFmpeg re-encode parameters applied to downloaded music tracks (e.g. `monochrome`). |
| `param_final` | `["-c", "copy"]` | Final FFmpeg parameters. When set, takes full precedence over `param_video` and `param_audio` |
| `audio_order` | `[]` | Order of audio tracks in the output, e.g. `["ita", "eng"]` |
| `subtitle_order` | `[]` | Order of subtitle tracks in the output, e.g. `["ita", "eng"]` |
| `merge_audio` | `true` | Merge all audio tracks into a single output file |
| `merge_subtitle` | `true` | Merge all subtitle tracks into a single output file |
| `subtitle_disposition_language` | `"it-it_forced"` | Mark a specific subtitle track as default/forced |
| `extension` | `"mkv"` | Output container format: `"mkv"` or `"mp4"` |

**`force_subtitle`** — Controls how subtitles are handled before remuxing:

| Value | Behaviour |
|-------|-----------|
| `"auto"` (default) | Subtitles are renamed/converted based on their detected format. VTT files are sanitized (unmatched `<` replaced) to prevent data loss when muxed as SRT |
| `"copy"` | No conversion or renaming — the original file is muxed as-is. Also skips VTT sanitization |
| `"srt"` / `"vtt"` / `"ass"` | Force-convert all subtitles to the specified format using FFmpeg, with sanitization applied for `vtt` |

See `VibraVid/core/processors/helper/ex_sub.py` in the repository for conversion logic.

## REQUESTS

```json
{
  "REQUESTS": {
    "timeout": 15,
    "max_retry": 8,
    "verify": true,
    "use_proxy": false,
    "proxy_scope": "scrap+down",
    "proxy": {
      "http": "http://localhost:8888",
      "https": "http://localhost:8888"
    },
    "bypasser_url": "http://localhost:8192"
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `timeout` | `15` | Request timeout in seconds |
| `max_retry` | `8` | Maximum retry attempts for failed requests |
| `verify` | `true` | Verify TLS/SSL certificates on outgoing requests and segment downloads. |
| `use_proxy` | `false` | Enable proxy support for HTTP requests |
| `proxy_scope` | `scrap+down` | Where the proxy is applied: `scrap`, `down`, or `scrap+down` (see below) |
| `proxy.http` | — | Proxy URL for HTTP targets |
| `proxy.https` | — | Proxy URL for HTTPS targets |
| `bypasser_url` | `http://localhost:8192` | Endpoint of the **bypasser** sidecar that solves monochrome.tf's Cloudflare Turnstile widget for the **monochrome** Amazon Music download. **Required** — there is no in-process fallback. Keep the localhost default for local runs; in Docker the `BYPASSER_URL` env in `docker-compose.yml` overrides it to the `bypasser` service. |

**Proxy scope** — when `use_proxy` is `true`, `proxy_scope` decides *which* traffic goes through the proxy:

| Value | Effect |
|-------|--------|
| `scrap` | Only VibraVid's own HTTP client (search, metadata, manifests, DRM licenses) |
| `down` | Only the Velora download engine (media/subtitle segment downloads) |
| `scrap+down` | Both (default) |

Any invalid value falls back to `scrap+down`. Override per run from the CLI with `--proxy-scope scrap|down|scrap+down`.

**SOCKS5 support** — the `http`/`https` keys refer to the **target** URL scheme, not the proxy protocol. The value can be an HTTP **or** a SOCKS5 proxy URL. Use `socks5h://` (with the `h`) to resolve DNS through the proxy — recommended for geo-restricted sites and to avoid DNS leaks. Authentication is supported via `user:pass@`.

```json
"proxy": {
  "http":  "socks5h://localhost:1080",
  "https": "socks5h://user:pass@localhost:1080"
}
```

## DRM

```json
{
  "DRM": {
    "use_cdm": true,
    "prefer_remote_cdm": false,
    "bypass_vault_cache": false,
    "log_engine_output": false,
    "vault": {
      "vault_1": {
        "url": "https://drm-db.server66.workers.dev",
        "token": ""
      }
    }
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `use_cdm` | `true` | Enable CDM-based key extraction. When `false`, only database/vault lookups are attempted |
| `prefer_remote_cdm` | `false` | Prefer remote CDM services (see [Remote CDM Services](#remote-cdm-services) below) over local device files |
| `bypass_vault_cache` | `false` | Skip the DRM key vault lookup and force a fresh CDM license request every run, instead of reusing a previously-seen key. |
| `log_engine_output` | `false` | Log the decrypt engine's own stdout/stderr output — equivalent to the CLI's `--log-decryptor-output`. Off by default since it's verbose; useful when a decrypt is failing and you need the raw engine output |
| `vault` | — | Optional external DRM key store(s), queried before CDM extraction |

### Multiple / self-hosted DRM vaults

`vault` isn't limited to the single `vault_1` entry shown above — add as many named entries as
you want. Each configured vault is queried in the order it's declared, stopping as soon as every
requested KID has been resolved; when a key is found in one vault it's written back to every
*other* configured vault (never back to the one it came from, to avoid a pointless round-trip):

```json
"vault": {
  "vault_1": { "url": "https://drm-db.server66.workers.dev", "token": "" },
  "myvault": { "url": "https://drm.example.com", "token": "my-secret-token" }
}
```

Any name other than `vault_1` and the reserved `vault_2` is treated as a self-hosted vault
speaking the same simple REST contract as `vault_1` — implement these two Bearer-token-authenticated
JSON endpoints and VibraVid can read/write keys from it, no code changes required on the VibraVid
side:

- `POST {url}/get-keys` — body `{"kids": ["<kid_hex>", ...], "license_url": "<optional>"}` (or
  `{"license_url": ..., "pssh": ...}` for a PSSH-scoped lookup) → `{"keys": [{"kid_key": "<kid>:<key>"}, ...]}`
- `POST {url}/save-keys` — body `{"license_url": ..., "pssh": ..., "keys": [{"kid": ..., "key": ..., "label": "<optional>"}, ...]}` → `{"added": <int>}`

**Config key vs. console label**: `vault_1` and `vault_2` are the *config keys* (`vault_2` is
reserved for a JSON-RPC-protocol vault, distinct from the `vault_1`/custom REST protocol above);
each vault also carries its own internal name used in log/console output (`Bypassing cached key
... from <name>`) — the `vault_1` entry logs as `claudio`, `vault_2` logs as `lab`. A custom
entry (`myvault` above) logs under the same name you gave it in `DRM.vault`, so there's no
separate label to know about for those.

For a backend that doesn't speak this REST contract (a different transport, auth scheme, or
response shape — like `vault_2`'s JSON-RPC), see [Adding a Vault](vault.md) for how to subclass
`BaseVault` instead.

### Remote CDM Services

When remote CDM services are available, add one or both of the following blocks to `config.json`:

**Widevine:**
```json
"widevine": {
  "device_type": "ANDROID",
  "system_id": 22590,
  "security_level": 3,
  "host": "https://cdrm-project.com/remotecdm/widevine",
  "secret": "CDRM",
  "device_name": "public"
}
```

| Key | Description |
|-----|-------------|
| `device_type` | Device model: `"ANDROID"` or `"CHROME"` |
| `system_id` | Widevine system ID (default `22590` for Android) |
| `security_level` | Security level 1–3 (3 = L3) |
| `host` | Remote CDM server URL |
| `secret` | Authentication secret |
| `device_name` | Device identifier registered on the remote service |

**PlayReady:**
```json
"playready": {
  "device_name": "public",
  "security_level": 3000,
  "host": "https://cdrm-project.com/remotecdm/playready",
  "secret": "CDRM"
}
```

| Key | Description |
|-----|-------------|
| `device_name` | Device identifier registered on the remote service |
| `security_level` | Security level (e.g. `3000` for SL3000) |
| `host` | Remote CDM server URL |
| `secret` | Authentication secret |

### Local CDM Devices

To use local CDM device files instead of remote services, place them in the binary directory resolved at runtime:

- Default on Linux: `~/.local/bin/binary`
- Override with `VIBRAVID_BINARY_DIR` if you need a different path, for example `/home/user_name/.local/bin/binary`

- **Widevine:** `.wvd` file (from pywidevine)
- **PlayReady:** `.prd` file (from pyplayready)

Set `prefer_remote_cdm` to `false` and local devices will be picked up automatically.
