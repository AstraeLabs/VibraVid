# 25.03.26
# ruff: noqa: E402

from dataclasses import dataclass, field


@dataclass
class MockDRM:
    """Minimal stand-in for manifest.stream.DRMInfo, just enough for `_is_encrypted()`."""

    encrypted: bool = False

    def is_encrypted(self) -> bool:
        return self.encrypted


@dataclass
class MockStream:
    """Mock stream object simulating real manifest streams."""

    type: str  # "video", "audio", "subtitle"

    # Common
    id: str = None
    codecs: str = None
    bitrate: int = None
    drm: object = None
    encrypted: bool = field(default=False, repr=False)

    # Video specific
    height: int = None
    width: int = None
    resolution: str = None
    video_range: str = None

    # Dolby Vision
    dv_companion: bool = field(default=False, repr=False)
    dv_companion_quality: str = field(default=None, repr=False)

    # Audio/Subtitle specific
    language: str = None
    resolved_language: str = None

    # Subtitle specific
    forced: bool = False
    is_cc: bool = False
    is_sdh: bool = False

    # Flags
    default: bool = False
    playlist_url: str = "mock://playlist"

    # Runtime state (not persisted)
    selected: bool = field(default=False, repr=False)

    def __post_init__(self):
        if self.drm is None and self.encrypted:
            self.drm = MockDRM(True)

    def __repr__(self):
        if self.type == "video":
            return f"Video({self.height}p, {self.codecs}, {self.bitrate // 1000}kbps, id={self.id})"
        elif self.type == "audio":
            return f"Audio({self.language}/{self.resolved_language}, {self.codecs}, {self.bitrate // 1000}kbps, id={self.id})"
        elif self.type == "subtitle":
            return f"Sub({self.language}/{self.resolved_language}, forced={self.forced}, cc={self.is_cc}, id={self.id})"
        return f"{self.type}(id={self.id})"


def create_video_streams_example1():
    """Example 1: Multiple resolutions with different codecs."""
    return [
        MockStream(type="video", height=480, codecs="avc1", bitrate=500_000, id="v0"),
        MockStream(type="video", height=720, codecs="avc1", bitrate=1_000_000, id="v1"),
        MockStream(type="video", height=1080, codecs="avc1", bitrate=3_000_000, id="v2"),
        MockStream(type="video", height=1080, codecs="hvc1", bitrate=2_000_000, id="v3"),
        MockStream(type="video", height=2160, codecs="avc1", bitrate=8_000_000, id="v4"),
    ]


def create_video_streams_example2():
    """Example 2: Limited resolutions, no 1080p."""
    return [
        MockStream(type="video", height=480, codecs="avc1", bitrate=500_000, id="v0"),
        MockStream(type="video", height=720, codecs="avc1", bitrate=1_000_000, id="v1"),
        MockStream(type="video", height=720, codecs="hvc1", bitrate=800_000, id="v2"),
    ]


def create_audio_streams_with_regions():
    """Example: same base language (English) split across two region locales, for testing region-specific locale codes (e.g. "en-au") vs generic 2/3-letter codes."""
    return [
        MockStream(type="audio", language="eng", resolved_language="en-US", codecs="mp4a", bitrate=128_000, id="a_us"),
        MockStream(type="audio", language="eng", resolved_language="en-AU", codecs="mp4a", bitrate=128_000, id="a_au"),
    ]


def create_audio_streams_example1():
    """Example 1: Multiple languages and codecs."""
    return [
        MockStream(type="audio", language="eng", resolved_language="en-US", codecs="mp4a", bitrate=128_000, id="a0"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="mp4a", bitrate=128_000, id="a1"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ac-3", bitrate=256_000, id="a2"),
        MockStream(type="audio", language="fra", resolved_language="fr-FR", codecs="mp4a", bitrate=128_000, id="a3"),
        MockStream(type="audio", language="deu", resolved_language="de-DE", codecs="mp4a", bitrate=192_000, id="a4"),
    ]


def create_audio_streams_example2():
    """Example 2: Only English audio available."""
    return [
        MockStream(type="audio", language="eng", resolved_language="en-US", codecs="mp4a", bitrate=128_000, id="a0"),
        MockStream(type="audio", language="eng", resolved_language="en-US", codecs="ac-3", bitrate=256_000, id="a1"),
    ]


def create_audio_streams_example3():
    """Example 3: Multiple Italian tracks with different qualities."""
    return [
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="mp4a", bitrate=128_000, id="a0"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ac-3", bitrate=256_000, id="a1"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="eac3", bitrate=192_000, id="a2"),
        MockStream(type="audio", language="eng", resolved_language="en-US", codecs="mp4a", bitrate=128_000, id="a3"),
    ]


def create_subtitle_streams_example1():
    """Example 1: Multiple languages with flags."""
    return [
        MockStream(type="subtitle", language="ita", resolved_language="it-IT", id="s0"),
        MockStream(type="subtitle", language="ita", resolved_language="it-IT", forced=True, id="s1"),
        MockStream(type="subtitle", language="eng", resolved_language="en-US", id="s2"),
        MockStream(type="subtitle", language="eng", resolved_language="en-US", is_cc=True, id="s3"),
        MockStream(type="subtitle", language="fra", resolved_language="fr-FR", id="s4"),
    ]


def create_full_manifest_streams():
    """Example: Complete manifest with video, audio, subtitle."""
    video_streams = create_video_streams_example1()
    audio_streams = create_audio_streams_example1()
    subtitle_streams = create_subtitle_streams_example1()
    return video_streams + audio_streams + subtitle_streams


def create_audio_streams_with_default():
    """Example: Audio streams where one is marked as default."""
    return [
        MockStream(
            type="audio",
            language="eng",
            resolved_language="en-US",
            codecs="mp4a",
            bitrate=128_000,
            id="a0",
            default=True,
        ),
        MockStream(
            type="audio",
            language="ita",
            resolved_language="it-IT",
            codecs="ac-3",
            bitrate=256_000,
            id="a1",
            default=False,
        ),
        MockStream(
            type="audio",
            language="fra",
            resolved_language="fr-FR",
            codecs="mp4a",
            bitrate=192_000,
            id="a2",
            default=False,
        ),
    ]


def create_subtitle_streams_with_default():
    """Example: Subtitle streams where some are marked as default."""
    return [
        MockStream(type="subtitle", language="eng", resolved_language="en-US", default=True, id="s0"),
        MockStream(type="subtitle", language="eng", resolved_language="en-US", is_cc=True, id="s1", default=False),
        MockStream(type="subtitle", language="ita", resolved_language="it-IT", forced=True, id="s2", default=False),
    ]
