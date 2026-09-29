# 29.09.26

import hashlib
import logging
import os
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://mapple.fun"
DEFAULT_SOURCES = (
    "mapple",
    "s25",
    "s2",
    "s4",
    "s12",
    "s19",
    "s13",
    "s26",
    "s24",
    "s6",
    "s15",
    "s7",
    "s8",
    "s3",
    "s16",
    "s5",
    "s1",
    "s10",
)


def get_base_url() -> str:
    return os.environ.get("VIBRAVID_MAPPLE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def get_source_ids() -> tuple[str, ...]:
    raw = os.environ.get("VIBRAVID_MAPPLE_SOURCES", "")
    if not raw.strip():
        return DEFAULT_SOURCES

    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or DEFAULT_SOURCES


def get_player_url(
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
    base_url: str | None = None,
) -> str:
    base = (base_url or get_base_url()).rstrip("/")
    media_type = str(media_type or "").lower()

    if media_type == "movie":
        return f"{base}/watch/movie/{int(tmdb_id)}"

    if media_type == "tv":
        if season is None or episode is None:
            raise ValueError("[Mapple] season and episode are required for TV player URLs")
        return f"{base}/watch/tv/{int(tmdb_id)}/{int(season)}/{int(episode)}"

    raise ValueError(f"[Mapple] Unsupported media type: {media_type}")


@dataclass(frozen=True)
class MappleResolvedStream:
    url: str
    source: str
    headers: dict[str, str]


class MappleResolver:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        sources: tuple[str, ...] | list[str] | None = None,
        client_factory=None,
        timeout: int = 20,
        user_agent: str | None = None,
    ):
        self.base_url = (base_url or get_base_url()).rstrip("/")
        self.sources = tuple(sources or get_source_ids())
        self.client_factory = client_factory or create_client
        self.timeout = timeout
        self.user_agent = user_agent or get_userAgent()

    @staticmethod
    def _leading_zero_bits(value: bytes) -> int:
        count = 0
        for byte in value:
            if byte == 0:
                count += 8
                continue
            count += 8 - byte.bit_length()
            break
        return count

    @classmethod
    def solve_pow(
        cls,
        challenge: str,
        difficulty: int,
        *,
        max_attempts: int = 50_000_000,
    ) -> str:
        challenge = str(challenge or "")
        difficulty = int(difficulty)

        if not challenge:
            raise ValueError("[Mapple] Empty PoW challenge")
        if difficulty < 0 or difficulty > 256:
            raise ValueError(f"[Mapple] Invalid PoW difficulty: {difficulty}")

        prefix = challenge.encode("utf-8")
        for nonce in range(max_attempts):
            digest = hashlib.sha256(prefix + str(nonce).encode("ascii")).digest()
            if cls._leading_zero_bits(digest) >= difficulty:
                return str(nonce)

        raise RuntimeError(
            f"[Mapple] PoW nonce not found after {max_attempts} attempts "
            f"(difficulty={difficulty})"
        )

    def _watch_candidates(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None,
        episode: int | None,
    ) -> tuple[str, ...]:
        primary = get_player_url(
            tmdb_id,
            media_type,
            season,
            episode,
            base_url=self.base_url,
        )
        if media_type != "tv":
            return (primary,)

        fallback = (
            f"{self.base_url}/watch/tv/{int(tmdb_id)}-"
            f"{int(season)}-{int(episode)}"
        )
        return (primary, fallback)

    def _html_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{self.base_url}/",
        }

    def _api_headers(self, referer: str) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": referer,
        }

    def _stream_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }

    @staticmethod
    def _json_payload(response, context: str) -> dict:
        if not response.ok:
            raise RuntimeError(f"[Mapple] {context} returned HTTP {response.status_code}")

        try:
            data = response.json()
        except Exception as error:
            raise RuntimeError(f"[Mapple] {context} returned invalid JSON") from error

        if not isinstance(data, dict):
            raise RuntimeError(
                f"[Mapple] {context} returned unexpected payload: {type(data).__name__}"
            )

        return data

    def _warm_session(
        self,
        client,
        tmdb_id: int,
        media_type: str,
        season: int | None,
        episode: int | None,
    ) -> str:
        errors = []

        for page_url in self._watch_candidates(
            tmdb_id,
            media_type,
            season,
            episode,
        ):
            try:
                response = client.get(
                    page_url,
                    headers=self._html_headers(),
                    timeout=self.timeout,
                )
            except Exception as error:
                errors.append(f"{page_url}: {type(error).__name__}")
                continue

            content_type = (response.headers.get("content-type") or "").lower()
            if response.ok and ("html" in content_type or not content_type):
                return page_url

            errors.append(f"{page_url}: HTTP {response.status_code}")

        detail = "; ".join(errors)
        raise RuntimeError(f"[Mapple] Player page is unavailable: {detail}")

    def _request_token(self, client, referer: str) -> str:
        response = client.post(
            f"{self.base_url}/api/request-token",
            headers=self._api_headers(referer),
            data=b"",
            timeout=self.timeout,
        )
        data = self._json_payload(response, "request-token")
        token = str(data.get("token") or "").strip()
        if not token:
            raise RuntimeError("[Mapple] request-token response did not contain a token")
        return token

    def _playback_token(
        self,
        client,
        *,
        tmdb_id: int,
        media_type: str,
        season: int | None,
        episode: int | None,
        request_token: str,
        referer: str,
    ) -> str:
        tv_slug = (
            f"{int(season)}-{int(episode)}"
            if media_type == "tv"
            else ""
        )
        payload = {
            "mediaId": int(tmdb_id),
            "mediaType": media_type,
            "tv_slug": tv_slug,
            "requestToken": request_token,
        }

        response = client.post(
            f"{self.base_url}/api/playback-init",
            headers=self._api_headers(referer),
            json=payload,
            timeout=self.timeout,
        )
        data = self._json_payload(response, "playback-init")

        token = str(data.get("token") or "").strip()
        if token:
            return token

        pow_data = data.get("pow")
        if data.get("requiresPow") is not True or not isinstance(pow_data, dict):
            raise RuntimeError("[Mapple] playback-init did not return a playback token or PoW challenge")

        challenge_id = str(pow_data.get("challengeId") or "").strip()
        challenge = str(pow_data.get("challenge") or "").strip()
        difficulty = pow_data.get("difficulty")

        if not challenge_id or not challenge or difficulty is None:
            raise RuntimeError("[Mapple] playback-init returned an incomplete PoW challenge")

        nonce = self.solve_pow(challenge, int(difficulty))
        solved_payload = dict(payload)
        solved_payload["pow"] = {
            "challengeId": challenge_id,
            "nonce": nonce,
        }

        solved_response = client.post(
            f"{self.base_url}/api/playback-init",
            headers=self._api_headers(referer),
            json=solved_payload,
            timeout=self.timeout,
        )
        solved = self._json_payload(solved_response, "playback-init PoW")

        token = str(solved.get("token") or "").strip()
        if not token:
            raise RuntimeError("[Mapple] PoW was accepted without returning a playback token")

        return token

    @staticmethod
    def _append_query(url: str, **params: str) -> str:
        parts = urlsplit(url)
        existing = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in params
        ]
        existing.extend((key, value) for key, value in params.items())
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(existing),
                parts.fragment,
            )
        )

    @staticmethod
    def _ensure_hls_hint(stream_url: str) -> str:
        if not stream_url:
            return stream_url

        lowered = stream_url.lower()
        if ("omena-puu" not in lowered and "nocach" not in lowered):
            return stream_url

        parts = urlsplit(stream_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "format" in query:
            return stream_url

        return MappleResolver._append_query(stream_url, format=".m3u8")

    def _manifest_is_playable(
        self,
        client,
        stream_url: str,
    ) -> bool:
        """Return whether a resolved Mapple URL is a readable HLS manifest."""
        try:
            response = client.get(
                stream_url,
                headers=self._stream_headers(),
                timeout=self.timeout,
            )
        except Exception as error:
            logger.debug("[Mapple] manifest probe failed: %s", error)
            return False

        if not response.ok:
            logger.debug(
                "[Mapple] manifest probe returned HTTP %s",
                response.status_code,
            )
            return False

        try:
            content = response.text
        except Exception as error:
            logger.debug("[Mapple] manifest probe body read failed: %s", error)
            return False

        if not str(content or "").lstrip().startswith("#EXTM3U"):
            logger.debug("[Mapple] resolved source is not an HLS manifest")
            return False

        return True

    def _resolve_source(
        self,
        client,
        *,
        source: str,
        tmdb_id: int,
        media_type: str,
        season: int | None,
        episode: int | None,
        request_token: str,
        playback_token: str,
        referer: str,
    ) -> MappleResolvedStream | None:
        tv_slug = (
            f"{int(season)}-{int(episode)}"
            if media_type == "tv"
            else ""
        )
        payload = {
            "data": {
                "mediaId": int(tmdb_id),
                "mediaType": media_type,
                "tv_slug": tv_slug,
                "source": source,
            },
            "endpoint": "stream-encrypted",
            "requestToken": request_token,
        }

        response = client.post(
            f"{self.base_url}/api/encrypt",
            headers=self._api_headers(referer),
            json=payload,
            timeout=self.timeout,
        )

        if not response.ok:
            logger.debug("[Mapple] source %s encrypt returned HTTP %s", source, response.status_code)
            return None

        try:
            encrypted = response.json()
        except Exception:
            logger.debug("[Mapple] source %s encrypt returned invalid JSON", source)
            return None

        if not isinstance(encrypted, dict):
            return None

        endpoint = str(encrypted.get("url") or "").strip()
        if not endpoint and encrypted.get("encrypted"):
            endpoint = (
                "/api/stream-encrypted?data="
                + quote(str(encrypted["encrypted"]), safe="")
            )

        if not endpoint:
            logger.debug("[Mapple] source %s encrypt response had no endpoint", source)
            return None

        endpoint = urljoin(f"{self.base_url}/", endpoint)
        endpoint = self._append_query(
            endpoint,
            requestToken=request_token,
            token=playback_token,
        )

        stream_response = client.get(
            endpoint,
            headers=self._api_headers(referer),
            timeout=self.timeout,
        )

        if not stream_response.ok:
            logger.debug(
                "[Mapple] source %s stream endpoint returned HTTP %s",
                source,
                stream_response.status_code,
            )
            return None

        try:
            stream_data = stream_response.json()
        except Exception:
            logger.debug("[Mapple] source %s stream endpoint returned invalid JSON", source)
            return None

        if not isinstance(stream_data, dict) or stream_data.get("success") is not True:
            return None

        data = stream_data.get("data")
        if not isinstance(data, dict):
            return None

        stream_url = str(data.get("stream_url") or "").strip()
        if not stream_url or "playback-unavailable" in stream_url.lower():
            return None

        stream_url = self._ensure_hls_hint(stream_url)

        if not self._manifest_is_playable(client, stream_url):
            logger.debug("[Mapple] source %s returned an unusable HLS manifest", source)
            return None

        return MappleResolvedStream(
            url=stream_url,
            source=source,
            headers=self._stream_headers(),
        )

    def resolve_stream(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> MappleResolvedStream:
        media_type = str(media_type or "").lower()
        if media_type not in {"movie", "tv"}:
            raise ValueError(f"[Mapple] Unsupported media type: {media_type}")
        if media_type == "tv" and (season is None or episode is None):
            raise ValueError("[Mapple] season and episode are required for TV media resolution")

        client = self.client_factory(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            browser=None,
        )

        try:
            referer = self._warm_session(
                client,
                int(tmdb_id),
                media_type,
                season,
                episode,
            )
            request_token = self._request_token(client, referer)
            playback_token = self._playback_token(
                client,
                tmdb_id=int(tmdb_id),
                media_type=media_type,
                season=season,
                episode=episode,
                request_token=request_token,
                referer=referer,
            )

            unavailable = []
            for source in self.sources:
                try:
                    result = self._resolve_source(
                        client,
                        source=source,
                        tmdb_id=int(tmdb_id),
                        media_type=media_type,
                        season=season,
                        episode=episode,
                        request_token=request_token,
                        playback_token=playback_token,
                        referer=referer,
                    )
                except Exception as error:
                    logger.debug("[Mapple] source %s failed: %s", source, error)
                    unavailable.append(f"{source}={type(error).__name__}")
                    continue

                if result is not None:
                    logger.info("[Mapple] selected source %s", source)
                    return result

                unavailable.append(f"{source}=unavailable")

        finally:
            try:
                client.close()
            except Exception:
                pass

        detail = "; ".join(unavailable)
        raise RuntimeError(
            f"[Mapple] No source backend is currently available"
            + (f" ({detail})" if detail else "")
        )

    def player_is_available(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> bool:
        media_type = str(media_type or "").lower()
        client = self.client_factory(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            browser=None,
        )

        try:
            self._warm_session(
                client,
                int(tmdb_id),
                media_type,
                season,
                episode,
            )
            return True
        except Exception:
            return False
        finally:
            try:
                client.close()
            except Exception:
                pass
