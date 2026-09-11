# 01.04.26

import logging
import re

logger = logging.getLogger(__name__)


class KeysManager:
    _HEX_PAIR_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})\s*:\s*([0-9a-fA-F]{32})(?![0-9a-fA-F])")
    _HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
    _SPLIT_RE = re.compile(r"[|,\s]+")

    def __init__(self, keys=None) -> None:
        self._keys: list[tuple[str, str]] = []
        if keys:
            self.add_keys(keys)

    def add_keys(self, keys) -> None:
        """Parse *keys* (any supported form), normalise, validate and append, skipping duplicates."""
        for kid, key in self._iter_pairs(keys):
            ckid, ckey = self._clean(kid), self._clean(key)

            if not self._HEX32_RE.match(ckey):
                logger.warning(f"Skipping key with invalid format (expected 32 hex chars, got len={len(ckey)}): kid={ckid[:8]}...")
                continue

            if ckid != "1" and not self._HEX32_RE.match(ckid):
                logger.warning(f"Skipping pair with invalid KID (expected 32 hex chars, got len={len(ckid)}): kid={ckid}")
                continue

            if ckid == ckey and ckid != "1":
                logger.warning(f"Skipping key where KID == KEY (always invalid): kid={ckid}")
                continue

            pair = (ckid, ckey)
            if pair not in self._keys:
                self._keys.append(pair)

    def get_keys_list(self) -> list[str]:
        """Return keys as a list of clean ``"kid:key"`` strings."""
        return [f"{kid}:{key}" for kid, key in self._keys]

    @staticmethod
    def _clean(value: str) -> str:
        """Canonical form for a KID or KEY: dash-stripped, trimmed, lowercase hex."""
        return str(value).replace("-", "").strip().lower()

    @classmethod
    def _iter_pairs(cls, keys):
        """Yield raw (uncleaned) ``(kid, key)`` pairs from any supported representation."""
        if not keys:
            return

        if isinstance(keys, KeysManager):
            yield from keys._keys
            return

        if isinstance(keys, dict):
            kid, key = keys.get("kid", ""), keys.get("key", "")
            if kid and key:
                yield (kid, key)
            return

        if isinstance(keys, (list, tuple)):
            # A bare 2-element pair of plain values (no ':' in the first) is one (kid, key).
            if len(keys) == 2 and all(isinstance(v, str) for v in keys) and ":" not in keys[0]:
                yield (keys[0], keys[1])
            else:
                for item in keys:
                    yield from cls._iter_pairs(item)
            return

        if isinstance(keys, str):
            s = keys.strip()
            if not s:
                return

            matches = list(cls._HEX_PAIR_RE.finditer(s))
            if matches:
                remainder = s
                for m in reversed(matches):
                    remainder = remainder[: m.start()] + " " + remainder[m.end() :]
                for token in cls._SPLIT_RE.split(remainder):
                    if token and ":" in token:
                        logger.warning(f"Skipping malformed key segment (expected 32 hex chars on each side of ':'): {token!r}")
                yield from (m.groups() for m in matches)
                return

            # Generic path: split on separators, then on the first ':' of each token.
            for token in cls._SPLIT_RE.split(s):
                if not token:
                    continue
                if ":" in token:
                    kid, key = token.split(":", 1)
                    yield (kid, key)
                else:
                    # Bare key with no KID (e.g. raw ClearKey) — pair with placeholder.
                    yield ("1", token)
            return

    @classmethod
    def normalize(cls, keys) -> list[tuple[str, str]]:
        """Coerce any supported key representation into a list of ``(kid, key)`` lowercase hex string tuples."""
        return list(cls(keys))

    @staticmethod
    def is_zero_kid(kid: str | None) -> bool:
        """Return True when *kid* is all-zero hex (fixed-key stream)."""
        return bool(kid and kid.lower() == "0" * len(kid))

    @classmethod
    def resolve_placeholder_kid(
        cls, detected_kid: str | None, normalized_keys: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """A single key supplied with the "1" no-KID placeholder should be replaced with the detected KID, if available and non-zero."""
        if len(normalized_keys) != 1 or normalized_keys[0][0] != "1":
            return normalized_keys
        if not detected_kid or cls.is_zero_kid(detected_kid):
            return normalized_keys
        return [(detected_kid.lower(), normalized_keys[0][1])]

    @classmethod
    def resolve_fixed_key(
        cls, encrypted_path: str, detected_kid: str | None, normalized_keys: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """For fixed-key streams (all-zero KID) with multiple candidates, refuse to guess.

        `detected_kid` already comes from `flux -d -j`'s `default_kid`.
        """
        if not cls.is_zero_kid(detected_kid) or len(normalized_keys) <= 1:
            return normalized_keys

        logger.error(f"Fixed-key stream ({encrypted_path}) with multiple candidate keys but no resolvable KID (flux found no matching PSSH either); refusing to guess a key")
        return []

    def __len__(self) -> int:
        return len(self._keys)

    def __iter__(self):
        return iter(self._keys)

    def __getitem__(self, index):
        return self._keys[index]

    def __bool__(self) -> bool:
        return len(self._keys) > 0
