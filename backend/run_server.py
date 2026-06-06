"""
Frozen-app entry point for the CutScript FastAPI backend.

In development the backend is started with ``python -m uvicorn main:app``.
Once bundled with PyInstaller there is no interpreter on the user's machine,
so we start uvicorn programmatically against the imported FastAPI ``app``.

Host/port are provided by the Electron launcher via CLI args (falling back to
environment variables, then sane defaults).
"""

import argparse
import os
import sys

# torchmetrics (pulled in via whisperx -> pyannote -> pytorch_lightning) imports
# matplotlib at import time. Force the headless Agg backend so it never tries to
# load a GUI backend (tkinter/Qt) that isn't bundled.
os.environ.setdefault("MPLBACKEND", "Agg")

# When frozen, ffmpeg.exe ships next to the executable. whisperx/moviepy shell
# out to "ffmpeg", so make the bundled copy discoverable regardless of how the
# process was launched (don't rely on the parent passing PATH).
if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(sys.executable)
    os.environ["PATH"] = _exe_dir + os.pathsep + os.environ.get("PATH", "")

    # Make torch's bundled CUDA libraries (cuBLAS / cuDNN 9) discoverable so
    # CTranslate2 (>=4.5) can load cuDNN on Windows GPU builds. torch normally
    # registers this on import, but add it explicitly to be safe.
    if sys.platform == "win32":
        _torch_lib = os.path.join(_exe_dir, "_internal", "torch", "lib")
        if os.path.isdir(_torch_lib):
            try:
                os.add_dll_directory(_torch_lib)
            except Exception:
                pass
            os.environ["PATH"] = _torch_lib + os.pathsep + os.environ.get("PATH", "")


def _ensure_importable() -> None:
    """Make the backend package modules importable regardless of CWD.

    When frozen, PyInstaller handles imports itself, but adding the executable
    directory keeps `python run_server.py` working during local testing too.
    """
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)


def main() -> None:
    _ensure_importable()

    parser = argparse.ArgumentParser(description="CutScript backend server")
    parser.add_argument(
        "--host",
        default=os.environ.get("CUTSCRIPT_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CUTSCRIPT_PORT", "8642")),
    )
    args = parser.parse_args()

    import uvicorn
    from main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
