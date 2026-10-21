# 21.10.26

import logging
import os
import re
from dataclasses import dataclass

from rich.console import Console

from VibraVid.utils import config_manager

from .font import FONT_FILE_EXTENSIONS, FontFace, FontManager, face_from_filename, parse_face_name

console = Console()
logger = logging.getLogger(__name__)
FALLBACK_POOL_URL = "https://static.crunchyroll.com/vilos-v2/web/vilos/assets/libass-fonts/"
FALLBACK_POOL = {
    "Adobe Arabic": "AdobeArabic-Bold.otf",
    "Andale Mono": "andalemo.ttf",
    "Arial": "arial.ttf",
    "Arial Black": "ariblk.ttf",
    "Arial Bold": "arialbd.ttf",
    "Arial Bold Italic": "arialbi.ttf",
    "Arial Italic": "ariali.ttf",
    "Arial Unicode MS": "arialuni.ttf",
    "Comic Sans MS": "comic.ttf",
    "Comic Sans MS Bold": "comicbd.ttf",
    "Courier New": "cour.ttf",
    "Courier New Bold": "courbd.ttf",
    "Courier New Bold Italic": "courbi.ttf",
    "Courier New Italic": "couri.ttf",
    "DejaVu LGC Sans Mono": "DejaVuLGCSansMono.ttf",
    "DejaVu LGC Sans Mono Bold": "DejaVuLGCSansMono-Bold.ttf",
    "DejaVu LGC Sans Mono Bold Oblique": "DejaVuLGCSansMono-BoldOblique.ttf",
    "DejaVu LGC Sans Mono Oblique": "DejaVuLGCSansMono-Oblique.ttf",
    "DejaVu Sans": "DejaVuSans.ttf",
    "DejaVu Sans Bold": "DejaVuSans-Bold.ttf",
    "DejaVu Sans Bold Oblique": "DejaVuSans-BoldOblique.ttf",
    "DejaVu Sans Condensed": "DejaVuSansCondensed.ttf",
    "DejaVu Sans Condensed Bold": "DejaVuSansCondensed-Bold.ttf",
    "DejaVu Sans Condensed Bold Oblique": "DejaVuSansCondensed-BoldOblique.ttf",
    "DejaVu Sans Condensed Oblique": "DejaVuSansCondensed-Oblique.ttf",
    "DejaVu Sans ExtraLight": "DejaVuSans-ExtraLight.ttf",
    "DejaVu Sans Mono": "DejaVuSansMono.ttf",
    "DejaVu Sans Mono Bold": "DejaVuSansMono-Bold.ttf",
    "DejaVu Sans Mono Bold Oblique": "DejaVuSansMono-BoldOblique.ttf",
    "DejaVu Sans Mono Oblique": "DejaVuSansMono-Oblique.ttf",
    "DejaVu Sans Oblique": "DejaVuSans-Oblique.ttf",
    "Gautami": "gautami.ttf",
    "Georgia": "georgia.ttf",
    "Georgia Bold": "georgiab.ttf",
    "Georgia Bold Italic": "georgiaz.ttf",
    "Georgia Italic": "georgiai.ttf",
    "Impact": "impact.ttf",
    "Meera Inimai": "MeeraInimai-Regular.ttf",
    "Mangal": "MANGAL.TTF",
    "Noto Sans Thai": "NotoSansThai.ttf",
    "Noto Sans Telugu": "NotoSansTeluguVariable.ttf",
    "Noto Sans Tamil": "NotoSansTamilVariable.ttf",
    "Rubik": "Rubik-Regular.ttf",
    "Rubik Black": "Rubik-Black.ttf",
    "Rubik Black Italic": "Rubik-BlackItalic.ttf",
    "Rubik Bold": "Rubik-Bold.ttf",
    "Rubik Bold Italic": "Rubik-BoldItalic.ttf",
    "Rubik Italic": "Rubik-Italic.ttf",
    "Rubik Light": "Rubik-Light.ttf",
    "Rubik Light Italic": "Rubik-LightItalic.ttf",
    "Rubik Medium": "Rubik-Medium.ttf",
    "Rubik Medium Italic": "Rubik-MediumItalic.ttf",
    "Tahoma": "tahoma.ttf",
    "Times New Roman": "times.ttf",
    "Times New Roman Bold": "timesbd.ttf",
    "Times New Roman Bold Italic": "timesbi.ttf",
    "Times New Roman Italic": "timesi.ttf",
    "Trebuchet MS": "trebuc.ttf",
    "Trebuchet MS Bold": "trebucbd.ttf",
    "Trebuchet MS Bold Italic": "trebucbi.ttf",
    "Trebuchet MS Italic": "trebucit.ttf",
    "Verdana": "verdana.ttf",
    "Verdana Bold": "verdanab.ttf",
    "Verdana Bold Italic": "verdanaz.ttf",
    "Verdana Italic": "verdanai.ttf",
    "Vrinda": "vrinda.ttf",
    "Vrinda Bold": "vrindab.ttf",
    "Webdings": "webdings.ttf",
}

ASS_EXTENSIONS = (".ass", ".ssa")
MAX_FONT_BYTES = 50 * 1024 * 1024
_FONT_MIME = {
    ".otf": "application/vnd.ms-opentype",
    ".ttf": "application/x-truetype-font",
    ".ttc": "application/x-truetype-font",
    ".dfont": "application/x-truetype-font",
}
_FN_TAG = re.compile(r"\\fn([^\\}]+)")
_BOLD_TAG = re.compile(r"\\b(?:[1-9]\d*)(?![\d])")
_ITALIC_TAG = re.compile(r"\\i1(?![\d])")
_DOWNLOAD_TIMEOUT = 20

_failed_downloads: set[str] = set()
_pool_unreachable = False
_system_font_manager = FontManager()


@dataclass(frozen=True)
class FontAttachment:
    name: str
    path: str
    mime: str


def embed_fonts_enabled() -> bool:
    return config_manager.config.get_bool("PROCESS", "embed_fonts", default=True)


def user_fonts_dir() -> str:
    """Folder for fonts the user drops in by hand: neither system-installed nor downloaded."""
    return os.path.join(config_manager.base_path, ".cache", "fonts")


def pool_fonts_dir() -> str:
    """Folder where the fonts of the Crunchyroll pool (FALLBACK_POOL) are downloaded, next to the rest of that service's cache."""
    return os.path.join(config_manager.base_path, ".cache", "services", "crunchyroll", "fonts")


def _is_ass(path: str) -> bool:
    return os.path.splitext(str(path))[1].lower() in ASS_EXTENSIONS


def _flag(value: str) -> bool:
    try:
        return int(value.strip()) != 0
    except ValueError:
        return False


def ass_font_usage(subtitle_path: str) -> list[tuple[str, bool, bool]]:
    """Return the (family, bold, italic) combinations an ASS/SSA file asks for: the `Fontname` of each Style plus every `\\fn` override."""
    try:
        with open(subtitle_path, encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        logger.warning(f"font_embed: could not read {subtitle_path}: {e}")
        return []

    usage: dict[tuple[str, bool, bool], str] = {}

    def _add(family: str, bold: bool, italic: bool) -> None:
        family = family.strip().lstrip("@")
        if family:
            usage.setdefault((family.lower(), bold, italic), family)

    in_styles = False
    in_events = False
    fields: list[str] = []
    bold_override = italic_override = False
    override_families: list[str] = []

    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()

        if lowered.startswith("["):
            in_styles = "styles" in lowered
            in_events = lowered == "[events]"
            continue

        if in_styles:
            if lowered.startswith("format:"):
                fields = [x.strip().lower() for x in stripped.split(":", 1)[1].split(",")]
            elif lowered.startswith("style:") and "fontname" in fields:
                parts = stripped.split(":", 1)[1].lstrip().split(",", len(fields) - 1)
                name_idx = fields.index("fontname")
                if len(parts) <= name_idx:
                    continue
                bold = _flag(parts[fields.index("bold")]) if "bold" in fields and len(parts) > fields.index("bold") else False
                italic = _flag(parts[fields.index("italic")]) if "italic" in fields and len(parts) > fields.index("italic") else False
                _add(parts[name_idx], bold, italic)
        elif in_events and lowered.startswith("dialogue:"):
            text = stripped
            override_families.extend(m.group(1) for m in _FN_TAG.finditer(text))
            bold_override = bold_override or bool(_BOLD_TAG.search(text))
            italic_override = italic_override or bool(_ITALIC_TAG.search(text))

    for family in override_families:
        _add(family, False, False)

    # A `\b1` / `\i1` override anywhere can switch any used family to its bold/italic face
    if bold_override or italic_override:
        for (_key, bold, italic), family in list(usage.items()):
            if bold_override:
                _add(family, True, italic)
            if italic_override:
                _add(family, bold, True)
            if bold_override and italic_override:
                _add(family, True, True)

    return [(name, b, i) for (_k, b, i), name in usage.items()]


def _pool_faces() -> dict[tuple[str, bool, bool], str]:
    pool: dict[tuple[str, bool, bool], str] = {}
    for face_name, filename in FALLBACK_POOL.items():
        pool.setdefault(parse_face_name(face_name), filename)
    return pool


def _match(faces: list[FontFace], family: str, bold: bool, italic: bool) -> FontFace | None:
    for face in faces:
        if face.family == family and face.bold == bold and face.italic == italic:
            return face
    return None


def _fallback(faces: list[FontFace], family: str) -> FontFace | None:
    """Same family, wrong weight/slant: libass fakes the missing style, so the regular face (or any) is still useful."""
    same = [f for f in faces if f.family == family]
    for face in same:
        if not face.bold and not face.italic:
            return face
    return same[0] if same else None


def _mark_pool_unreachable(reason: str) -> None:
    """The pool server is down or unreachable: stop asking for the rest of this run instead of waiting on every font."""
    global _pool_unreachable
    if _pool_unreachable:
        return
    _pool_unreachable = True
    logger.warning(f"font_embed: Crunchyroll font pool unreachable ({reason})")
    console.print(f"[yellow]    Crunchyroll font pool unreachable ({reason}): using the fonts found on this machine")


def _download_pool_font(filename: str) -> str | None:
    """Download one font of the fallback pool into the Crunchyroll fonts folder (skipped if already there). Never raises."""
    target_dir = pool_fonts_dir()
    target = os.path.join(target_dir, filename)
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target
    if filename in _failed_downloads or _pool_unreachable:
        return None

    try:
        from VibraVid.utils.http_client import create_client

        if os.path.isfile(target):
            os.remove(target)  # zero-byte leftover

        client = create_client(timeout=_DOWNLOAD_TIMEOUT)
        try:
            response = client.get(FALLBACK_POOL_URL + filename)
        finally:
            client.close()

        if response.status_code >= 500:
            _mark_pool_unreachable(f"HTTP {response.status_code}")
            return None
        if response.status_code != 200 or not response.content:
            raise RuntimeError(f"HTTP {response.status_code}")

        os.makedirs(target_dir, exist_ok=True)
        tmp = f"{target}.part"
        with open(tmp, "wb") as f:
            f.write(response.content)
        os.replace(tmp, target)
        logger.info(f"font_embed: downloaded {filename} into {target_dir}")
        return target

    except Exception as e:
        _failed_downloads.add(filename)
        logger.warning(f"font_embed: could not download {filename}: {e}")
        if not str(e).startswith("HTTP "):
            _mark_pool_unreachable(str(e))
        return None


def _user_faces() -> list[FontFace]:
    """Faces for the files in the user fonts folder and the downloaded pool folder (pool files keep the exact face name they were published under)."""
    by_filename = {name.lower(): face_name for face_name, name in FALLBACK_POOL.items()}
    faces = []
    seen = set()
    for folder in (pool_fonts_dir(), user_fonts_dir()):
        if not os.path.isdir(folder):
            continue

        for entry in sorted(os.listdir(folder)):
            path = os.path.join(folder, entry)
            key = os.path.normcase(os.path.abspath(path))
            if key in seen or not entry.lower().endswith(FONT_FILE_EXTENSIONS) or not os.path.isfile(path):
                continue
            seen.add(key)

            known = by_filename.get(entry.lower())
            if known:
                family, bold, italic = parse_face_name(known)
                faces.append(FontFace(family, bold, italic, path))
            else:
                faces.append(face_from_filename(path))
    return faces


def resolve_font_file(family: str, bold: bool, italic: bool, faces: list[FontFace]) -> str | None:
    """Find the file for one requested face: exact local face, exact pool face (downloaded), any local face of the family, regular pool face."""
    wanted_family, name_bold, name_italic = parse_face_name(family)
    bold = bold or name_bold
    italic = italic or name_italic

    # The Crunchyroll pool wins over the system fonts: it is what the service's own player renders with.
    pool_dir = os.path.normcase(os.path.abspath(pool_fonts_dir()))
    pool_downloaded = [f for f in faces if os.path.normcase(os.path.abspath(f.path)).startswith(pool_dir + os.sep)]
    exact = _match(pool_downloaded, wanted_family, bold, italic)
    if exact:
        return exact.path

    pool = _pool_faces()
    pool_file = pool.get((wanted_family, bold, italic))
    if pool_file:
        downloaded = _download_pool_font(pool_file)
        if downloaded:
            return downloaded

    exact = _match(faces, wanted_family, bold, italic)
    if exact:
        return exact.path

    near = _fallback(faces, wanted_family)
    if near:
        return near.path

    pool_regular = pool.get((wanted_family, False, False))
    if pool_regular:
        return _download_pool_font(pool_regular)

    return None


def collect_font_attachments(subtitle_paths: list, out_path: str) -> list[FontAttachment]:
    """Fonts to attach to *out_path*: the files behind every font used by the ASS/SSA subtitles being muxed."""
    if os.path.splitext(str(out_path))[1].lower() != ".mkv" or not embed_fonts_enabled():
        return []

    ass_paths = [str(p) for p in subtitle_paths if _is_ass(str(p))]
    if not ass_paths:
        return []

    faces = _user_faces() + _system_font_manager.get_font_faces()
    attachments: dict[str, FontAttachment] = {}
    missing: set[str] = set()

    for subtitle in ass_paths:
        for family, bold, italic in ass_font_usage(subtitle):
            path = resolve_font_file(family, bold, italic, faces)
            if not path:
                missing.add(family)
                continue

            key = os.path.normcase(os.path.abspath(path))
            if key in attachments:
                continue

            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size == 0 or size > MAX_FONT_BYTES:
                logger.warning(f"font_embed: skipping {path} ({size} bytes)")
                continue

            ext = os.path.splitext(path)[1].lower()
            attachments[key] = FontAttachment(os.path.basename(path), path, _FONT_MIME.get(ext, "application/octet-stream"))

    for family in sorted(missing):
        console.print(f"[yellow]    No font file found for '{family}': it will not be embedded")

    if attachments:
        logger.info(f"font_embed: attaching {[a.name for a in attachments.values()]}")

    return list(attachments.values())


def ffmpeg_attach_args(attachments: list[FontAttachment], first_index: int = 0) -> list[str]:
    """ffmpeg output options attaching each font. *first_index* is the number of attachment streams the output already has."""
    args: list[str] = []
    for i, attachment in enumerate(attachments, start=first_index):
        args += ["-attach", attachment.path]
        args += [f"-metadata:s:t:{i}", f"mimetype={attachment.mime}"]
        args += [f"-metadata:s:t:{i}", f"filename={attachment.name}"]
    return args


def mkvmerge_attach_args(attachments: list[FontAttachment]) -> list[str]:
    args: list[str] = []
    for attachment in attachments:
        args += ["--attachment-name", attachment.name, "--attachment-mime-type", attachment.mime, "--attach-file", attachment.path]
    return args


def mkv_subtitle_codec_args(subtitle_paths: list) -> list[str]:
    """Per-stream `-c:s` options for an mkv built through ffmpeg: ASS/SSA stay ASS (styles and fonts survive), everything else becomes SRT."""
    args: list[str] = []
    for i, path in enumerate(subtitle_paths):
        args += [f"-c:s:{i}", "ass" if _is_ass(str(path)) else "srt"]
    return args
