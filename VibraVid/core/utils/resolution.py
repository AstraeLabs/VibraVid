# 19.08.26

_WIDTH_TOLERANCE = 0.98
RESOLUTION_TIERS = [
    (4320, 7680, "4320p"),
    (2880, 5120, "2880p"),
    (2160, 3840, "2160p"),
    (1440, 2560, "1440p"),
    (1080, 1920, "1080p"),
    (900, 1600, "900p"),
    (768, 1366, "768p"),
    (720, 1280, "720p"),
    (540, 960, "540p"),
    (480, 854, "480p"),
    (360, 640, "360p"),
    (240, 426, "240p"),
    (144, 256, "144p"),
]


def classify_resolution(width, height) -> str:
    for min_h, min_w, label in RESOLUTION_TIERS:
        if (height and height >= min_h) or (width and width >= min_w * _WIDTH_TOLERANCE):
            return label
    if height:
        return f"{height}p"
    return ""
