"""Transcription endpoint using WhisperX."""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class TranscribeRequest(BaseModel):
    file_path: str
    model: str = "base"
    language: Optional[str] = None
    use_gpu: bool = True
    use_cache: bool = True
    diarize: bool = False
    hf_token: Optional[str] = None
    num_speakers: Optional[int] = None


@router.post("/transcribe")
async def transcribe(req: TranscribeRequest):
    # Validate the input up front so a genuinely missing file is a clear 404,
    # rather than masking unrelated FileNotFoundErrors (e.g. a missing ffmpeg
    # executable) as if the input were missing.
    if not Path(req.file_path).is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    try:
        # Imported lazily so the backend boots fast: the heavy ML stack
        # (torch/whisperx/pyannote) only loads on the first transcription.
        from services.transcription import transcribe_audio
        from services.diarization import diarize_and_label

        result = transcribe_audio(
            file_path=req.file_path,
            model_name=req.model,
            use_gpu=req.use_gpu,
            use_cache=req.use_cache,
            language=req.language,
        )

        if req.diarize and req.hf_token:
            result = diarize_and_label(
                transcription_result=result,
                audio_path=req.file_path,
                hf_token=req.hf_token,
                num_speakers=req.num_speakers,
                use_gpu=req.use_gpu,
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
