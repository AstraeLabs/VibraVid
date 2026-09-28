# VibraVid

DRM HLS/DASH downloader.

[![Windows](https://img.shields.io/badge/🪟_Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_win_2025_x64.exe)
[![macOS](https://img.shields.io/badge/🍎_macOS-000000?style=for-the-badge&logo=apple&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_mac_15_x64)
[![Linux](https://img.shields.io/badge/🐧_Linux_latest-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_linux_24_04_x64)

## Installation

### Option 1 — Manual Clone

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

Then install and run with either **pip** or **uv**:

**pip:**
```bash
pip install -r requirements.txt   # install
python manual.py                  # run
pip install -r requirements.txt --upgrade  # sync deps
```

**uv:**
```bash
uv sync              # install
uv run manual.py     # run
uv sync --upgrade    # sync deps
```

### Option 2 — Unraid

```
You can find the app in the Community Application
```

### Option 3 — Android/Termux (automatic)

!!! important
    This script requires **Termux**. Do **NOT** install Termux from the Google Play Store, as
    that version is outdated and abandoned due to Android policy restrictions. Instead,
    download the latest official version from:

    - 📥 [F-Droid](https://f-droid.org/packages/com.termux/)
    - 📥 [GitHub Releases](https://github.com/termux/termux-app/releases)

Once you have installed Termux, open the app, copy the command below, paste it into the terminal, and press **Enter** to run the automatic installation (it will download VibraVid, compile all necessary components including Velora, and configure the storage folder):

```bash
curl -sL https://raw.githubusercontent.com/ManoloZocco/StreamingCommunity/main/termux_install.sh | bash
```

Once the installation is complete, you can launch the app at any time by simply typing:

```bash
vibravid
```

### Additional Documentation

- 📝 [Login Guide](login.md) — Authentication for supported services
- 🖥️ [NAS Deployment Guide](nas.md) — Docker setup on Synology, TrueNAS, and other NAS devices

## Quick Start

```bash
python manual.py
```

## Update

### Binary (Windows / macOS / Linux)

```bash
VibraVid -UP
```

### Manual clone

```bash
git fetch origin
git reset --hard origin/main
```

Then sync dependencies:

**pip:**
```bash
pip install -r requirements.txt --upgrade
```

**uv:**
```bash
uv sync --upgrade
```

!!! note
    If the folder is not yet an initialized Git repository:
    ```bash
    git init
    git remote add origin https://github.com/AstraeLabs/VibraVid.git
    git fetch origin
    git reset --hard origin/main
    ```

!!! warning
    Folders ignored by `.gitignore` (e.g. `Video/`) **will not be deleted**.

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

`Generic_Downloader` takes a list of `sources`, downloads every selected track from
every source **concurrently** on one shared progress bar, then muxes them into a single
file — including hybrid **Dolby Vision + HDR10** output (the DV RPU is injected into the
HDR10 base via `mkvmerge`/`dovi_tool`).

When sources are full manifests (DASH MPD, HLS master) the tracks are auto-selected from
the advertised codec/resolution/range — including the Dolby Vision base/companion pairing
when `custom_filters={"video": "hybrid"}` is set (see [Companion Dolby Vision](configuration.md)),
across any number of sources, with no per-source tagging needed.

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

Supported `role` values: `video`, `audio`, `subtitle` (a range tag like `video:hdr10`
is accepted but is purely a cosmetic label). Roles are only needed for attribute-less
rendition manifests (e.g. Apple TV+, where every stream parses with no codec/resolution
metadata) to tell sources apart — a normal manifest doesn't need any. Optional per-source
fields: `language`, `name`, `label`, `headers`, `cookies`, `protocol`. Limit a test run
with `max_segments=N` or `max_time="HH:MM:SS"`, or grab a specific clip with a range:
`max_segments="10-50"` or `max_time="00:01:00-00:05:00"`.

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
