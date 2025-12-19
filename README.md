<div align="center">

<img src="https://i.postimg.cc/Y9t2XgB1/z562m3.png" alt="StreamingCommunity Logo" width="110" style="background: transparent;">

[![PyPI Version](https://img.shields.io/pypi/v/streamingcommunity?logo=pypi&logoColor=white&labelColor=2d3748&color=3182ce&style=for-the-badge)](https://pypi.org/project/streamingcommunity/)
[![Last Commit](https://img.shields.io/github/last-commit/Arrowar/StreamingCommunity?logo=git&logoColor=white&labelColor=2d3748&color=805ad5&style=for-the-badge)](https://github.com/Arrowar/StreamingCommunity/commits)
[![Sponsor](https://img.shields.io/badge/💖_Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white&labelColor=2d3748)](https://ko-fi.com/arrowar)

---

[![Windows](https://img.shields.io/badge/🪟_Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white&labelColor=2d3748)](https://github.com/Arrowar/StreamingCommunity/releases/latest/download/StreamingCommunity_win.exe)
[![macOS](https://img.shields.io/badge/🍎_macOS-000000?style=for-the-badge&logo=apple&logoColor=white&labelColor=2d3748)](https://github.com/Arrowar/StreamingCommunity/releases/latest/download/StreamingCommunity_mac)
[![Linux latest](https://img.shields.io/badge/🐧_Linux_latest-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/Arrowar/StreamingCommunity/releases/latest/download/StreamingCommunity_linux_latest)
[![Linux 22.04](https://img.shields.io/badge/🐧_Linux_22.04-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/Arrowar/StreamingCommunity/releases/latest/download/StreamingCommunity_linux_previous)

*⚡ **Quick Start:** `pip install StreamingCommunity && StreamingCommunity`*

</div>

---

## 📖 Table of Contents
- ⚙️ [Installation](#installation)
- 🚀 [Quick Start](#quick-start)
- 📥 [Downloaders](#downloaders)
- 🛠️ [Configuration](#configuration)
- 🔐 [Login](.github/.site/login.md)
- 🌐 [Domain](https://arrowar.github.io/StreamingCommunity)
- 💡 [Usage Examples](#usage-examples)
- 🔍 [Global Search](#global-search)
- 🧩 [Advanced Features](#advanced-options)
- 🐳 [Deployment](#docker)
- 🎓 [Tutorials](#tutorials)
- 🔗 [Related Projects](#useful-project)

## Installation

### Prerequisites
Make sure you have Python installed on your system:
- **Windows**: Download from [python.org](https://python.org) or install via Microsoft Store
- **Linux**: `sudo apt install python3 python3-pip` (Ubuntu/Debian) or equivalent for your distro
- **MacOS**: `brew install python3` or download from [python.org](https://python.org)

### Dependencies
```bash
# Windows
pip install -r requirements.txt

# Linux/MacOS  
pip3 install -r requirements.txt
```

### Update
```bash
# Windows
python update.py

# Linux/MacOS
python3 update.py
```

---

## Quick Start

**Via pip installation:**
```bash
StreamingCommunity
```

**Manual execution:**
```bash
# Windows
python test_run.py

# Linux/MacOS
python3 test_run.py
```
---

## DNS

DNS configuration is **required** to ensure full functionality, better reliability and proper connectivity.

- **Cloudflare DNS:**  
  - Primary: `1.1.1.1`
  - Setup guide: https://developers.cloudflare.com/1.1.1.1/setup/

- **Quad9 DNS:**  
  - Primary: `9.9.9.9`  
  - Setup guide: https://quad9.net/


---

## Downloaders

<summary>📥 HLS</summary>

Download HTTP Live Streaming (HLS) content from m3u8 URLs.

```python
from StreamingCommunity import HLS_Downloader

downloader = HLS_Downloader(
    m3u8_url="https://example.com/stream.m3u8",
    output_path="/downloads/video.mp4"
)

downloader.download()
```

See [HLS example](./Test/Downloads//HLS.py) for complete usage.

<summary>📽️ MP4</summary>

Direct MP4 file downloader with support for custom headers and referrer.

```python
from StreamingCommunity import MP4_downloader

downloader = MP4_downloader(
    url="https://example.com/video.mp4",
    path="/downloads/saved_video.mp4"
)

downloader.download()
```

See [MP4 example](./Test/Downloads/MP4.py) for complete usage.

<summary>🎞️ DASH</summary>

```python
mpd_url = "https://example.com/stream.mpd"
license_url = "https://example.com/get_license"

dash_process = DASH_Downloader(
    license_url=license_url,
    mpd_url=mpd_url,
    output_path="output.mp4",
)
dash_process.parse_manifest()

if dash_process.download_and_decrypt():
    dash_process.finalize_output()
```

See [DASH example](./Test/Downloads/DASH.py) for complete usage.

<summary>Ⓜ️ MEGA</summary>

```python
mega = Mega_Downloader()
m = mega.login()

output_path = m.download_url(
    url="https://mega.nz/file/0kgCWZZB#7u....",
    dest_path=".\\prova.mp4"
)
```

See [MEGA example](./Test/Downloads/MEGA.py) for complete usage.

---

## Configuration

<summary>⚙️ Overview</summary>

You can change some behaviors by tweaking the configuration file. The configuration file is divided into several main sections.

<summary>📁 OUT_FOLDER</summary>

```json
{
    "OUT_FOLDER": {
        "root_path": "Video",
        "movie_folder_name": "Movie",
        "serie_folder_name": "Serie",
        "anime_folder_name": "Anime",
        "map_episode_name": "E%(episode)_%(episode_name)",
        "add_siteName": false
    }
}
```

#### Directory Configuration
- `root_path`: Directory where all videos will be saved
  * Windows: `C:\\MyLibrary\\Folder` or `\\\\MyServer\\MyLibrary` (network folder)
  * Linux/MacOS: `Desktop/MyLibrary/Folder`

#### Folder Names
- `movie_folder_name`: Subdirectory for movies (can be changed with `--movie_folder_name`)
- `serie_folder_name`: Subdirectory for TV series (can be changed with `--serie_folder_name`)
- `anime_folder_name`: Subdirectory for anime (can be changed with `--anime_folder_name`)

#### Episode Naming
- `map_episode_name`: Template for episode filenames
  * `%(tv_name)`: Name of TV Show
  * `%(season)`: Season number
  * `%(episode)`: Episode number
  * `%(episode_name)`: Episode name
  * Can be changed with `--map_episode_name`

#### Additional Options
- `add_siteName`: Appends site_name to root path (can be changed with `--add_siteName true/false`)

<summary>📥 M3U8_DOWNLOAD Settings</summary>

```json
{
    "M3U8_DOWNLOAD": {
        "default_video_workser": 12,
        "default_audio_workser": 12,
        "segment_timeout": 8,
        "specific_list_audio": [
            "ita"
        ],
        "merge_subs": true,
        "specific_list_subtitles": [
            "ita",    // Specify language codes or use ["*"] to download all available subtitles
            "eng"
        ],
        "cleanup_tmp_folder": true,
        "get_only_link": false
    }
}
```

#### Performance Settings
- `default_video_workser`: Number of threads for video download
  * Can be changed with `--default_video_worker <number>`
- `default_audio_workser`: Number of threads for audio download
  * Can be changed with `--default_audio_worker <number>`

#### Audio Settings
- `specific_list_audio`: List of audio languages to download
  * Can be changed with `--specific_list_audio ita,eng`

#### Subtitle Settings
- `merge_subs`: Whether to merge subtitles with video
- `specific_list_subtitles`: List of subtitle languages to download
  * Use `["*"]` to download all available subtitles
  * Or specify individual languages like `["ita", "eng"]`
  * Can be changed with `--specific_list_subtitles ita,eng`

#### Cleanup
- `cleanup_tmp_folder`: Remove temporary .ts files after download

<summary>🔍 M3U8_PARSER Settings</summary>

```json
{
    "M3U8_PARSER": {
        "force_resolution": "Best"
    }
}
```

#### Resolution Options
- `force_resolution`: Choose video resolution:
  * `"Best"`: Highest available resolution
  * `"Worst"`: Lowest available resolution
  * `"720p"`: Force 720p resolution
  * Specific resolutions:
    - 1080p (1920x1080)
    - 720p (1280x720)
    - 480p (640x480)
    - 360p (640x360)


#### Output fomat Options
- `extension`: Choose video format:
  * `"mp4"`: Output as MP4 file
  * `"mkv"`: Output as MKV file

Final video will be saved with the selected extension. For each format, specific subtitles parameters need to be set in the M3U8_CONVERSION section.

MP4 example:
```json
{
    "M3U8_CONVERSION": {
        "param_subtitles": [
            "-c:s",
            "mov_text"
        ],
        "extension": "mp4"
    }
}
```

MKV example:
```json
{
    "M3U8_CONVERSION": {
        "param_subtitles": [
            "-c:s",
            "webvtt"
        ],
        "extension": "mkv"
    }
}
```

#### Link options
- `get_only_link`: Return M3U8 playlist/index URL instead of downloading


## Update Domains

<summary>🌐 Domain Configuration Methods</summary>

There are two ways to manage the domains for the supported websites:

### 1. Online Domain Fetching (Recommended)

Set `fetch_domain_online` to `true` in your `config.json`:

```json
{
   "DEFAULT": {
      "fetch_domain_online": true
   }
}
```

This will:
- Download the latest domains from the GitHub repository
- Automatically save them to a local `domains.json` file
- Ensure you always have the most up-to-date streaming site domains

### 2. Local Domain Configuration

Set `fetch_domain_online` to `false` to use a local configuration:

```json
{
   "DEFAULT": {
      "fetch_domain_online": false
   }
}
```

Then create a `domains.json` file in the root directory with your domain configuration:

```json
{
   "altadefinizione": {
       "domain": "si",
       "full_url": "https://altadefinizione.si/"
   },
   "streamingcommunity": {
       "domain": "best",
       "full_url": "https://streamingcommunity.best/"
   }
}
```

### 3. Automatic Fallback

If online fetching fails, the script will automatically attempt to use the local `domains.json` file as a fallback, ensuring maximum reliability.

#### 💡 Adding a New Site
If you want to request a new site to be added to the repository, message us on the Discord server!


---

## Usage Examples

### Basic Commands

```bash
# Show help (includes available sites by name and by index)
python test_run.py -h

# Run a specific site by name with a search term
python test_run.py --site streamingcommunity --search "interstellar"

# Run a specific site by numeric index
python test_run.py --site 0 --search "interstellar"

# Auto-download the first result from search
python test_run.py --site streamingcommunity --search "interstellar" --auto-first
```

### Advanced Options

```bash
# Change video and audio workers
python test_run.py --default_video_worker 8 --default_audio_worker 8

# Set specific languages
python test_run.py --specific_list_audio ita,eng --specific_list_subtitles eng,spa

# Keep console open after download
python test_run.py --not_close true
```

### Global Search Commands

```bash
# Use global search
python test_run.py --global -s "cars"

# Select specific category
python test_run.py --category 1       # Search in anime category
python test_run.py --category 2       # Search in movies & series
python test_run.py --category 3       # Search in series
```

### PyPI Installation Usage

```bash
# If installed via pip, you can simply run:
StreamingCommunity

# Or use the entrypoint with arguments, for example:
StreamingCommunity --site streamingcommunity --search "interstellar" --auto-first
```

---

# Global Search

<summary>🔍 Feature Overview</summary>

You can now search across multiple streaming sites at once using the Global Search feature. This allows you to find content more efficiently without having to search each site individually.

<summary>🎯 Search Options</summary>

When using Global Search, you have three ways to select which sites to search:

1. **Search all sites** - Searches across all available streaming sites
2. **Search by category** - Group sites by their categories (movies, series, anime, etc.)
3. **Select specific sites** - Choose individual sites to include in your search

<summary>📝 Navigation and Selection</summary>

After performing a search:

1. Results are displayed in a consolidated table showing:
   - Title
   - Media type (movie, TV series, etc.)
   - Source site

2. Select an item by number to view details or download

3. The system will automatically use the appropriate site's API to handle the download

<summary>⌨️ Command Line Arguments</summary>

The Global Search can be configured from the command line:

- `--global` - Perform a global search across multiple sites.
- `-s`, `--search` - Specify the search terms.

---

## 🧩 Advanced Features

## Hook/Plugin System

<summary>🧩 Run custom scripts before/after the main execution</summary>

Define pre/post hooks in `config.json` under the `HOOKS` section. Supported types:

- **python**: runs `script.py` with the current Python interpreter
- **bash/sh**: runs via `bash`/`sh` on macOS/Linux
- **bat/cmd**: runs via `cmd /c` on Windows
- Inline **command**: use `command` instead of `path`

Sample configuration:

```json
{
  "HOOKS": {
    "pre_run": [
      {
        "name": "prepare-env",
        "type": "python",
        "path": "scripts/prepare.py",
        "args": ["--clean"],
        "env": {"MY_FLAG": "1"},
        "cwd": "~",
        "os": ["linux", "darwin"],
        "timeout": 60,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "post_run": [
      {
        "name": "notify",
        "type": "bash",
        "command": "echo 'Download completed'"
      }
    ]
  }
}
```

Notes:

- **os**: optional OS filter (`windows`, `darwin` (`darwin` is used for MacOS), `linux`).
- **args**: list of arguments passed to the script.
- **env**: additional environment variables.
- **cwd**: working directory for the script; supports `~` and environment variables.
- **continue_on_error**: if `false`, the app stops when the hook fails.
- **timeout**: in seconds; when exceeded the hook fails.

Hooks are executed automatically by `run.py` before (`pre_run`) and after (`post_run`) the main execution.


---

# Docker

<summary>🐳 Basic Setup</summary>

Build the image:
```
docker build -t streaming-community-api .
```

Run the container with Cloudflare DNS for better connectivity:
```
docker run -d --name streaming-community --dns 1.1.1.1 -p 8000:8000 streaming-community-api
```

Tip CLI:
- To run the CLI inside the container, attach to the container and execute:
```
docker exec -it streaming-community python test_run.py
```

<summary>💾 Custom Storage Location</summary>

By default the videos will be saved in `/app/Video` inside the container. To save them on your machine:

```
docker run -it --dns 9.9.9.9 -p 8000:8000 -v /path/to/download:/app/Video streaming-community-api
```

<summary>🛠️ Quick Setup with Make</summary>

Inside the Makefile (install `make`) are already configured two commands to build and run the container:

```
make build-container

# set your download directory as ENV variable
make LOCAL_DIR=/path/to/download run-container
```

The `run-container` command mounts also the `config.json` file, so any change to the configuration file is reflected immediately without having to rebuild the image.


# Tutorials

- [Windows](https://www.youtube.com/watch?v=mZGqK4wdN-k)
- [Linux](https://www.youtube.com/watch?v=0qUNXPE_mTg)
- [Pypy](https://www.youtube.com/watch?v=C6m9ZKOK0p4)


# Useful Project

## 🎯 [Unit3Dup](https://github.com/31December99/Unit3Dup)
Bot in Python per la generazione e l'upload automatico di torrent su tracker basati su Unit3D.

## 🇮🇹 [MammaMia](https://github.com/UrloMythus/MammaMia)
Addon per Stremio che consente lo streaming HTTPS di film, serie, anime e TV in diretta in lingua italiana.

## 🧩 [streamingcommunity-unofficialapi](https://github.com/Blu-Tiger/streamingcommunity-unofficialapi)
API non ufficiale per accedere ai contenuti del sito italiano StreamingCommunity.


# Disclaimer
> **Note:** This software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and noninfringement. In no event shall the authors or copyright holders be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software.

> **Note:** DASH downloads require a valid L3 CDM (Content Decryption Module) to proceed. This project does not provide, include, or facilitate obtaining any CDM. Users are responsible for ensuring compliance with all applicable laws and requirements regarding DRM and decryption modules.

---
<div align="center">
**Made with ❤️ for streaming lovers**

*If you find this project useful, consider starring it! ⭐*
</div>