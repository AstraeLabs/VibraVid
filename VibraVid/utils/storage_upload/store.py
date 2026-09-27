# 12.06.26

import logging
import os
import threading

import requests

from VibraVid.utils.config import config_manager
from VibraVid.utils.http_client import create_client
from VibraVid.utils.storage_upload import client

logger = logging.getLogger(__name__)

STORE_URL = config_manager.config.get_dict("HOOKS", "db_info", default={}).get("url", "")
STORE_TOKEN = config_manager.config.get_dict("HOOKS", "db_info", default={}).get("token", "")


class ExternalUploadVault:
    def __init__(self):
        self.base_url = STORE_URL.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if STORE_TOKEN:
            headers["Authorization"] = f"Bearer {STORE_TOKEN}"

        self.api = create_client(headers=headers, http2=True)
        self.storage = requests.Session()
        self.upload_client = client.new_upload_client()
        self._lock = threading.Lock()

    def close(self):
        for s in (getattr(self, "api", None), getattr(self, "storage", None), getattr(self, "upload_client", None)):
            try:
                if s:
                    s.close()
            except Exception:
                pass

    def search(
        self, title: str, media_type: str | None = None, season: int | None = None, episode: int | None = None
    ) -> dict | None:
        if not self.base_url or not title:
            return None

        params: dict = {"title": title.strip()}
        if media_type:
            params["type"] = str(media_type).strip().lower()
        if season is not None:
            params["season"] = str(int(season))
        if episode is not None:
            params["episode"] = str(int(episode))
        try:
            with self._lock:
                r = self.api.get(f"{self.base_url}/search", params=params)
            r.raise_for_status()
            data = r.json()
            return data if data.get("found") else None
        except Exception as e:
            logger.debug(f"upload store search error: {e}")
            return None

    def upload(
        self,
        file_path: str,
        title: str | None = None,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        category: str | None = None,
        expiry_days: int | None = None,
        on_progress=None,
    ) -> str | None:
        if not self.base_url or not os.path.isfile(file_path):
            return None

        filename = os.path.basename(file_path)
        size = os.path.getsize(file_path)
        try:
            r = self.api.post(f"{self.base_url}/upload/target")
            r.raise_for_status()
            endpoint = r.json()["endpoint"]

            up = client.upload_file(
                self.upload_client, file_path, endpoint, filename=filename, on_progress=on_progress
            )

            register = {
                "filename": filename,
                "size": up.get("size", size),
                "storageUrl": up["url"],
            }
            if title:
                register["title"] = title
            if expiry_days is not None:
                register["expiryDays"] = expiry_days
            for k, v in (("mediaType", media_type), ("category", category)):
                if v:
                    register[k] = v
            if season is not None:
                register["season"] = season
            if episode is not None:
                register["episode"] = episode

            r = self.api.post(f"{self.base_url}/upload/register", json=register)
            r.raise_for_status()
            link = r.json().get("link")
            logger.info(f"upload store: uploaded {filename} -> {link}")
            return link
        except Exception as e:
            logger.error(f"upload store upload error: {e}", exc_info=True)
            return None

    def download(self, xh: str, dest_path: str, password: str | None = None, on_progress=None) -> str | None:
        if not self.base_url or not xh:
            return None

        try:
            with self._lock:
                r = self.api.get(f"{self.base_url}/upload/resolve/{xh}")
            r.raise_for_status()
            info = r.json()
            url = info.get("url")
            if not url:
                return None

            client.download_file(self.storage, url, dest_path, total=info.get("size"), on_progress=on_progress)
            logger.info(f"upload store: downloaded -> {dest_path}")
            return dest_path
        except Exception as e:
            logger.error(f"upload store download error: {e}", exc_info=True)
            return None

    def report_error(self, xh: str) -> None:
        if not self.base_url or not xh:
            return
        try:
            self.api.post(f"{self.base_url}/report/error", json={"xh": xh})
        except Exception as e:
            logger.debug(f"upload store report_error error: {e}")


is_upload_vault_valid = bool(STORE_URL)
upload_vault = ExternalUploadVault() if is_upload_vault_valid else None
