import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


async def get_video_metadata(video_path: Path) -> Dict[str, Any]:
    """
    Extract width, height, duration using ffprobe.
    Returns a dict with 'width', 'height', 'duration'.
    """
    metadata = {"width": 0, "height": 0, "duration": 0}
    if not video_path.exists():
        return metadata

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=width,height,duration:format=duration",
        "-of", "json",
        str(video_path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning(f"ffprobe failed for {video_path}: {stderr.decode(errors='ignore')}")
            return metadata

        info = json.loads(stdout.decode(errors="ignore"))
        streams = info.get("streams", [])
        video_stream = next((s for s in streams if "width" in s and "height" in s), None)

        if video_stream:
            metadata["width"] = int(video_stream.get("width", 0))
            metadata["height"] = int(video_stream.get("height", 0))
            if "duration" in video_stream:
                try:
                    metadata["duration"] = int(float(video_stream["duration"]))
                except (ValueError, TypeError):
                    pass

        if not metadata["duration"] and "format" in info and "duration" in info["format"]:
            try:
                metadata["duration"] = int(float(info["format"]["duration"]))
            except (ValueError, TypeError):
                pass

    except Exception as e:
        logger.error(f"Error extracting metadata from {video_path}: {e}")

    return metadata


async def generate_thumbnail(video_path: Path, thumb_path: Path, timestamp: float = 1.0) -> Optional[Path]:
    """
    Generate a JPEG thumbnail from a video at the specified timestamp.
    Scaled down to a max width of 320 for Telegram preview specifications.
    """
    if not video_path.exists():
        return None

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", "scale='min(320,iw)':-1",
        "-q:v", "3",
        str(thumb_path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning(f"ffmpeg thumbnail generation failed: {stderr.decode(errors='ignore')}")
            return None

        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return thumb_path
    except Exception as e:
        logger.error(f"Error generating thumbnail for {video_path}: {e}")

    return None
