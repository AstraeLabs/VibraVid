# Supported Sites

VibraVid auto-discovers every service module under `VibraVid/services/`. Each one
gets an **index** (the `#` column) — the position it appears at in the CLI and Web
GUI site pickers. Select a service by name or by index:

## Built-in services

<!-- BEGIN:sites -->

| Site | # | Content | Region | Streaming | DRM |
| --- | --- | --- | --- | --- | --- | --- |
| `streamingcommunity` | 0 | Movies & Series | Global | HLS | - | 
| `animeunity` | 1 | Anime | Global | HLS | - | - |
| `mediasetinfinity` | 3 | Movies & Series | IT, ES | DASH | Widevine |
| `raiplay` | 4 | Movies & Series | IT | DASH / HLS | Widevine | - |
| `animeworld` | 5 | Anime | Global | - | - | - |
| `crunchyroll` | 6 | Anime | Global | DASH | Widevine |
| `realtime` | 7 | Series | IT | HLS | - | - |
| `dmax` | 8 | Series | IT | HLS | - | - |
| `tubitv` | 9 | Movies & Series | US | DASH | Yes | - |
| `discoveryplus` | 10 | Movies & Series | Global | DASH | Widevine + PlayReady + ClearKey |
| `discovery` | 11 | Series | IT | HLS | - | - |
| `nove` | 12 | Series | IT | HLS | - | - |
| `foodnetwork` | 13 | Series | IT | HLS | - | - |
| `homegardentv` | 14 | Series | IT | HLS | - | - |
| `plutotv` | 17 | Series | IT | DASH / HLS | PlayReady |
| `monochrome` | 18 | Music | Global | - | - | - |
| `rakutentv` | 19 | Movies & Series | Multi-country (see below) | DASH / HLS | PlayReady / Widevine |

---

## Rakuten TV countries

`rakutentv` works with any Rakuten TV country (it, nl, fr, de, es, uk, pt, at, ch, be, ...), each with its own catalogue. The country is picked in this order:

1. the country in the title URL: `python manual.py -i rakutentv --url "https://www.rakuten.tv/nl/movies/de-eetclub"`
2. the `--country` option: `python manual.py -i rakutentv -s "eetclub" --country nl`
3. `country` in the `rakutentv` section of `login.json` (this is also what the Web GUI search uses; in the GUI you can paste a title URL to open another country's title)
4. `it`

```json
"rakutentv": {
  "email": "",
  "password": "",
  "country": "nl"
}
```

- The audio language defaults to the country's main one (`NLD` for `nl`, `ITA` for `it`, ...) if the title has it, otherwise the first one the title offers. Use `--audio-lang` to force one (for example `ENG`).
- Free titles (with ads) need no account. Rented or bought ones need `email` and `password`, and the login is kept separately for each country.
- The country only selects the catalogue: Rakuten can still restrict the stream by your IP address, so some titles may need a connection from that country.
