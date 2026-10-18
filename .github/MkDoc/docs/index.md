# VibraVid

DRM HLS/DASH downloader.

[![Windows](https://img.shields.io/badge/🪟_Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_win_2025_x64.exe)
[![macOS](https://img.shields.io/badge/🍎_macOS-000000?style=for-the-badge&logo=apple&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_mac_15_arm64)
[![Linux](https://img.shields.io/badge/🐧_Linux_latest-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_linux_24_04_x64)

## Installation

### Manual clone

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

| | pip | uv |
|---|---|---|
| Install | `pip install -r requirements.txt` | `uv sync` |
| Run | `python manual.py` | `uv run manual.py` |
| Sync deps | `pip install -r requirements.txt --upgrade` | `uv sync --upgrade` |

### Unraid

Find the app in the Community Applications.

### Android/Termux (automatic)

!!! important
    Requires **Termux**. Do **NOT** use the Google Play version (outdated and abandoned); get it from [F-Droid](https://f-droid.org/packages/com.termux/) or [GitHub Releases](https://github.com/termux/termux-app/releases).

In Termux, paste this to download VibraVid, compile the components (including Velora) and set up the storage folder:

```bash
curl -sL https://raw.githubusercontent.com/ManoloZocco/StreamingCommunity/main/termux_install.sh | bash
```

Then launch it any time with `vibravid`.

See also: [Login guide](login.md), [NAS deployment](nas.md).

## Quick Start

```bash
python manual.py
```

## Update

At interactive CLI startup VibraVid checks GitHub Releases in the background (cached 24 h) and prints the command for your installation if a newer release exists. `VibraVid --check-update` checks on demand; `--skip-update-check` disables the startup check.

| Installation | Update |
|---|---|
| Binary (Windows / macOS / Linux) | `VibraVid -UP` |
| Docker | `docker compose pull && docker compose up -d` |
| Manual clone | `git fetch origin && git reset --hard origin/main`, then sync dependencies (table above) |

Folders ignored by `.gitignore` (e.g. `Video/`) are **not** deleted by the reset. If the folder isn't a git repository yet:

```bash
git init
git remote add origin https://github.com/AstraeLabs/VibraVid.git
git fetch origin
git reset --hard origin/main
```

## Downloaders

| Type     | Description                  | Example                                  |
| -------- | ----------------------------- | ---------------------------------------- |
| **HLS**  | HTTP Live Streaming (m3u8)   | [View example](https://github.com/AstraeLabs/VibraVid/blob/main/Test/Downloads/HLS.py) |
| **MP4**  | Direct MP4 download          | [View example](https://github.com/AstraeLabs/VibraVid/blob/main/Test/Downloads/MP4.py) |
| **DASH** | MPEG-DASH with DRM bypass\*  | [View example](https://github.com/AstraeLabs/VibraVid/blob/main/Test/Downloads/DASH.py) |
| **ISM** | Smooth Streaming with DRM bypass\*  | [View example](https://github.com/AstraeLabs/VibraVid/blob/main/Test/Downloads/ISM.py) |
| **Custom** | Multi-source hybrid  | [View example](https://github.com/AstraeLabs/VibraVid/blob/main/Test/Downloads/CUSTOM.py) |

> **\*DASH with DRM bypass:** Requires a valid L3\L2\L1\SL3000\SL2000 CDM (Content Decryption Module). This project does not provide or facilitate obtaining CDMs. Users must ensure compliance with applicable laws.

### Custom multi-source downloads

`Generic_Downloader` downloads every selected track from every source in `sources` **concurrently** on one progress bar and muxes them into one file, including hybrid **Dolby Vision + HDR10** (the DV RPU is injected into the HDR10 base via `mkvmerge`/`dovi_tool`).

Full manifests (DASH MPD, HLS master) are auto-selected by codec/resolution/range, including the DV base/companion pairing with `custom_filters={"video": "hybrid"}` (see [Companion Dolby Vision](configuration.md)), across any number of sources and without per-source tagging.

```python
from VibraVid.core.downloader import Generic_Downloader

sources = [
    {"url": "<hdr10+dv m3u8>", "key": "<kid:key>"},
    {"role": "audio", "language": "en", "url": "<audio m3u8>", "key": "<kid:key>"},
    {"role": "subtitle", "language": "en", "url": "<sub url>"},
]

Generic_Downloader(
    sources=sources,
    output_path="./Video/out.mkv",
    custom_filters={"video": "hybrid"},
).start()
```

- `role`: `video`, `audio`, `subtitle` (a range tag like `video:hdr10` is only a label). Needed only for attribute-less rendition manifests (e.g. Apple TV+) to tell sources apart.
- Optional per-source fields: `language`, `name`, `label`, `headers`, `cookies`, `protocol`.
- Test runs: `max_segments=N` or `max_time="HH:MM:SS"`; a clip: `max_segments="10-50"` or `max_time="00:01:00-00:05:00"`.

## Related Projects

- **[MammaMia](https://github.com/UrloMythus/MammaMia)** — Stremio addon for Italian streaming (by UrloMythus)
- **[Unit3Dup](https://github.com/31December99/Unit3Dup)** — Torrent automation for Unit3D tracker (by 31December99)
- **[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE)** — Universal downloader for HLS/DASH/ISM (by nilaoda)
- **[pywidevine](https://github.com/devine-dl/pywidevine)** — Widevine L3 decryption library (by devine-dl)
- **[pyplayready](https://git.gay/ready-dl/pyplayready)** — PlayReady decryption library (by ready-dl)

## Disclaimer

!!! warning
    This software is for **educational and research purposes only**. The authors:

    - **DO NOT** assume responsibility for illegal use
    - **DO NOT** provide or facilitate DRM circumvention tools, CDMs, or decryption keys
    - **DO NOT** endorse piracy or copyright infringement

    By using this software, you agree to comply with all applicable laws and confirm you
    have rights to any content you process. No warranty is provided.

---

**Made with ❤️ for streaming lovers** — *if you find this project useful, consider starring it on [GitHub](https://github.com/AstraeLabs/VibraVid)! ⭐*
