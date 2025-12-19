# 16.12.25

import re
import subprocess
import shutil
from pathlib import Path


# External libraries
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.os import get_megatools_path
from StreamingCommunity.Util.os import os_manager


# Variable
console = Console()


class MEGA_Downloader:
    
    # Episode patterns for series organization
    EP_PATTERNS = [
        re.compile(r'[Ss](\d{1,2})[Ee](\d{1,2})'),
        re.compile(r'[_\s-]+(\d{1,2})x(\d{1,2})', re.IGNORECASE)
    ]

    LANG_PRIORITY = [
        re.compile(r'\bita\b', re.IGNORECASE),
        re.compile(r'italian', re.IGNORECASE)
    ]
    
    VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}
    
    def __init__(self, choose_files=False):
        self.megatools_exe = get_megatools_path()
        self.choose_files = choose_files
        
        if not self.megatools_exe:
            raise RuntimeError("Megatools executable not found. Please ensure it is installed.")

    def download_url(self, url, dest_path=None):
        """Download a file or folder by its public url"""
        if "/folder/" in url:
            return self._download_folder_megatools(str(url).strip(), dest_path)
        else:
            return self._download_movie_megatools(str(url).strip(), dest_path)

    def _download_movie_megatools(self, url, dest_path=None):
        """Download a single movie file using megatools"""
        output_dir = Path(dest_path).parent if dest_path else Path("./Movies")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        cmd = [str(self.megatools_exe), "dl", url, "--path", str(output_dir)]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        for line in process.stdout:
            print(line, end='')
        
        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, process.args)
        
        console.print("[green]Download completato!")
        return str(output_dir)

    def _download_folder_megatools(self, url, dest_path=None):
        """Download and organize a series folder using megatools"""
        # Sanitize dest_path if provided
        if dest_path:
            dest_path = os_manager.get_sanitize_path(dest_path)
            base_dir = Path(dest_path).parent
        else:
            base_dir = Path("./")
        
        tv_dir = base_dir / "TV"
        
        # Use a shorter temp directory name to avoid path length issues
        import uuid
        tmp_dir_name = f"_tmp_{uuid.uuid4().hex[:8]}"  # Shorter temp name
        tmp_dir = base_dir / tmp_dir_name
        
        console.print("[cyan]Download Series ...")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        cmd = [str(self.megatools_exe), "dl", url, "--path", str(tmp_dir)]
        if self.choose_files:
            cmd.append("--choose-files")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        for line in process.stdout:
            print(line, end='')
            
            if line.startswith('F ') and not self.choose_files:
                filename = line.split('\\')[-1].strip()
                parsed = self._parse_episode(filename)
                
                if parsed:
                    show, season, episode, ep_title = parsed
                    ep_display = f"S{season:02d}E{episode:02d}"
                    if ep_title:
                        ep_display += f" - {ep_title}"
                    
                    console.print(f"\n[cyan]Download: [yellow]{show} [magenta]{ep_display}[/magenta]\n")
        
        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, process.args)

        series_root = self._select_language_folder(tmp_dir)
        result_path = self._organize_series(series_root, tv_dir)

        shutil.rmtree(tmp_dir)
        return result_path

    def _select_language_folder(self, base):
        """Select folder based on language priority"""
        folders = [f for f in base.iterdir() if f.is_dir()]
        if not folders:
            return base

        for rx in self.LANG_PRIORITY:
            for f in folders:
                if rx.search(f.name):
                    console.print(f"[green]Select language: {f.name}")
                    return f

        return folders[0]
    
    def _organize_series(self, base, tv_dir):
        """Organize series files into proper structure"""
        tv_dir.mkdir(parents=True, exist_ok=True)
        show_name = None
        last_path = None
        
        for file in base.rglob("*"):
            if not file.is_file():
                continue
            
            if file.suffix.lower() not in self.VIDEO_EXTENSIONS:
                continue

            parsed = self._parse_episode(file.name)
            if not parsed:
                continue

            show, season, episode, ep_title = parsed
            
            if show_name is None:
                show_name = show
            
            # Sanitize show name
            clean_show = os_manager.get_sanitize_file(show)
            season_dir = tv_dir / clean_show / f"Season {season:02d}"
            season_dir.mkdir(parents=True, exist_ok=True)

            name = f"S{season:02d}E{episode:02d}"
            if ep_title:
                # Limit episode title length
                max_title_len = 50  # Adjust as needed
                if len(ep_title) > max_title_len:
                    ep_title = ep_title[:max_title_len]
                name += f" - {ep_title}"

            # Sanitize the final filename
            final_name = os_manager.get_sanitize_file(f"{name}{file.suffix}")
            last_path = season_dir / final_name
            
            shutil.move(file, last_path)
        
        return str(last_path.parent) if last_path else str(tv_dir)

    def _parse_episode(self, filename):
        """Parse episode information from filename"""
        for rx in self.EP_PATTERNS:
            m = rx.search(filename)
            if m:
                season = int(m.group(1))
                episode = int(m.group(2))

                before = filename[:m.start()]
                after = filename[m.end():]

                show = self._clean_show_name(before)
                ep_title = self._clean_episode_title(after)

                return show, season, episode, ep_title
        return None

    def _clean_show_name(self, text):
        """Clean show name from extra characters"""
        text = re.sub(r'[-._\s]+\d*$', '', text)
        text = re.sub(r'[-._]+$', '', text)
        text = text.replace('_', ' ')
        text = re.sub(r'\s{2,}', ' ', text)
        return text.strip().title()

    def _clean_episode_title(self, text):
        """Clean episode title from quality tags"""
        text = re.sub(r'\.(mkv|mp4|avi).*$', '', text, flags=re.I)
        text = re.sub(r'[-_]', ' ', text)
        text = re.sub(r'\b(web[- ]?dl|bluray|720p|1080p|ita|eng|subs?)\b', '', text, flags=re.I)
        text = re.sub(r'\s{2,}', ' ', text).strip()
        return text if len(text) > 3 else None