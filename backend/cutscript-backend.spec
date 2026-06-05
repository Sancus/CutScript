# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the CutScript FastAPI backend.

Produces a self-contained Windows folder (onedir):
    dist/cutscript-backend/cutscript-backend.exe  (+ _internal/)

The ML stack (torch, whisperx, faster-whisper, pyannote, deepfilternet, ...)
ships data files and performs a lot of dynamic importing, so we lean on
`collect_all` for those packages and `copy_metadata` for the ones that read
their installed version at runtime via importlib.metadata.
"""

from PyInstaller.utils.hooks import collect_all, copy_metadata, collect_submodules

datas = []
binaries = []
hiddenimports = []


def add_all(pkg):
    """collect_all for a package, tolerating absence (optional/extra deps)."""
    global datas, binaries, hiddenimports
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
        print(f"[spec] collected {pkg} ({len(d)} datas, {len(b)} binaries)")
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] skip collect_all({pkg}): {exc}")


def add_meta(pkg):
    """copy_metadata for a package, tolerating absence."""
    global datas
    try:
        datas += copy_metadata(pkg)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] no metadata for {pkg}: {exc}")


def add_submodules(pkg):
    global hiddenimports
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] skip collect_submodules({pkg}): {exc}")


# Heavy packages that ship data files / dynamic imports.
COLLECT_PACKAGES = [
    "torch",
    "torchaudio",
    "whisperx",
    "faster_whisper",
    "ctranslate2",
    "pyannote",
    "pyannote.audio",
    "pyannote.core",
    "pyannote.database",
    "pyannote.metrics",
    "pyannote.pipeline",
    "asteroid_filterbanks",
    "speechbrain",
    "lightning",
    "lightning_fabric",
    "pytorch_lightning",
    "torchmetrics",
    "transformers",
    "tokenizers",
    "huggingface_hub",
    "sentencepiece",
    "librosa",
    "soundfile",
    "soxr",
    "audioread",
    "pooch",
    "numba",
    "llvmlite",
    "scipy",
    "sklearn",
    "pandas",
    "df",
    "av",
    "moviepy",
    "imageio",
    "imageio_ffmpeg",
    "nltk",
    "julius",
    "omegaconf",
    "antlr4",
    "einops",
    "loguru",
    "primePy",
]
for _pkg in COLLECT_PACKAGES:
    add_all(_pkg)

# Packages that introspect their installed distribution metadata at runtime.
META_PACKAGES = [
    "torch",
    "torchaudio",
    "tqdm",
    "regex",
    "requests",
    "packaging",
    "filelock",
    "numpy",
    "tokenizers",
    "transformers",
    "huggingface_hub",
    "safetensors",
    "pyyaml",
    "faster_whisper",
    "ctranslate2",
    "whisperx",
    "speechbrain",
    "pyannote.audio",
    "pytorch_lightning",
    "lightning",
    "lightning_fabric",
    "torchmetrics",
    "scipy",
    "scikit-learn",
    "numba",
    "librosa",
    "soundfile",
    "rich",
    "openai",
    "anthropic",
    "fastapi",
    "uvicorn",
    "starlette",
    "pydantic",
    "pydantic_core",
    "deepfilternet",
]
for _pkg in META_PACKAGES:
    add_meta(_pkg)

# Bundle metadata for EVERY installed distribution. Many packages in this stack
# (imageio, transformers, speechbrain, pyannote, lightning, ...) call
# importlib.metadata.version(...) or pkg_resources at import time, which raises
# PackageNotFoundError unless the .dist-info is bundled. Copying all of it is
# cheap (metadata is tiny) and avoids fixing these one crash at a time.
import importlib.metadata as _ilmd

_seen_meta = set()
for _dist in _ilmd.distributions():
    try:
        _name = _dist.metadata["Name"]
    except Exception:  # noqa: BLE001
        _name = None
    if not _name or _name in _seen_meta:
        continue
    _seen_meta.add(_name)
    try:
        datas += copy_metadata(_name)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] no metadata for {_name}: {exc}")
print(f"[spec] bundled metadata for {len(_seen_meta)} distributions")

# Uvicorn picks its protocol/loop implementations dynamically at runtime.
add_submodules("uvicorn")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
]

# faster-whisper and ctranslate2 import pkg_resources at runtime.
add_submodules("pkg_resources")
add_meta("setuptools")
hiddenimports += ["pkg_resources"]

# The backend's own first-party modules.
hiddenimports += [
    "main",
    "routers",
    "routers.transcribe",
    "routers.export",
    "routers.ai",
    "routers.captions",
    "routers.audio",
    "services",
    "services.transcription",
    "services.diarization",
    "services.ai_provider",
    "services.audio_cleaner",
    "services.caption_generator",
    "services.video_editor",
    "services.background_removal",
    "utils",
    "utils.gpu_utils",
    "utils.audio_processing",
    "utils.cache",
]


a = Analysis(
    ["run_server.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "IPython",
        "notebook",
        "jupyter",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cutscript-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="cutscript-backend",
)
