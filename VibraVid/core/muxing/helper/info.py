# 16.04.24

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from VibraVid.core.muxing.helper.video.ts import is_mpegts_file

logger = logging.getLogger(__name__)
_PATTERNS = {
    "stream": re.compile(r"  Stream #.*"),
    "id": re.compile(r"#0:\d(\[0x\w+?\])"),
    "type": re.compile(r": (\w+): (.*)"),
    "base_info": re.compile(r"(.*?)(,|$)"),
    "replace": re.compile(r" \/ 0x\w+"),
    "res": re.compile(r"\d{2,}x\d+"),
    "bitrate": re.compile(r"\d+ kb\/s"),
    "fps": re.compile(r"(\d+(?:\.\d+)?) fps"),
    "dovi": re.compile(r"DOVI configuration record.*?profile: (\d).*?compatibility id: (\d)"),
    "start": re.compile(r"Duration.*?start: (\d+\.?\d{0,3})"),
}


@dataclass
class Mediainfo:
    text: str = ""
    id: str = ""
    type: str = ""
    resolution: str = ""
    bitrate: str = ""
    fps: str = ""
    base_info: str = ""
    hdr: bool = False
    dolby_vision: bool = False
    start_time: float = 0.0

    @staticmethod
    def _patterns():
        return _PATTERNS

    @classmethod
    async def from_file_async(cls, binary: str, file: str) -> list["Mediainfo"]:
        result = []
        if not file or not Path(file).exists():
            logger.warning(f"File not found or invalid: {file}")
            return result

        p = cls._patterns()
        logger.debug(f"Probing media file with ffprobe: {file}")
        args = [binary, "-hide_banner"]
        if is_mpegts_file(file):
            args += ["-f", "mpegts"]

        logger.info(f"Running ffprobe command: {' '.join(args + ['-i', file])}")
        args += ["-i", file]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()
        output = stderr_bytes.decode("utf-8", errors="replace")

        for stream_match in p["stream"].finditer(output):
            line = stream_match.group(0)
            type_m = p["type"].search(line)
            id_m = p["id"].search(line)

            stream_type = type_m.group(1) if type_m else ""
            stream_text = type_m.group(2).rstrip() if type_m else ""
            stream_id = id_m.group(1) if id_m else ""

            base_m = p["base_info"].match(stream_text)
            base_info = p["replace"].sub("", base_m.group(1) if base_m else "")

            res_m = p["res"].search(stream_text)
            bitrate_m = p["bitrate"].search(stream_text)
            fps_m = p["fps"].search(stream_text)
            start_m = p["start"].search(output)

            info = cls(
                text=stream_text,
                id=stream_id,
                type=stream_type,
                resolution=res_m.group(0) if res_m else "",
                bitrate=bitrate_m.group(0) if bitrate_m else "",
                fps=fps_m.group(0) if fps_m else "",
                base_info=base_info,
                hdr="/bt2020/" in stream_text,
                dolby_vision=(
                    "dvhe" in base_info
                    or "dvh1" in base_info
                    or "DOVI" in base_info
                    or "dvvideo" in base_info
                    or bool(p["dovi"].search(output) and stream_type == "Video")
                ),
                start_time=float(start_m.group(1)) if start_m else 0.0,
            )
            result.append(info)

        if not result:
            result.append(cls(type="Unknown"))
            logger.debug(f"No streams detected in file: {file}, adding Unknown stream")

        return result
