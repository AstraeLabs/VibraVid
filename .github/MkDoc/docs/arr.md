# ARR Integration

The `ARR` block enables VibraVid to work as an automation layer between **Seerr/Jellyseerr**, **Sonarr**, **Radarr**, and the final media library. When enabled, VibraVid polls Sonarr/Radarr for missing media, receives webhook events, downloads through its provider pipeline, and reports the resulting files back so that Sonarr/Radarr can import them.

!!! important
    The ARR integration requires the VibraVid web GUI to be running. All polling loops,
    webhook listeners, and download workers are managed by the Django application server.
    The CLI (`vibraNid` / `python -m VibraVid`) does not start the ARR stack.

Typical flow:

```text
Seerr / Jellyseerr
    ↓ user requests a movie or series
Sonarr / Radarr
    ↓ media is added and marked as missing
VibraVid ARR
    ↓ detects missing media through polling or webhooks
VibraVid downloader
    ↓ searches and downloads through the configured provider or through the provider selected by tag
Sonarr / Radarr
    ↓ rescans/imports the downloaded file
Media library
    ↓ Jellyfin/Plex can detect the final file
```

## Supported integrations

| Service | Role |
|---------|------|
| **Seerr/Jellyseerr** | Handles user requests and sends approval/pending webhook events |
| **Sonarr** | Manages TV series, missing episodes, episode metadata, rescans and imports |
| **Radarr** | Manages movies, missing movie metadata, rescans and imports |
| **VibraVid** | Searches, downloads and hands files back to Sonarr/Radarr |

## Configuration reference

```json
{
  "ARR": {
    "enabled": false,
    "enable_polling": false,
    "enable_seerr_webhook": false,
    "enable_sonarr_webhook": false,
    "enable_radarr_webhook": false,
    "polling_interval": 300,
    "full_resync_interval": 21600,
    "max_concurrent_downloads": 1,
    "webhook_priority_enabled": true,
    "native_webhook_priority_window_seconds": 120,
    "seerr_fallback_delay_seconds": 20,
    "download_italian_anime_default": true,
    "provider_fallback": ["streamingcommunity", "animeunity"],
    "path_mapping": {},
    "sonarr": { "url": "", "api_key": "" },
    "radarr": { "url": "", "api_key": "" },
    "seerr": { "webhook_secret": "" },
    "sonarr_webhook": { "webhook_secret": "" },
    "radarr_webhook": { "webhook_secret": "" }
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enables the ARR integration globally |
| `enable_polling` | `false` | Periodically scans Sonarr/Radarr for missing media |
| `enable_seerr_webhook` | `false` | Enables the Seerr/Jellyseerr webhook endpoint |
| `enable_sonarr_webhook` | `false` | Enables the Sonarr native webhook endpoint |
| `enable_radarr_webhook` | `false` | Enables the Radarr native webhook endpoint |
| `polling_interval` | `300` | Seconds between incremental polling cycles |
| `full_resync_interval` | `21600` | Seconds between full reconciliation syncs |
| `max_concurrent_downloads` | `1` | Maximum parallel ARR-triggered downloads |
| `webhook_priority_enabled` | `true` | Native Sonarr/Radarr webhooks take priority over Seerr to avoid duplicates |
| `native_webhook_priority_window_seconds` | `120` | Dedup window for near-simultaneous webhook events |
| `seerr_fallback_delay_seconds` | `20` | Delay before processing a Seerr event when a native webhook may follow |
| `download_italian_anime_default` | `true` | When an anime provider returns both an original and an `(ITA)` dubbed version, prefer the Italian dub |
| `provider_fallback` | `[]` | Ordered list of providers tried in sequence when the primary provider finds no match. If empty, `streamingcommunity` is the built-in default |
| `path_mapping` | `{}` | Translates VibraVid host paths to the paths seen by Radarr/Sonarr containers. Leave empty when both services share the same filesystem view |
| `sonarr.url` | — | Base URL of the Sonarr instance, e.g. `http://sonarr:8989` |
| `sonarr.api_key` | — | Sonarr API key |
| `radarr.url` | — | Base URL of the Radarr instance, e.g. `http://radarr:7878` |
| `radarr.api_key` | — | Radarr API key |
| `seerr.webhook_secret` | — | Secret expected in Seerr/Jellyseerr webhook requests |
| `sonarr_webhook.webhook_secret` | — | Secret expected in Sonarr webhook requests |
| `radarr_webhook.webhook_secret` | — | Secret expected in Radarr webhook requests |

!!! tip
    All ARR settings can also be edited directly from the VibraVid web GUI under
    **Settings -> Configuration editor**, without touching `config.json` manually.

These keys can also be set via environment variables (useful in Docker):

```bash
USE_ARR_SERVICES=true
SONARR_URL=http://sonarr:8989
SONARR_API_KEY=your-key
RADARR_URL=http://radarr:7878
RADARR_API_KEY=your-key
SEERR_WEBHOOK_SECRET=your-secret
SONARR_WEBHOOK_SECRET=your-secret
RADARR_WEBHOOK_SECRET=your-secret

# Less commonly needed, but also read from the environment:
ENABLE_ARR_POLLING=true               # enable_polling
ARR_POLLING_INTERVAL=300              # polling_interval (seconds)
ARR_FULL_RESYNC_INTERVAL=21600        # full_resync_interval (seconds)
ENABLE_SEERR_WEBHOOK=true             # enable_seerr_webhook
ENABLE_SONARR_WEBHOOK=true            # enable_sonarr_webhook
ENABLE_RADARR_WEBHOOK=true            # enable_radarr_webhook
ARR_MAX_CONCURRENT_DOWNLOADS=1        # max_concurrent_downloads
ARR_WEBHOOK_PRIORITY_ENABLED=true     # webhook_priority_enabled
ARR_NATIVE_WEBHOOK_PRIORITY_WINDOW_SECONDS=120  # native_webhook_priority_window_seconds
ARR_SEERR_FALLBACK_DELAY_SECONDS=20   # seerr_fallback_delay_seconds
```

Each of these mirrors the identically-named `ARR.*` key in `config.json` (comment shows the
matching key) — set either the env var or the config key, not both.

!!! tip
    Strict TMDB/TVDB identity matching (used when resolving Sonarr/Radarr items) requires a
    TMDB API key — see [TMDB API Key](configuration.md#tmdb-api-key) for setup.

## Webhook setup

VibraVid exposes one webhook endpoint per ARR application. Add **one connection only** per app — adding multiple webhooks for the same app causes duplicate processing.

When the GUI/Django server is running, the ARR integration exposes dedicated webhook endpoints:

```text
POST /api/arr/webhook/seerr/
POST /api/arr/webhook/sonarr/
POST /api/arr/webhook/radarr/
```

Each endpoint can be protected with its own webhook secret. Configure the same secret both in VibraVid and in the corresponding external service.

**Radarr** -> Settings -> Connect -> Webhook

| Field | Value |
|-------|-------|
| URL | `http://<vibravid-host>:<port>/api/arr/webhook/radarr/` |
| Triggers | On Movie Added, On Movie File Delete |
| Secret | any value mirrored in `radarr_webhook.webhook_secret` |

**Sonarr** -> Settings -> Connect -> Webhook

| Field | Value |
|-------|-------|
| URL | `http://<vibravid-host>:<port>/api/arr/webhook/sonarr/` |
| Triggers | On Series Add, On Episode File Delete |
| Secret | any value mirrored in `sonarr_webhook.webhook_secret` |

Then enable in `config.json`:
```json
"enable_sonarr_webhook": true,
"enable_radarr_webhook": true
```

!!! note
    Webhooks and polling can be used together: webhooks trigger an immediate download when
    media is added, polling acts as a safety net for items that arrive without a webhook event.

## Manual and automatic sync

ARR can process media in two ways:

1. **Polling** — VibraVid periodically asks Sonarr/Radarr for wanted or missing media.
2. **Webhooks** — VibraVid reacts immediately when Seerr, Sonarr or Radarr sends an event.

Both modes can be enabled together. Native Sonarr/Radarr webhooks can be prioritized over Seerr events to reduce duplicate processing.

## Provider selection

VibraVid determines which provider to use for each item through two mechanisms — you can use one or both.

**Method 1 — Per-item tag (advanced, requires tagging each title)**

Add a tag directly to the movie or series in Sonarr/Radarr using the format `provider-<site>`. VibraVid reads the tag at download time and uses that provider regardless of the fallback list. This is useful when specific titles are only available on a particular service.

Tags are created in Sonarr/Radarr under Settings -> Tags, then assigned to individual series or movies from their edit page.

| Tag | Behaviour |
|-----|-----------|
| `provider-animeunity` | Uses AnimeUnity for that title |
| `provider-<site>` | Uses any supported VibraVid site for that title |
| `hold` / `pausa` | Skips the item until the tag is removed |
| `skip-s1`, `skip-s2`, ... | Skips a specific season of a series |

**Method 2 — Global fallback list (recommended, zero per-title configuration)**

Configure `provider_fallback` with an ordered list of providers. VibraVid tries them in sequence and stops at the first that finds a matching title. No tagging required — add as many providers as you want as safety nets.

Recommended full configuration (covers general content, anime, and niche services):

```json
"provider_fallback": [
    "streamingcommunity",
    "animeunity",
    "discoveryplus",
    "discovery",
    "dmax",
    "nove",
    "realtime",
    "mediasetinfinity",
    "raiplay",
    "homegardentv",
    "foodnetwork",
    "animeworld",
    "crunchyroll",
    "primevideo",
    "tubitv"
]
```

If `provider_fallback` is empty or omitted, VibraVid tries `streamingcommunity` only and fails if the title is not found there.

**Italian dub preference (`download_italian_anime_default`)**

When `true`, if the selected provider returns both an original-language version and an `(ITA)` dubbed version of the same title, VibraVid automatically picks the Italian dub. This applies regardless of which method selected the provider.

```json
"download_italian_anime_default": true
```

## Path mapping — essential for split environments

`path_mapping` is one of the most important settings when Radarr/Sonarr run in Docker while VibraVid runs on the host (or vice versa). After a download completes, VibraVid must tell Radarr/Sonarr exactly where the file is so they can import it. If the two services see the same physical folder under different paths, Radarr/Sonarr will receive a path they cannot resolve and the import will fail.

| Setup | `path_mapping` needed? |
|-------|----------------------|
| Both on bare metal | No |
| Both in Docker with identical volume mounts | No |
| VibraVid on host, ARR stack in Docker | **Yes** |
| Both in Docker with different volume mounts | **Yes** |

**Example:** VibraVid on the host sees `/media/Media/Film`. Radarr's Docker Compose mounts the same folder at a different path:

```yaml
volumes:
  - /media/Media/Film:/media/Film
  - /media/Media/Anime:/media/Anime
  - /media/Media/Series:/media/Series
```

Without `path_mapping`, VibraVid reports `/media/Media/Film/my-movie` to Radarr. Radarr looks for that path inside its container — it does not exist there — and the import fails. With the mapping configured, VibraVid automatically translates the path before every API call:

```json
"path_mapping": {
    "/media/Media/Film":   "/media/Film",
    "/media/Media/Anime":  "/media/Anime",
    "/media/Media/Series": "/media/Series"
}
```

Each key is a prefix as seen by VibraVid; the value is the equivalent prefix inside the Radarr/Sonarr container. Entries are checked in order and the first matching prefix is replaced. Leave `path_mapping` as `{}` when both services share the same filesystem view.

## Sonarr workflow

For series, VibraVid ARR can:

- read missing episodes from Sonarr;
- resolve series, season and episode metadata;
- download the requested episode into the expected series path;
- trigger a Sonarr rescan/import;
- verify whether the episode was imported;
- optionally mark the episode as unmonitored after successful import.

## Radarr workflow

For movies, VibraVid ARR can:

- read missing movies from Radarr;
- resolve title, year and TMDB metadata;
- download the requested movie into the expected movie path;
- trigger a Radarr rescan/import;
- verify whether the movie was imported;
- optionally mark the movie as unmonitored after successful import.

## Naming and folder structure — always delegated to Sonarr/Radarr

VibraVid never invents its own filename/folder scheme for imported media. After a
download completes it calls Sonarr/Radarr's own `Rescan`/`ManualImport`/`Rename` commands,
so the final file on disk always follows **your** naming configuration in Sonarr/Radarr
(Settings -> Media Management), not a VibraVid-specific format.

!!! warning
    Sonarr's **"Rename Episodes"** and Radarr's **"Rename Movies"** options (Settings ->
    Media Management) are **disabled by default** on a clean Sonarr/Radarr install. If left
    disabled, imported files still land correctly in your library, but keep VibraVid's raw
    scraped filename instead of your configured naming format (no `S01E01`, no year, etc).
    Enable "Rename Episodes" / "Rename Movies" in Sonarr/Radarr if you want clean, consistent
    filenames. VibraVid logs a one-time warning per ARR sync when it detects this setting is
    off.

## Recommended setup

Use shared volumes so VibraVid and Sonarr/Radarr see the same filesystem paths. If containers use different internal paths for the same media folder, imports may fail because Sonarr/Radarr will not find the downloaded files.

Example Docker path layout:

```text
/media
├── movies
├── series
└── downloads
```

Mount the same media root into VibraVid, Sonarr and Radarr whenever possible.
