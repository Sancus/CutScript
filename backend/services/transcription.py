"""
WhisperX-based transcription service with word-level alignment.
Falls back to standard Whisper if WhisperX is not available.
"""

import logging
from pathlib import Path
from typing import Optional

import torch

from utils.gpu_utils import get_optimal_device, configure_gpu
from utils.audio_processing import extract_audio
from utils.cache import load_from_cache, save_to_cache

logger = logging.getLogger(__name__)

_model_cache: dict = {}

try:
    import whisperx
    WHISPERX_AVAILABLE = True
except ImportError:
    WHISPERX_AVAILABLE = False
    import whisper


def _patch_speechbrain_lazy_imports():
    """Make speechbrain's lazy modules safe for hasattr()/inspect on Windows.

    speechbrain registers optional integrations (e.g. k2) as LazyModules. Their
    ensure_module() calls inspect.getframeinfo(), which re-enters
    inspect.getmodule() -> hasattr(lazyModule, '__file__') -> __getattr__ ->
    ensure_module() ... infinitely. speechbrain guards against this with
    `filename.endswith("/inspect.py")`, but that forward-slash check never
    matches on Windows, so pytorch_lightning's inspect.stack() during model
    loading hits a RecursionError (and, if it imports, an ImportError for the
    missing optional dep).

    Fix with a thread-local re-entrancy guard: if __getattr__ is re-entered
    while already resolving, raise AttributeError immediately to break the
    cycle, and convert a failed optional import to AttributeError so hasattr()
    simply skips it. Path-separator independent, so correct on all platforms.
    """
    try:
        import threading
        from speechbrain.utils import importutils as _sb
        lazy_module = _sb.LazyModule
        if getattr(lazy_module, "_cutscript_patched", False):
            return
        _orig_getattr = lazy_module.__getattr__
        _guard = threading.local()

        def _safe_getattr(self, attr):
            if getattr(_guard, "busy", False):
                raise AttributeError(attr)
            _guard.busy = True
            try:
                return _orig_getattr(self, attr)
            except ImportError as exc:
                raise AttributeError(attr) from exc
            finally:
                _guard.busy = False

        lazy_module.__getattr__ = _safe_getattr
        lazy_module._cutscript_patched = True
    except Exception:
        pass


_patch_speechbrain_lazy_imports()

try:
    HF_TOKEN = None
    import os
    HF_TOKEN = os.environ.get("HF_TOKEN")
except Exception:
    pass


def _get_device(use_gpu: bool = True) -> torch.device:
    if use_gpu:
        return get_optimal_device()
    return torch.device("cpu")


def _load_model(model_name: str, device: torch.device):
    cache_key = f"{model_name}_{device}"
    if cache_key in _model_cache:
        return _model_cache[cache_key]

    logger.info(f"Loading model: {model_name} on {device}")
    if WHISPERX_AVAILABLE:
        compute_type = "float16" if device.type == "cuda" else "int8"
        model = whisperx.load_model(
            model_name,
            device=str(device),
            compute_type=compute_type,
        )
    else:
        model = whisper.load_model(model_name, device=device)

    _model_cache[cache_key] = model
    return model


def transcribe_audio(
    file_path: str,
    model_name: str = "base",
    use_gpu: bool = True,
    use_cache: bool = True,
    language: Optional[str] = None,
) -> dict:
    """
    Transcribe audio/video file and return word-level timestamps.

    Returns:
        dict with keys: words, segments, language
    """
    file_path = Path(file_path)

    if use_cache:
        cached = load_from_cache(file_path, model_name, "transcribe_wx")
        if cached:
            logger.info("Using cached transcription")
            return cached

    video_extensions = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    if file_path.suffix.lower() in video_extensions:
        audio_path = extract_audio(file_path)
    else:
        audio_path = file_path

    device = _get_device(use_gpu)
    model = _load_model(model_name, device)

    logger.info(f"Transcribing: {file_path}")

    if WHISPERX_AVAILABLE:
        result = _transcribe_whisperx(model, str(audio_path), device, language)
    else:
        result = _transcribe_standard(model, str(audio_path), language)

    if use_cache:
        save_to_cache(file_path, result, model_name, "transcribe_wx")

    return result


def _transcribe_whisperx(model, audio_path: str, device: torch.device, language: Optional[str]) -> dict:
    audio = whisperx.load_audio(audio_path)
    transcribe_opts = {}
    if language:
        transcribe_opts["language"] = language

    result = model.transcribe(audio, batch_size=16, **transcribe_opts)
    detected_language = result.get("language", "en")

    align_model, align_metadata = whisperx.load_align_model(
        language_code=detected_language,
        device=str(device),
    )
    aligned = whisperx.align(
        result["segments"],
        align_model,
        align_metadata,
        audio,
        str(device),
        return_char_alignments=False,
    )

    words = []
    for seg in aligned.get("segments", []):
        for w in seg.get("words", []):
            words.append({
                "word": w.get("word", ""),
                "start": round(w.get("start", 0), 3),
                "end": round(w.get("end", 0), 3),
                "confidence": round(w.get("score", 0), 3),
            })

    segments = []
    for i, seg in enumerate(aligned.get("segments", [])):
        seg_words = []
        for w in seg.get("words", []):
            seg_words.append({
                "word": w.get("word", ""),
                "start": round(w.get("start", 0), 3),
                "end": round(w.get("end", 0), 3),
                "confidence": round(w.get("score", 0), 3),
            })
        segments.append({
            "id": i,
            "start": round(seg.get("start", 0), 3),
            "end": round(seg.get("end", 0), 3),
            "text": seg.get("text", "").strip(),
            "words": seg_words,
        })

    return {
        "words": words,
        "segments": segments,
        "language": detected_language,
    }


def _transcribe_standard(model, audio_path: str, language: Optional[str]) -> dict:
    """Fallback: standard Whisper (segment-level only, synthesized word timestamps)."""
    opts = {}
    if language:
        opts["language"] = language

    result = model.transcribe(audio_path, **opts)
    detected_language = result.get("language", "en")

    words = []
    segments = []

    for i, seg in enumerate(result.get("segments", [])):
        text = seg.get("text", "").strip()
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)
        seg_words_text = text.split()
        duration = seg_end - seg_start

        seg_words = []
        for j, w_text in enumerate(seg_words_text):
            w_start = seg_start + (j / max(len(seg_words_text), 1)) * duration
            w_end = seg_start + ((j + 1) / max(len(seg_words_text), 1)) * duration
            word_obj = {
                "word": w_text,
                "start": round(w_start, 3),
                "end": round(w_end, 3),
                "confidence": 0.5,
            }
            words.append(word_obj)
            seg_words.append(word_obj)

        segments.append({
            "id": i,
            "start": round(seg_start, 3),
            "end": round(seg_end, 3),
            "text": text,
            "words": seg_words,
        })

    return {
        "words": words,
        "segments": segments,
        "language": detected_language,
    }
