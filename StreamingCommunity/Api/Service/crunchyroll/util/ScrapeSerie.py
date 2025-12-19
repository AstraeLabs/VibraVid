# 16.03.25

import logging
from typing import List, Dict, Tuple


# Internal utilities
from StreamingCommunity.Api.Template.object import SeasonManager
from .get_license import CrunchyrollClient


# Variable
NORMALIZE_SEASON_NUMBERS = False        # Set to True to remap seasons to 1..N range


def get_series_seasons(series_id, client: CrunchyrollClient, params):
    """Fetches seasons for a series."""
    url = f'https://www.crunchyroll.com/content/v2/cms/series/{series_id}/seasons'
    return client._request_with_retry('GET', url, params=params)


def get_season_episodes(season_id, client: CrunchyrollClient, params):
    """Fetches episodes for a season."""
    url = f'https://www.crunchyroll.com/content/v2/cms/seasons/{season_id}/episodes'
    return client._request_with_retry('GET', url, params=params)


class GetSerieInfo:
    def __init__(self, series_id):
        """
        Args:
            - series_id (str): The Crunchyroll series ID.
        """
        self.series_id = series_id
        self.seasons_manager = SeasonManager()
        
        # Initialize Crunchyroll client
        self.client = CrunchyrollClient()
        if not self.client.start():
            raise Exception("Failed to authenticate with Crunchyroll")
        
        self.headers = self.client._get_headers()
        self.params = {
            'force_locale': '',
            'preferred_audio_language': 'it-IT',
            'locale': 'it-IT',
        }
        self.series_name = None
        self._episodes_cache = {}
        self.normalize_seasons = NORMALIZE_SEASON_NUMBERS

    def collect_season(self) -> None:
        """
        Retrieve all seasons.
        If normalize_season_numbers=True: assigns 1..N and keeps cr_number.
        """
        response = get_series_seasons(self.series_id, self.client, self.params)

        if response.status_code != 200:
            logging.error(f"Failed to fetch seasons for series {self.series_id}")
            return

        data = response.json()
        seasons = data.get("data", [])

        # Set series name from first season if available
        if seasons:
            self.series_name = seasons[0].get("series_title") or seasons[0].get("title")

        # Extract raw data
        rows = []
        for s in seasons:
            raw_num = s.get("season_number", 0)
            rows.append({
                "id": s.get('id'),
                "title": s.get("title", f"Season {raw_num}"),
                "raw_number": int(raw_num or 0),
                "slug": s.get("slug", ""),
            })

        # Sort by raw number then title for stability
        rows.sort(key=lambda r: (r["raw_number"], r["title"] or ""))

        if self.normalize_seasons:
            # Normalize: assign 1..N, keep original as cr_number
            for i, r in enumerate(rows, start=1):
                self.seasons_manager.add_season({
                    'number': i,
                    'cr_number': r["raw_number"],
                    'name': r["title"],
                    'id': r["id"],
                    'slug': r["slug"],
                })

        else:
            # No normalization: use CR's number directly
            for r in rows:
                self.seasons_manager.add_season({
                    'number': r["raw_number"],
                    'name': r["title"],
                    'id': r["id"],
                    'slug': r["slug"],
                })

    def _fetch_episodes_for_season(self, season_number: int) -> List[Dict]:
        """Fetch and cache episodes for a specific season number."""
        season = self.seasons_manager.get_season_by_number(season_number)
        if not season:
            logging.error(f"Season {season_number} not found")
            return []
            
        ep_response = get_season_episodes(season.id, self.client, self.params)

        if ep_response.status_code != 200:
            logging.error(f"Failed to fetch episodes for season {season.id}")
            return []

        ep_data = ep_response.json()
        episodes = ep_data.get("data", [])
        episode_list = []

        for ep in episodes:
            ep_num = ep.get("episode_number")
            ep_title = ep.get("title", f"Episodio {ep_num}")
            ep_id = ep.get("id")
            ep_url = f"https://www.crunchyroll.com/watch/{ep_id}"
            
            episode_list.append({
                'number': ep_num,
                'name': ep_title,
                'url': ep_url,
                'duration': int(ep.get('duration_ms', 0) / 60000),
            })
            
        self._episodes_cache[season_number] = episode_list
        return episode_list

    def _get_episode_audio_locales_and_urls(self, episode_id: str) -> Tuple[List[str], Dict[str, str]]:
        """
        Fetch available audio locales and their URLs for an episode.
        1. Try 'versions' from CMS
        2. Fallback to single audio_locale from metadata
        3. Last resort: query playback and cleanup token
        
        Returns: (audio_locales, urls_by_locale)
        """
        url = f'https://www.crunchyroll.com/content/v2/cms/objects/{episode_id}'
        params = {
            'ratings': 'true',
            'locale': 'it-IT',
        }
        
        try:
            response = self.client._request_with_retry('GET', url, params=params)
            
            if response.status_code != 200:
                logging.warning(f"Failed to fetch audio locales for episode {episode_id}")
                return [], {}
            
            data = response.json()
            item = (data.get("data") or [{}])[0] or {}
            meta = item.get('episode_metadata', {}) or {}

            # Strategy 1: versions array
            versions = meta.get("versions") or item.get("versions") or []
            audio_locales = []
            urls_by_locale = {}

            if versions:
                for v in versions:
                    if not isinstance(v, dict):
                        continue

                    locale = v.get("audio_locale")
                    guid = v.get("guid")
                    if locale and guid:
                        audio_locales.append(locale)
                        urls_by_locale[locale] = f"https://www.crunchyroll.com/watch/{guid}"
                
                if audio_locales:
                    return sorted(set(audio_locales)), urls_by_locale

            # Strategy 2: single audio_locale from metadata
            base_audio = (
                meta.get("audio_locale")
                or item.get("audio_locale")
                or (meta.get("audio") or {}).get("locale")
                or (item.get("audio") or {}).get("locale")
            )
            
            if base_audio:
                return [base_audio], {base_audio: f"https://www.crunchyroll.com/watch/{episode_id}"}
            
            # Strategy 3: query playback as last resort
            try:
                from .get_license import get_playback_session
                _url, _hdrs, _subs, token, audio_loc = get_playback_session(self.client, episode_id)
                
                # Cleanup token immediately
                if token:
                    try:
                        self.client.delete_active_stream(episode_id, token)
                    except Exception:
                        pass
                
                if audio_loc:
                    return [audio_loc], {audio_loc: f"https://www.crunchyroll.com/watch/{episode_id}"}
            except Exception as e:
                logging.warning(f"Playback fallback failed for {episode_id}: {e}")

            return [], {}
            
        except Exception as e:
            logging.error(f"Error parsing audio locales for episode {episode_id}: {e}")
            return [], {}

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """
        Get the total number of seasons available for the series.
        """
        if not self.seasons_manager.seasons:
            self.collect_season()
        return len(self.seasons_manager.seasons)
    
    def getEpisodeSeasons(self, season_number: int) -> list:
        """
        Get all episodes for a specific season (fetches only when needed).
        """
        if not self.seasons_manager.seasons:
            self.collect_season()
        if season_number not in self._episodes_cache:
            episodes = self._fetch_episodes_for_season(season_number)
        else:
            episodes = self._episodes_cache[season_number]
        return episodes

    def selectEpisode(self, season_number: int, episode_index: int) -> dict:
        """
        Get information for a specific episode in a specific season.
        """
        episodes = self.getEpisodeSeasons(season_number)
        if not episodes or episode_index < 0 or episode_index >= len(episodes):
            logging.error(f"Episode index {episode_index} is out of range for season {season_number}")
            return None

        episode = episodes[episode_index]
        episode_id = episode.get("url", "").split("/")[-1] if "url" in episode else None

        if not episode_id:
            return episode

        # Try to get best audio URL
        try:
            _, urls_by_locale = self._get_episode_audio_locales_and_urls(episode_id)
            new_url = urls_by_locale.get("it-IT") or urls_by_locale.get("en-US")
            
            if new_url:
                episode["url"] = new_url
                
        except Exception as e:
            logging.warning(f"Could not update episode URL: {e}")

        return episode