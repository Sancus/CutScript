"""
FFmpeg-based video cutting engine.
Uses stream copy for fast, lossless cuts and falls back to re-encode when needed.
"""

import hashlib
import json
import logging
import subprocess
import tempfile
import threading
import os
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def _find_ffmpeg() -> str:
    """Locate ffmpeg binary."""
    for cmd in ["ffmpeg", "ffmpeg.exe"]:
        try:
            subprocess.run([cmd, "-version"], capture_output=True, check=True)
            return cmd
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("FFmpeg not found. Install it or add it to PATH.")


# Containers Chromium's <video> can generally play directly.
_PLAYABLE_SUFFIXES = {".mp4", ".m4v", ".webm"}
_preview_cache: dict = {}
_audio_cache: dict = {}
_preview_lock = threading.Lock()
_audio_lock = threading.Lock()


def _probe_codecs(ffmpeg: str, src: str):
    ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
    vcodec = acodec = ""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", src],
            capture_output=True, text=True, check=True,
        ).stdout
        for s in json.loads(out).get("streams", []):
            if s.get("codec_type") == "video" and not vcodec:
                vcodec = s.get("codec_name", "")
            elif s.get("codec_type") == "audio" and not acodec:
                acodec = s.get("codec_name", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"ffprobe failed for {src}: {exc}")
    return vcodec, acodec


def get_playable_media(src_path: str) -> str:
    """Return a path to a browser-playable (MP4/H.264/AAC) version of `src_path`.

    Chromium's <video> can't render many containers (e.g. Matroska .mkv). We
    remux instantly when the video is already H.264, otherwise transcode, and
    cache the result so it's produced at most once per source.
    """
    src = Path(src_path)
    if src.suffix.lower() in _PLAYABLE_SUFFIXES:
        return str(src)

    key = str(src.resolve())
    cached = _preview_cache.get(key)
    if cached and os.path.exists(cached):
        return cached

    # Serialize generation so concurrent requests (the <video> element issues
    # several range requests at once) don't run ffmpeg into the same temp file.
    with _preview_lock:
        cached = _preview_cache.get(key)
        if cached and os.path.exists(cached):
            return cached

        ffmpeg = _find_ffmpeg()
        vcodec, _acodec = _probe_codecs(ffmpeg, str(src))

        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        out = os.path.join(tempfile.gettempdir(), f"cutscript_preview_{digest}.mp4")

        # Copy H.264 video (instant); re-encode if it's anything else.
        vargs = ["-c:v", "copy"] if vcodec == "h264" else \
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        # Always re-encode audio to AAC. Copying AAC out of Matroska/TS into MP4
        # frequently produces silent audio in the browser (it needs the
        # aac_adtstoasc bitstream filter); a clean re-encode sidesteps that and
        # handles non-AAC sources uniformly. It's cheap relative to the video.
        aargs = ["-c:a", "aac", "-b:a", "192k"]

        base = [ffmpeg, "-y", "-i", str(src), "-map", "0:v:0?", "-map", "0:a:0?"]
        cmd = [*base, *vargs, *aargs, "-movflags", "+faststart", out]
        logger.info(f"Generating browser-playable preview -> {out}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Stream-copy can fail on some H.264-in-Matroska variants; transcode.
            logger.warning(f"Preview remux failed, transcoding instead: {result.stderr[-300:]}")
            cmd = [
                *base,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Preview generation failed: {result.stderr[-300:]}")

        _preview_cache[key] = out
        return out


def get_audio_track(src_path: str) -> str:
    """Extract a clean mono 16 kHz WAV for client-side waveform rendering.

    The browser's decodeAudioData is unreliable on full video containers, so the
    waveform should decode a plain PCM WAV instead of the video file. Cached.
    """
    src = Path(src_path)
    key = str(src.resolve())
    cached = _audio_cache.get(key)
    if cached and os.path.exists(cached):
        return cached

    with _audio_lock:
        cached = _audio_cache.get(key)
        if cached and os.path.exists(cached):
            return cached

        ffmpeg = _find_ffmpeg()
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        out = os.path.join(tempfile.gettempdir(), f"cutscript_audio_{digest}.wav")
        cmd = [
            ffmpeg, "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Audio extraction failed: {result.stderr[-300:]}")

        _audio_cache[key] = out
        return out


def export_stream_copy(
    input_path: str,
    output_path: str,
    keep_segments: List[dict],
) -> str:
    """
    Export video using FFmpeg concat demuxer with stream copy.
    ~100x faster than re-encoding. No quality loss.

    Args:
        input_path: source video file
        output_path: destination file
        keep_segments: list of {"start": float, "end": float} to keep

    Returns:
        output_path on success
    """
    ffmpeg = _find_ffmpeg()
    input_path = str(Path(input_path).resolve())
    output_path = str(Path(output_path).resolve())

    if not keep_segments:
        raise ValueError("No segments to export")

    temp_dir = tempfile.mkdtemp(prefix="aive_export_")

    try:
        segment_files = []
        for i, seg in enumerate(keep_segments):
            seg_file = os.path.join(temp_dir, f"seg_{i:04d}.ts")
            cmd = [
                ffmpeg, "-y",
                "-ss", str(seg["start"]),
                "-to", str(seg["end"]),
                "-i", input_path,
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                "-f", "mpegts",
                seg_file,
            ]
            logger.info(f"Extracting segment {i}: {seg['start']:.2f}s - {seg['end']:.2f}s")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning(f"Stream copy segment {i} failed, will try re-encode: {result.stderr[-200:]}")
                return export_reencode(input_path, output_path, keep_segments)
            segment_files.append(seg_file)

        concat_str = "|".join(segment_files)
        cmd = [
            ffmpeg, "-y",
            "-i", f"concat:{concat_str}",
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        logger.info(f"Concatenating {len(segment_files)} segments -> {output_path}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning(f"Concat failed, falling back to re-encode: {result.stderr[-200:]}")
            return export_reencode(input_path, output_path, keep_segments)

        return output_path

    finally:
        for f in os.listdir(temp_dir):
            try:
                os.remove(os.path.join(temp_dir, f))
            except OSError:
                pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass


def export_reencode(
    input_path: str,
    output_path: str,
    keep_segments: List[dict],
    resolution: str = "1080p",
    format_hint: str = "mp4",
) -> str:
    """
    Export video with full re-encode. Slower but supports resolution changes,
    format conversion, and avoids stream-copy edge cases.
    """
    ffmpeg = _find_ffmpeg()
    input_path = str(Path(input_path).resolve())
    output_path = str(Path(output_path).resolve())

    if not keep_segments:
        raise ValueError("No segments to export")

    scale_map = {
        "720p": "scale=-2:720",
        "1080p": "scale=-2:1080",
        "4k": "scale=-2:2160",
    }

    filter_parts = []
    for i, seg in enumerate(keep_segments):
        filter_parts.append(
            f"[0:v]trim=start={seg['start']}:end={seg['end']},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={seg['start']}:end={seg['end']},asetpts=PTS-STARTPTS[a{i}];"
        )

    n = len(keep_segments)
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]")

    filter_complex = "".join(filter_parts)

    scale = scale_map.get(resolution, "")
    if scale:
        filter_complex += f";[outv]{scale}[outv_scaled]"
        video_map = "[outv_scaled]"
    else:
        video_map = "[outv]"

    codec_args = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
    if format_hint == "webm":
        codec_args = ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus"]

    cmd = [
        ffmpeg, "-y",
        "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", video_map,
        "-map", "[outa]",
        *codec_args,
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(f"Re-encoding {n} segments -> {output_path} ({resolution})")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg re-encode failed: {result.stderr[-500:]}")

    return output_path


def export_reencode_with_subs(
    input_path: str,
    output_path: str,
    keep_segments: List[dict],
    subtitle_path: str,
    resolution: str = "1080p",
    format_hint: str = "mp4",
) -> str:
    """
    Export video with re-encode and burn-in subtitles (ASS format).
    Applies trim+concat first, then overlays the subtitle file.
    """
    ffmpeg = _find_ffmpeg()
    input_path = str(Path(input_path).resolve())
    output_path = str(Path(output_path).resolve())
    subtitle_path = str(Path(subtitle_path).resolve())

    if not keep_segments:
        raise ValueError("No segments to export")

    scale_map = {
        "720p": "scale=-2:720",
        "1080p": "scale=-2:1080",
        "4k": "scale=-2:2160",
    }

    filter_parts = []
    for i, seg in enumerate(keep_segments):
        filter_parts.append(
            f"[0:v]trim=start={seg['start']}:end={seg['end']},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={seg['start']}:end={seg['end']},asetpts=PTS-STARTPTS[a{i}];"
        )

    n = len(keep_segments)
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]")

    filter_complex = "".join(filter_parts)

    # Escape path for FFmpeg subtitle filter (Windows backslashes need escaping)
    escaped_sub = subtitle_path.replace("\\", "/").replace(":", "\\:")

    scale = scale_map.get(resolution, "")
    if scale:
        filter_complex += f";[outv]{scale},ass='{escaped_sub}'[outv_final]"
    else:
        filter_complex += f";[outv]ass='{escaped_sub}'[outv_final]"
    video_map = "[outv_final]"

    codec_args = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
    if format_hint == "webm":
        codec_args = ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus"]

    cmd = [
        ffmpeg, "-y",
        "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", video_map,
        "-map", "[outa]",
        *codec_args,
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(f"Re-encoding {n} segments with subtitles -> {output_path} ({resolution})")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg re-encode with subs failed: {result.stderr[-500:]}")

    return output_path


def get_video_info(input_path: str) -> dict:
    """Get basic video metadata using ffprobe."""
    ffmpeg = _find_ffmpeg()
    ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")

    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(input_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        import json
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})

        return {
            "duration": float(fmt.get("duration", 0)),
            "size": int(fmt.get("size", 0)),
            "format": fmt.get("format_name", ""),
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "codec": video_stream.get("codec_name", ""),
            "fps": eval(video_stream.get("r_frame_rate", "0/1")) if "/" in video_stream.get("r_frame_rate", "") else 0,
        }
    except Exception as e:
        logger.error(f"Failed to get video info: {e}")
        return {}
