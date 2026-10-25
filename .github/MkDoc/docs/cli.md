# CLI Usage

## Basics

```bash
python manual.py -h                                                       # help + available sites
python manual.py -i streamingcommunity --search "interstellar"            # search and download
python manual.py -i streamingcommunity --search "interstellar" --item 0   # pick a result (0-based) without prompting
python manual.py -i 0 --search "interstellar"                             # site by index number
python manual.py -i streamingcommunity --search "interstellar" --skip-ts  # skip TS/CAM (StreamingCommunity only)
python manual.py -i streamingcommunity --search "interstellar" --no-log   # no log file for this run
python manual.py --dep                                                    # show dependency paths
python manual.py --migrate-config --dry-run                               # preview local JSON-to-TOML migration
```

For JSON configuration upgrades, see [Migrating from JSON](configuration.md#migrating-from-json), including backups and `--overwrite` when TOML files already exist. `python manual.py --migrate-config --help` lists migration options without starting the application.

Site index numbers follow the services that are switched on: services listed in [`hidden_services`](configuration.md#default) are not loaded and the others are renumbered without gaps, so check `-h` for the current numbers (names never change).

## Series and year

`--season` / `--episode` skip the prompts. Values: a number, a range `"1-5"`, or `"*"` for all.

```bash
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season 1 --episode 3
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season 1 --episode "1-5"
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season 1 --episode "*"   # whole season
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season "*"               # all seasons
python manual.py -i streamingcommunity --search "breaking bad" --item 0 --season "1-3"

python manual.py -i streamingcommunity --search "dune" --year 2021          # exact year
python manual.py -i streamingcommunity --search "batman" --year "1990-2015" # range
```

## Track selection and output

```bash
python manual.py ... -sv 1080                  # video
python manual.py ... -sa "eng"                 # audio language (long form: --audio-langs)
python manual.py ... -ss "eng"                 # subtitles (long form: --sub-langs)
python manual.py ... -ss "ita_forced"          # only the forced subtitle track
python manual.py ... -sa "deu" --skip-no-match # skip the whole download if the filter matches no track
python manual.py ... --extension mp4           # container (overrides PROCESS.extension for this run)
```

Full filter syntax: [Stream selection filters](configuration.md#stream-selection-filters).

## Console, proxy, progress

```bash
python manual.py --close-console false                                # keep the console open (loop mode)
python manual.py -i ... --close-console true                          # close it after the download

python manual.py -i ... --use_proxy                                   # configured proxy, default scope
python manual.py -i ... --use_proxy --proxy-scope down                # only downloads (Velora)
python manual.py -i ... --use_proxy --proxy-scope scrap               # only scraping

python manual.py -i ... --plain-progress                              # one line per track every 5s, e.g.
#   Vid [H.264] 720p 2.4 Mbps  28%  290.2M / 1012.7M  65.27M/s  ETA 00:11
python manual.py -i ... --plain-progress --progress-interval 2        # every 2s
python manual.py -i ... --no-progress                                 # no progress output
```

## Metadata sidecars

`.nfo` + artwork for Jellyfin/Emby/Kodi, only for a reliable match; off by default (`DOWNLOAD.write_sidecars`). See [Metadata providers](configuration.md#metadata-providers).

```bash
python manual.py -i ... --sidecars                                    # TMDB, then IMDb
python manual.py -i ... --sidecars --metadata-provider imdb           # IMDb only, no key
python manual.py -i ... --sidecars --metadata-provider tvdb,tmdb      # needs TVDB_API_KEY or Provider.tvdb
python manual.py -i ... --no-sidecars                                 # off for this run
```

## Direct download (`--down`)

Download from a URL, skipping site search. Type is auto-detected (MP4 / HLS / DASH / ISM) or forced with `--type`.

```bash
# MP4 / auto-detected
python manual.py --down "https://example.com/video.mp4" -o "./Video/clip.mp4"

# HLS with a known key
python manual.py --down "https://example.com/master.m3u8" --type hls --key "<KID>:<KEY>" -o "./Video/movie.mkv"

# DASH + Widevine license server (--license-headers adds headers for the license request; repeatable like --headers)
python manual.py --down "https://example.com/manifest.mpd" --type dash \
  --license-url "https://example.com/wv/license" --drm widevine \
  --headers "Authorization: Bearer <token>" --license-headers "Authorization: Bearer <token>" -o "./Video/movie.mkv"

# Only a clip: segments 10-50, or a time range
python manual.py --down "https://example.com/master.m3u8" --type hls --max-segments "10-50" -o "./Video/clip.mkv"
python manual.py --down "https://example.com/master.m3u8" --type hls --max-time "00:01:00-00:05:00" -o "./Video/clip.mkv"
```

### Custom JSON manifest

`--down` also takes a `.json` file/URL containing `"vibravid_manifest"` (auto-detected, no `--type`) for sources without a real manifest:

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
```

| `segments` mode | Fields | Behaviour |
|---|---|---|
| `list` | URLs, or objects with `url`/`size`/`duration`/`range` | explicit list |
| `template` | `template`, `start`, `step` (or `duration`), `end` (or `count`) | DASH-style `$Number$` / `$Time$` / `$RepresentationID$` expansion |
| `ranges` / `chunk` | `url` + `ranges` array, or `chunk` + `size` to auto-split | byte ranges of a single file |

`init` is a URL string, an object (`url`, `range`) or inline base64 (`data`). Placeholders support zero-padding (`$Number%05d$`); `$$` is a literal `$`.

### Export and import (`--export`, `--import`)

`--export [FILE]` resolves the tracks and keys like a normal run but **does not download**: it writes the result to a JSON file (default `.cache/export/<site>_<type>_<title>.json`). `--import FILE` downloads every entry of such a file, in sequence, with no search and (when the keys were exported) no CDM. Both work for MP4, HLS, DASH and ISM, from `--down` or from a service search (`-i SITE -s QUERY --export`).

```bash
python manual.py --down "https://example.com/master.m3u8" --key "<KID>:<KEY>" --export ./movie.json
python manual.py --import ./movie.json              # add -o to pick the output name (single entry)
```

### Metadata for a direct download (`--meta-*`)

A plain `--down` has no title context, so title-dependent hooks and Vault upload/lookup are skipped. `--meta-*` attaches it (`--resolve-only` sets these automatically):

```bash
python manual.py --down "https://example.com/master.m3u8" --type hls --key "<KID>:<KEY>" \
  -o "./Video/Movie (2024)/movie.mkv" --meta-title "Movie Name" --meta-type Film --meta-site streamingcommunity

python manual.py --down "..." --type hls --key "<KID>:<KEY>" -o "./Video/Show/Season 01/episode.mkv" \
  --meta-title "Show Name" --meta-type TV --meta-season 1 --meta-episode 3
```

## yt-dlp (`--yt-dlp`)

Any site yt-dlp supports (separate from `--down`, which is for HLS/DASH/ISM/MP4 URLs).

```bash
python manual.py --yt-dlp "https://www.youtube.com/watch?v=..." -o "./Video/clip.mp4"
python manual.py --yt-dlp "<URL>" --extract-audio --audio-format mp3 --audio-quality 0
```

| Flag | Effect |
|---|---|
| `--yt-dlp <URL>` | Download with yt-dlp instead of the site-search/`--down` flow |
| `--format <SPEC>` | Format selector (default `bestvideo+bestaudio/best`) |
| `--list-formats` | List formats and exit |
| `--interactive-format` | Show formats and prompt for the format ID |
| `--extract-audio` | Audio only |
| `--audio-format <FORMAT>` | `mp3`, `m4a`, `wav`, `opus`, ... |
| `--audio-quality <QUALITY>` | `0`, `5`, `8k`, ... |
| `--playlist-end <N>` | Only the first N playlist entries |
| `--sub-langs <LANGS>` | Subtitle languages (`it,en`); same flag as `-ss` |
| `--write-subs` / `--write-auto-subs` | Write subtitles / auto-generated subtitles |

## Advanced options

| Flag | Effect |
|---|---|
| `--use-curl-cffi` | Download segments via curl_cffi (browser TLS impersonation) instead of Velora, for Cloudflare-protected segments |
| `--http-version {1.1,2,3}` | Force the HTTP version |
| `-o`, `--output <PATH>` | Output path for `--down` / `--yt-dlp` |
| `--clear` | Delete the cached login/session tokens of the service given with `-i` (requires `-i`; never touches other services or `login.toml`) |
| `--key <KID:KEY>` | Hex decryption key, repeatable. With `--down` or a service run (`-i 0 --key <KID>:<KEY>`): used instead of the vault/CDM for the KIDs it covers, other KIDs resolve normally |
| `--no-vault-cache` | Bypass the vault cache; fresh CDM license request every run |
| `--cdm <CDM>` | CDM for this run, above `login.toml` and `config.toml`: a `.wvd`/`.prd` name, `search`, or a remote CDM id. Repeatable, one per DRM system (`--cdm mine.wvd --cdm my_playready`). A JSON list/table is accepted for [quality rules](configuration.md#choosing-the-cdm-cdm). Unknown file/id stops the run before downloading |
| `--no-decrypt` | Debug: no decryption at all (in-flight or post-download) |
| `--no-livemux` / `--livemux` | Disable / force the streaming-mux fast path (off by default: turn it on with `process` in `login.toml` or `PROCESS.use_live_mux`) |
| `--no-live-decrypt` | Disable in-flight (per-segment) decryption for this run: segments are decrypted after the download. It is on only with `process.live_decrypt` / `DOWNLOAD.use_live_decrypt` ([`process`](configuration.md#pipeline-switches-for-a-service-process) in `login.toml`, `DOWNLOAD.use_live_decrypt`) |
| `--no-concurrent` | Download video, audio and subtitles sequentially (concurrent by default) |
| `--skip-no-match` | Skip the download if `-sv`/`-sa`/`-ss` match no track, instead of falling back to the best |
| `--log-decryptor-output` | Write flux's stdout/stderr to the log, tagged with the engine name (e.g. `[FLUX]`) |
| `--abc` | Anonymize printed KID:KEY pairs (alternating characters masked with `?`) |
| `--hls-method AES_128\|NONE` | Override the HLS encryption method, ignoring `#EXT-X-KEY` (or supplying it). `NONE` = clear segments; `AES_128` = AES-128-CBC (pair with `--hls-key`/`--hls-iv`) |
| `--hls-key <HEX\|BASE64\|FILE>` | Raw AES-128 key instead of fetching the manifest's `URI=` |
| `--hls-iv <HEX>` | IV instead of the manifest's `IV=0x...` (or the implicit per-segment IV) |
| `--skip-content-check` | Skip the HEAD content-type check for MP4 downloads (`--type mp4`), for single-use URLs |
| `--skip-sanitize` | Use `-o` verbatim (no transliteration of non-ASCII characters) |
| `--no-manifest-info` | Don't print the manifest/streams table |
| `--binary-update` | Check FFmpeg/Flux/Packager/dovi_tool/MKVToolNix/Velora against AstraeLabs/Binary and re-download outdated ones (the CLI also does it every 2 days: `auto_update_binaries`) |
| `--resolve-only` | Resolve and cache manifest/keys/license/playlist without downloading (pairs with the queue) |
| `--export [FILE]` | Resolve tracks and keys, write them to a JSON file instead of downloading (see [Export and import](#export-and-import-export-import)) |
| `--import <FILE>` | Download every entry of a file written by `--export`, no search and no CDM when the keys were exported |
| `--tui` | Launch the Textual terminal UI |
| `-UP`, `--update` | Auto-update to the latest release (binary builds only) |
| `--check-update` | Print how to update for your installation (binary, source, Docker) and exit |
| `--skip-update-check` | Skip the background startup check (cached 24h; failures retried after 30 min); `auto_check_update = false` in `config.toml` turns it off for good |
| `--version` | Print the version and exit |

```bash
# Manual AES-128 override (manifest without its own #EXT-X-KEY)
python manual.py --down "https://example.com/master.m3u8" --type hls --hls-method AES_128 \
  --hls-key "0011223344556677889900112233445566" --hls-iv "00000000000000000000000000000001" -o "./Video/clip.mkv"
```

### Default options for a service

`python manual.py -i <service> --help` lists a service's options; `args` in `login.toml` sets permanent defaults for them ([details](configuration.md#default-options-for-a-service-args)).

## Download queue (`--queue-*`)

```bash
python manual.py -i streamingcommunity --search "interstellar" --item 0 --queue-add   # queue instead of downloading
python manual.py --down "https://example.com/movie.mkv" -o "./Video/movie.mkv" --queue-add
python manual.py --queue-list
python manual.py --queue-remove <ID>
python manual.py --queue-clear
python manual.py --queue-run                       # process every pending/interrupted item in order
```

!!! note
    Only invocations that complete without any prompt can be queued; ambiguous ones (`--global`, a site search without `--item`) are rejected when enqueued.

Items enqueued together share an auto-generated queue name (e.g. `20260723-152525`, shown by `--queue-list`). Pass it to target one batch:

```bash
python manual.py --queue-run 20260723-152525       # also --queue-list / --queue-clear <name>
python manual.py --queue-run --queue-ids abc12345,def67890   # only these item ids (see --queue-list)
python manual.py --queue-select                    # pick the queue from a numbered list
```

## Global search

```bash
python manual.py --global -s "cars"
python manual.py --category 1    # Anime   (2: Movies & Series, 3: Series only, 4: Movies only)
```
