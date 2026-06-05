import logging
import os
import stat
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from routers import transcribe, export, ai, captions, audio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Tracks whether the heavy transcription stack actually imports. /health only
# reports that uvicorn is alive; /ready reports whether the engine is usable.
_engine_status = {"state": "starting", "detail": None}


def _warmup_engine() -> None:
    """Import the ML stack in the background so /ready reflects real usability.

    Importing the third-party packages directly (before the service module's
    whisperx->whisper fallback) means a missing dependency surfaces as its true
    error instead of being masked.
    """
    global _engine_status
    try:
        import torch  # noqa: F401
        import whisperx  # noqa: F401  -- surfaces real import errors (e.g. matplotlib)
        import pyannote.audio  # noqa: F401
        import services.transcription  # noqa: F401
        import services.diarization  # noqa: F401
        _engine_status = {"state": "ready", "detail": None}
        logger.info("Transcription engine warmed up and ready")
    except Exception as exc:  # noqa: BLE001
        _engine_status = {"state": "error", "detail": f"{type(exc).__name__}: {exc}"}
        logger.error("Transcription engine failed to load", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI Video Editor backend starting up")
    # Warm up off the event loop so startup/health stay instant.
    threading.Thread(target=_warmup_engine, name="engine-warmup", daemon=True).start()
    yield
    logger.info("AI Video Editor backend shutting down")


app = FastAPI(
    title="AI Video Editor Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

app.include_router(transcribe.router)
app.include_router(export.router)
app.include_router(ai.router)
app.include_router(captions.router)
app.include_router(audio.router)


MIME_MAP = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
}


@app.get("/file")
async def serve_local_file(request: Request, path: str = Query(...)):
    """Stream a local file with HTTP Range support (required for video seeking)."""
    file_path = Path(path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    file_size = file_path.stat().st_size
    content_type = MIME_MAP.get(file_path.suffix.lower(), "application/octet-stream")

    range_header = request.headers.get("range")
    if range_header:
        range_spec = range_header.replace("bytes=", "")
        range_start_str, range_end_str = range_spec.split("-")
        range_start = int(range_start_str) if range_start_str else 0
        range_end = int(range_end_str) if range_end_str else file_size - 1
        range_end = min(range_end, file_size - 1)
        content_length = range_end - range_start + 1

        def iter_range():
            with open(file_path, "rb") as f:
                f.seek(range_start)
                remaining = content_length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            iter_range(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {range_start}-{range_end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
            },
        )

    def iter_file():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Report whether the transcription engine actually imported.

    state is one of: "starting" (still importing), "ready" (usable),
    or "error" (import failed; `detail` carries the reason).
    """
    return _engine_status
