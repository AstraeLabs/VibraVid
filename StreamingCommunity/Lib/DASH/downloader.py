# 25.07.25

import os
import shutil
import logging
from typing import Optional, Dict


# External libraries
from rich.console import Console
from rich.table import Table


# Internal utilities
from StreamingCommunity.Util import config_manager, os_manager, internet_manager
from StreamingCommunity.Util.os import get_wvd_path
from StreamingCommunity.Util.http_client import create_client, get_userAgent


# Logic class
from .parser import MPD_Parser
from .segments import MPD_Segments
from .decrypt import decrypt_with_mp4decrypt
from .cdm_helpher import get_widevine_keys


# FFmpeg functions
from StreamingCommunity.Lib.FFmpeg.util import print_duration_table
from StreamingCommunity.Lib.FFmpeg.merge import join_audios, join_video, join_subtitle


# Config
DOWNLOAD_SPECIFIC_AUDIO = config_manager.get_list('M3U8_DOWNLOAD', 'specific_list_audio')
DOWNLOAD_SPECIFIC_SUBTITLE = config_manager.get_list('M3U8_DOWNLOAD', 'specific_list_subtitles')
MERGE_SUBTITLE = config_manager.get_bool('M3U8_DOWNLOAD', 'merge_subs')
FILTER_CUSTOM_REOLUTION = str(config_manager.get('M3U8_CONVERSION', 'force_resolution')).strip().lower()
CLEANUP_TMP = config_manager.get_bool('M3U8_DOWNLOAD', 'cleanup_tmp_folder')
RETRY_LIMIT = config_manager.get_int('REQUESTS', 'max_retry')
EXTENSION_OUTPUT = config_manager.get("M3U8_CONVERSION", "extension")


# Variable
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


class DASH_Downloader:
    def __init__(self, license_url, mpd_url, mpd_sub_list: list = None, output_path: str = None):
        """
        Initialize the DASH Downloader with necessary parameters.

        Parameters:
            - license_url (str): URL to obtain the license for decryption.
            - mpd_url (str): URL of the MPD manifest file.
            - mpd_sub_list (list): List of subtitle dicts with keys: 'language', 'url', 'format'.
            - output_path (str): Path to save the final output file.
        """
        self.cdm_device = get_wvd_path()
        self.license_url = str(license_url).strip() if license_url else None
        self.mpd_url = str(mpd_url).strip()
        self.mpd_sub_list = mpd_sub_list or []
        
        # Sanitize the output path to remove invalid characters
        sanitized_output_path = os_manager.get_sanitize_path(output_path)
        self.out_path = os.path.splitext(os.path.abspath(sanitized_output_path))[0]
        self.original_output_path = sanitized_output_path
        self.file_already_exists = os.path.exists(self.original_output_path)
        self.parser = None

        # Added defaults to avoid AttributeError when no subtitles/audio/video are present
        # Non la soluzione migliore ma evita crash in assenza di audio/video/subs
        self.selected_subs = []
        self.selected_video = None
        self.selected_audio = None

        self._setup_temp_dirs()

        self.error = None
        self.stopped = False
        self.output_file = None
        
        # For progress tracking
        self.current_downloader: Optional[MPD_Segments] = None
        self.current_download_type: Optional[str] = None

    def _setup_temp_dirs(self):
        """
        Create temporary folder structure under out_path\tmp
        """
        if self.file_already_exists:
            return

        self.tmp_dir = os.path.join(self.out_path, "tmp")
        self.encrypted_dir = os.path.join(self.tmp_dir, "encrypted")
        self.decrypted_dir = os.path.join(self.tmp_dir, "decrypted")
        self.optimize_dir = os.path.join(self.tmp_dir, "optimize")
        self.subs_dir = os.path.join(self.tmp_dir, "subs")
        
        os.makedirs(self.encrypted_dir, exist_ok=True)
        os.makedirs(self.decrypted_dir, exist_ok=True)
        os.makedirs(self.optimize_dir, exist_ok=True)
        os.makedirs(self.subs_dir, exist_ok=True)

    def parse_manifest(self, custom_headers):
        """
        Parse the MPD manifest file and extract relevant information.
        """
        if self.file_already_exists:
            return

        self.parser = MPD_Parser(self.mpd_url)
        self.parser.parse(custom_headers)

        def calculate_column_widths():
            """Calculate optimal column widths based on content."""
            data_rows = []
            
            # Video info
            selected_video, list_available_resolution, filter_custom_resolution, downloadable_video = self.parser.select_video(FILTER_CUSTOM_REOLUTION)
            self.selected_video = selected_video
            
            available_video = ', '.join(list_available_resolution) if list_available_resolution else "Nothing"
            set_video = str(filter_custom_resolution) if filter_custom_resolution else "Nothing"
            downloadable_video_str = str(downloadable_video) if downloadable_video else "Nothing"
            
            data_rows.append(["Video", available_video, set_video, downloadable_video_str])

            # Audio info
            selected_audio, list_available_audio_langs, filter_custom_audio, downloadable_audio = self.parser.select_audio(DOWNLOAD_SPECIFIC_AUDIO)
            self.selected_audio = selected_audio
            
            if list_available_audio_langs:
                available_audio = ', '.join(list_available_audio_langs)
                set_audio = str(filter_custom_audio) if filter_custom_audio else "Nothing"
                downloadable_audio_str = str(downloadable_audio) if downloadable_audio else "Nothing"
                
                data_rows.append(["Audio", available_audio, set_audio, downloadable_audio_str])
            
            # Subtitle info
            available_sub_languages = [sub.get('language') for sub in self.mpd_sub_list]
            
            if available_sub_languages:
                available_subs = ', '.join(available_sub_languages)
                
                # Filter subtitles based on configuration
                if "*" in DOWNLOAD_SPECIFIC_SUBTITLE:
                    self.selected_subs = self.mpd_sub_list
                    downloadable_sub_languages = available_sub_languages
                else:
                    self.selected_subs = [
                        sub for sub in self.mpd_sub_list 
                        if sub.get('language') in DOWNLOAD_SPECIFIC_SUBTITLE
                    ]
                    downloadable_sub_languages = [sub.get('language') for sub in self.selected_subs]
                
                downloadable_subs = ', '.join(downloadable_sub_languages) if downloadable_sub_languages else "Nothing"
                set_subs = ', '.join(DOWNLOAD_SPECIFIC_SUBTITLE) if DOWNLOAD_SPECIFIC_SUBTITLE else "Nothing"
                
                data_rows.append(["Subtitle", available_subs, set_subs, downloadable_subs])
            
            # Calculate max width for each column
            headers = ["Type", "Available", "Set", "Downloadable"]
            max_widths = [len(header) for header in headers]
            
            for row in data_rows:
                for i, cell in enumerate(row):
                    max_widths[i] = max(max_widths[i], len(str(cell)))
            
            # Add some padding
            max_widths = [w + 2 for w in max_widths]
            
            return data_rows, max_widths
        
        data_rows, column_widths = calculate_column_widths()
        
        # Create table with dynamic widths
        table = Table(show_header=True, header_style="cyan", border_style="blue")
        table.add_column("Type", style="cyan bold", width=column_widths[0])
        table.add_column("Available", style="green", width=column_widths[1])
        table.add_column("Set", style="red", width=column_widths[2])
        table.add_column("Downloadable", style="yellow", width=column_widths[3])
        
        # Add all rows to the table
        for row in data_rows:
            table.add_row(*row)

        console.print(table)
        console.print("")

    def get_representation_by_type(self, typ):
        """
        Get the representation of the selected stream by type.
        """
        if typ == "video":
            return getattr(self, "selected_video", None)
        elif typ == "audio":
            return getattr(self, "selected_audio", None)
        return None

    def download_subtitles(self) -> bool:
        """
        Download subtitle files based on configuration with retry mechanism.
        Returns True if successful or if no subtitles to download, False on critical error.
        """
        client = create_client(headers={'User-Agent': get_userAgent()})
        
        for sub in self.selected_subs:
            try:
                language = sub.get('language', 'unknown')
                fmt = sub.get('format', 'vtt')

                # Download subtitle
                response = client.get(sub.get('url'))
                response.raise_for_status()
                
                # Save subtitle file and make request
                sub_filename = f"{language}.{fmt}"
                sub_path = os.path.join(self.subs_dir, sub_filename)
                
                with open(sub_path, 'wb') as f:
                    f.write(response.content)
                    
            except Exception as e:
                console.print(f"[red]Error downloading subtitle {language}: {e}")
                return False
            
        return True

    def download_and_decrypt(self, custom_headers=None, query_params=None, key=None) -> bool:
        """
        Download and decrypt video/audio streams. Skips download if file already exists.

        Args:
            - custom_headers (dict): Optional HTTP headers for the license request.
            - query_params (dict): Optional query parameters to append to the license URL.
            - license_data (str/bytes): Optional raw license data to bypass HTTP request.
            - key (str): Optional raw license data to bypass HTTP request.
        """
        if self.file_already_exists:
            console.print(f"[red]File already exists: {self.original_output_path}")
            self.output_file = self.original_output_path
            return True
        
        self.error = None
        self.stopped = False
        video_segments_count = 0

        # Fetch keys immediately after obtaining PSSH
        if not self.parser.pssh:
            self.download_segments(clear=True)
            return True

        keys = get_widevine_keys(
            pssh=self.parser.pssh,
            license_url=self.license_url,
            cdm_device_path=self.cdm_device,
            headers=custom_headers,
            query_params=query_params,
            key=key
        )

        if not keys:
            console.print("[red]No keys found, cannot proceed with download.")
            return False

        # Extract the first key for decryption
        KID = keys[0]['kid']
        KEY = keys[0]['key']

        # Download subtitles
        self.download_subtitles()

        # Download the video to get segment count
        video_rep = self.get_representation_by_type("video")
        if video_rep:
            encrypted_path = os.path.join(self.encrypted_dir, f"{video_rep['id']}_encrypted.m4s")

            # If m4s file doesn't exist, start downloading
            if not os.path.exists(encrypted_path):
                video_downloader = MPD_Segments(
                    tmp_folder=self.encrypted_dir,
                    representation=video_rep,
                    pssh=self.parser.pssh
                )

                # Set current downloader for progress tracking
                self.current_downloader = video_downloader
                self.current_download_type = 'video'

                try:
                    result = video_downloader.download_streams(description="Video")
                    
                    # Store the video segment count for limiting audio
                    video_segments_count = video_downloader.get_segments_count()

                    # Check for interruption or failure
                    if result.get("stopped"):
                        self.stopped = True
                        self.error = "Download interrupted"
                        return False
                    
                    if result.get("nFailed", 0) > 0:
                        self.error = f"Failed segments: {result['nFailed']}"
                        return False
                    
                except Exception as ex:
                    self.error = str(ex)
                    return False
                
                finally:
                    self.current_downloader = None
                    self.current_download_type = None

                # Decrypt video
                decrypted_path = os.path.join(self.decrypted_dir, f"video.{extension_output}")
                result_path = decrypt_with_mp4decrypt(
                    "Video", encrypted_path, KID, KEY, output_path=decrypted_path
                )

                if not result_path:
                    self.error = "Decryption of video failed"
                    return False

        else:
            self.error = "No video found"
            return False
            
        # Now download audio with segment limiting
        audio_rep = self.get_representation_by_type("audio")
        if audio_rep:
            encrypted_path = os.path.join(self.encrypted_dir, f"{audio_rep['id']}_encrypted.m4s")

            # If m4s file doesn't exist, start downloading
            if not os.path.exists(encrypted_path):
                audio_language = audio_rep.get('language', 'Unknown')
                
                audio_downloader = MPD_Segments(
                    tmp_folder=self.encrypted_dir,
                    representation=audio_rep,
                    pssh=self.parser.pssh,
                    limit_segments=video_segments_count if video_segments_count > 0 else None
                )

                # Set current downloader for progress tracking
                self.current_downloader = audio_downloader
                self.current_download_type = f"audio_{audio_language}"

                try:
                    result = audio_downloader.download_streams(description=f"Audio {audio_language}")

                    # Check for interruption or failure
                    if result.get("stopped"):
                        self.stopped = True
                        self.error = "Download interrupted"
                        return False
                    
                    if result.get("nFailed", 0) > 0:
                        self.error = f"Failed segments: {result['nFailed']}"
                        return False
                    
                except Exception as ex:
                    self.error = str(ex)
                    return False
                
                finally:
                    self.current_downloader = None
                    self.current_download_type = None

                # Decrypt audio
                decrypted_path = os.path.join(self.decrypted_dir, f"audio.{extension_output}")
                result_path = decrypt_with_mp4decrypt(
                    f"Audio {audio_language}", encrypted_path, KID, KEY, output_path=decrypted_path
                )

                if not result_path:
                    self.error = "Decryption of audio failed"
                    return False

        else:
            self.error = "No audio found"
            return False

        return True

    def download_segments(self, clear=False):
        """
        Download video/audio segments without decryption (for clear content).
        
        Parameters:
            clear (bool): If True, content is not encrypted and doesn't need decryption
        """
        if not clear:
            console.print("[yellow]Warning: download_segments called with clear=False")
            return False
        
        video_segments_count = 0
        
        # Download subtitles
        self.download_subtitles()
        
        # Download video
        video_rep = self.get_representation_by_type("video")
        if video_rep:
            encrypted_path = os.path.join(self.encrypted_dir, f"{video_rep['id']}_encrypted.m4s")
            
            # If m4s file doesn't exist, start downloading
            if not os.path.exists(encrypted_path):
                video_downloader = MPD_Segments(
                    tmp_folder=self.encrypted_dir,
                    representation=video_rep,
                    pssh=self.parser.pssh
                )
                
                # Set current downloader for progress tracking
                self.current_downloader = video_downloader
                self.current_download_type = 'video'
                
                try:
                    result = video_downloader.download_streams(description="Video")
                    
                    # Store the video segment count for limiting audio
                    video_segments_count = video_downloader.get_segments_count()
                    
                    # Check for interruption or failure
                    if result.get("stopped"):
                        self.stopped = True
                        self.error = "Download interrupted"
                        return False
                    
                    if result.get("nFailed", 0) > 0:
                        self.error = f"Failed segments: {result['nFailed']}"
                        return False
                    
                except Exception as ex:
                    self.error = str(ex)
                    console.print(f"[red]Error downloading video: {ex}")
                    return False
                
                finally:
                    self.current_downloader = None
                    self.current_download_type = None
            
            # NO DECRYPTION: just copy/move to decrypted folder
            decrypted_path = os.path.join(self.decrypted_dir, f"video.{extension_output}")
            if os.path.exists(encrypted_path) and not os.path.exists(decrypted_path):
                shutil.copy2(encrypted_path, decrypted_path)

        else:
            self.error = "No video found"
            console.print(f"[red]{self.error}")
            return False
        
        # Download audio with segment limiting
        audio_rep = self.get_representation_by_type("audio")
        if audio_rep:
            encrypted_path = os.path.join(self.encrypted_dir, f"{audio_rep['id']}_encrypted.m4s")
            
            # If m4s file doesn't exist, start downloading
            if not os.path.exists(encrypted_path):
                audio_language = audio_rep.get('language', 'Unknown')
                
                audio_downloader = MPD_Segments(
                    tmp_folder=self.encrypted_dir,
                    representation=audio_rep,
                    pssh=self.parser.pssh,
                    limit_segments=video_segments_count if video_segments_count > 0 else None
                )
                
                # Set current downloader for progress tracking
                self.current_downloader = audio_downloader
                self.current_download_type = f"audio_{audio_language}"
                
                try:
                    result = audio_downloader.download_streams(description=f"Audio {audio_language}")
                    
                    # Check for interruption or failure
                    if result.get("stopped"):
                        self.stopped = True
                        self.error = "Download interrupted"
                        return False
                    
                    if result.get("nFailed", 0) > 0:
                        self.error = f"Failed segments: {result['nFailed']}"
                        return False
                    
                except Exception as ex:
                    self.error = str(ex)
                    console.print(f"[red]Error downloading audio: {ex}")
                    return False
                
                finally:
                    self.current_downloader = None
                    self.current_download_type = None
            
            # NO DECRYPTION: just copy/move to decrypted folder
            decrypted_path = os.path.join(self.decrypted_dir, f"audio.{extension_output}")
            if os.path.exists(encrypted_path) and not os.path.exists(decrypted_path):
                shutil.copy2(encrypted_path, decrypted_path)
                
        else:
            self.error = "No audio found"
            console.print(f"[red]{self.error}")
            return False
        
        return True

    def finalize_output(self):
        """
        Merge video, audio, and optionally subtitles into final output file.
        """
        if self.file_already_exists:
            output_file = self.original_output_path
            self.output_file = output_file
            return output_file
        
        # Definition of decrypted files
        video_file = os.path.join(self.decrypted_dir, f"video.{extension_output}")
        audio_file = os.path.join(self.decrypted_dir, f"audio.{extension_output}")
        output_file = self.original_output_path
        
        # Set the output file path for status tracking
        self.output_file = output_file
        use_shortest = False

        # Merge video and audio
        merged_file = None
        try:
            if os.path.exists(video_file) and os.path.exists(audio_file):
                audio_tracks = [{"path": audio_file}]
                merged_file, use_shortest = join_audios(video_file, audio_tracks, output_file)
                
            elif os.path.exists(video_file):
                merged_file = join_video(video_file, output_file, codec=None)
                
            else:
                console.print("[red]Video file missing, cannot export")
                self.error = "Video file missing, cannot export"
                return None
                
        except Exception as e:
            console.print(f"[red]Error during merge: {e}")
            self.error = f"Merge failed: {e}"
            return None
        
        # Merge subtitles if available
        if MERGE_SUBTITLE and self.selected_subs:

            # Check which subtitle files actually exist
            existing_sub_tracks = []
            for sub in self.selected_subs:
                language = sub.get('language', 'unknown')
                fmt = sub.get('format', 'vtt')
                sub_path = os.path.join(self.subs_dir, f"{language}.{fmt}")
                
                if os.path.exists(sub_path):
                    existing_sub_tracks.append({
                        'path': sub_path,
                        'language': language
                    })
            
            if existing_sub_tracks:

                # Create temporary file for subtitle merge
                temp_output = output_file.replace(f'.{extension_output}', f'_temp.{extension_output}')
                
                try:
                    final_file = join_subtitle(
                        video_path=merged_file,
                        subtitles_list=existing_sub_tracks,
                        out_path=temp_output
                    )
                    
                    # Replace original with subtitled version
                    if os.path.exists(final_file):
                        if os.path.exists(output_file):
                            os.remove(output_file)
                        os.rename(final_file, output_file)
                        merged_file = output_file
                        
                except Exception as e:
                    console.print(f"[yellow]Warning: Failed to merge subtitles: {e}")
        
        # Handle failed sync case
        if use_shortest:
            new_filename = output_file.replace(EXTENSION_OUTPUT, f"_failed_sync.{EXTENSION_OUTPUT}")
            if os.path.exists(output_file):
                os.rename(output_file, new_filename)
                output_file = new_filename
                self.output_file = new_filename

        # Display file information
        file_size = internet_manager.format_file_size(os.path.getsize(output_file))
        duration = print_duration_table(output_file, description=False, return_string=True)
        console.print(f"[yellow]Output[white]: [red]{os.path.abspath(output_file)} \n"
            f"  [cyan]with size[white]: [red]{file_size} \n"
            f"      [cyan]and duration[white]: [red]{duration}")

        if CLEANUP_TMP:
            
            # Clean up: delete only the tmp directory, not the main directory
            if os.path.exists(self.tmp_dir):
                shutil.rmtree(self.tmp_dir, ignore_errors=True)

            # Only remove the temp base directory if it was created specifically for this download
            # and if the final output is NOT inside this directory
            output_dir = os.path.dirname(self.original_output_path)
            
            # Check if out_path is different from the actual output directory
            # and if it's empty, then it's safe to remove
            if (self.out_path != output_dir and os.path.exists(self.out_path) and not os.listdir(self.out_path)):
                try:
                    os.rmdir(self.out_path)

                except Exception:
                    pass

        # Verify the final file exists before returning
        if os.path.exists(output_file):
            return output_file
        else:
            self.error = "Final output file was not created successfully"
            return None
    
    def get_status(self):
        """
        Returns a dict with 'path', 'error', and 'stopped' for external use.
        """
        return {
            "path": self.output_file,
            "error": self.error,
            "stopped": self.stopped
        }
    
    def get_progress_data(self) -> Optional[Dict]:
        """Get current download progress data."""
        if not self.current_downloader:
            return None

        try:
            progress = self.current_downloader.get_progress_data()
            if progress:
                progress['download_type'] = self.current_download_type
            return progress
            
        except Exception as e:
            logging.error(f"Error getting progress data: {e}")
            return None