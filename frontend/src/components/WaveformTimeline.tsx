import { useRef, useEffect, useCallback, useState } from 'react';
import { useEditorStore } from '../store/editorStore';
import { ZoomIn, ZoomOut, AlertTriangle, Scissors } from 'lucide-react';

export default function WaveformTimeline() {
  const waveCanvasRef = useRef<HTMLCanvasElement>(null);
  const headCanvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(null);
  const [mediaDuration, setMediaDuration] = useState(0);
  const [threshold, setThreshold] = useState(0.5);

  const videoUrl = useEditorStore((s) => s.videoUrl);
  const videoPath = useEditorStore((s) => s.videoPath);
  const backendUrl = useEditorStore((s) => s.backendUrl);
  const duration = useEditorStore((s) => s.duration);
  const deletedRanges = useEditorStore((s) => s.deletedRanges);
  const setCurrentTime = useEditorStore((s) => s.setCurrentTime);
  const removeSilences = useEditorStore((s) => s.removeSilences);
  const clearSilences = useEditorStore((s) => s.clearSilences);
  const restoreRange = useEditorStore((s) => s.restoreRange);
  const addTimeCut = useEditorStore((s) => s.addTimeCut);

  const silenceCount = deletedRanges.filter((r) => r.wordIndices.length === 0).length;

  const audioContextRef = useRef<AudioContext | null>(null);
  const audioBufferRef = useRef<AudioBuffer | null>(null);
  const zoomRef = useRef(1);
  const rafRef = useRef(0);
  // Drag state: distinguishes a click (seek/restore) from a drag (select).
  const dragRef = useRef<{ startX: number; startTime: number; dragging: boolean } | null>(null);

  const getDur = useCallback(
    () => audioBufferRef.current?.duration || mediaDuration || duration || 0,
    [mediaDuration, duration],
  );

  useEffect(() => {
    if (!videoUrl || !videoPath) return;
    setAudioError(null);

    const loadAudio = async () => {
      try {
        const ctx = new AudioContext();
        audioContextRef.current = ctx;

        // Decode a dedicated extracted WAV, not the video container (which the
        // browser's decodeAudioData can't reliably handle).
        const audioUrl = `${backendUrl}/audio?path=${encodeURIComponent(videoPath)}`;
        const response = await fetch(audioUrl);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const arrayBuffer = await response.arrayBuffer();
        const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
        audioBufferRef.current = audioBuffer;
        setMediaDuration(audioBuffer.duration);
        drawStaticWaveform();
      } catch (err) {
        console.warn('Could not decode audio for waveform:', err);
        setAudioError('Waveform unavailable — audio could not be decoded');
      }
    };

    loadAudio();

    return () => {
      audioContextRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoUrl, videoPath]);

  const drawStaticWaveform = useCallback(() => {
    const canvas = waveCanvasRef.current;
    const buffer = audioBufferRef.current;
    if (!canvas || !buffer) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    const channelData = buffer.getChannelData(0);
    const samplesPerPixel = Math.floor(channelData.length / width);
    const dur = buffer.duration || 1;

    ctx.clearRect(0, 0, width, height);

    // Cut regions (word deletions AND silences) drawn in red.
    for (const range of deletedRanges) {
      const x1 = (range.start / dur) * width;
      const x2 = (range.end / dur) * width;
      ctx.fillStyle = 'rgba(239, 68, 68, 0.18)';
      ctx.fillRect(x1, 0, Math.max(1, x2 - x1), height);
    }

    const mid = height / 2;
    ctx.beginPath();
    ctx.strokeStyle = '#4a4d5e';
    ctx.lineWidth = 1;

    for (let x = 0; x < width; x++) {
      const start = x * samplesPerPixel;
      const end = Math.min(start + samplesPerPixel, channelData.length);

      let min = 0;
      let max = 0;
      for (let i = start; i < end; i++) {
        if (channelData[i] < min) min = channelData[i];
        if (channelData[i] > max) max = channelData[i];
      }

      const yMin = mid + min * mid * 0.9;
      const yMax = mid + max * mid * 0.9;
      ctx.moveTo(x, yMin);
      ctx.lineTo(x, yMax);
    }
    ctx.stroke();
  }, [deletedRanges]);

  useEffect(() => {
    drawStaticWaveform();
  }, [drawStaticWaveform]);

  // RAF loop for the playhead (reads video.currentTime directly).
  useEffect(() => {
    const headCanvas = headCanvasRef.current;
    const waveCanvas = waveCanvasRef.current;
    if (!headCanvas || !waveCanvas) return;

    const tick = () => {
      const ctx = headCanvas.getContext('2d');
      if (!ctx) { rafRef.current = requestAnimationFrame(tick); return; }

      const video = document.querySelector('video') as HTMLVideoElement | null;
      const dur = audioBufferRef.current?.duration || mediaDuration || duration || 0;

      const dpr = window.devicePixelRatio || 1;
      const rect = headCanvas.getBoundingClientRect();
      if (headCanvas.width !== waveCanvas.width || headCanvas.height !== waveCanvas.height) {
        headCanvas.width = rect.width * dpr;
        headCanvas.height = rect.height * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const width = rect.width;
      const height = rect.height;
      ctx.clearRect(0, 0, width, height);

      if (dur > 0 && video) {
        const px = (video.currentTime / dur) * width;
        ctx.beginPath();
        ctx.strokeStyle = '#6366f1';
        ctx.lineWidth = 2;
        ctx.moveTo(px, 0);
        ctx.lineTo(px, height);
        ctx.stroke();
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [videoUrl, mediaDuration, duration]);

  useEffect(() => {
    const observer = new ResizeObserver(() => drawStaticWaveform());
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [drawStaticWaveform]);

  const timeFromClientX = useCallback(
    (clientX: number) => {
      const canvas = headCanvasRef.current;
      if (!canvas) return 0;
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      return ratio * getDur();
    },
    [getDur],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      dragRef.current = { startX: e.clientX, startTime: timeFromClientX(e.clientX), dragging: false };
    },
    [timeFromClientX],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      if (!d.dragging && Math.abs(e.clientX - d.startX) > 4) d.dragging = true;
      if (d.dragging) {
        const t = timeFromClientX(e.clientX);
        setSelection({ start: Math.min(d.startTime, t), end: Math.max(d.startTime, t) });
      }
    },
    [timeFromClientX],
  );

  const finishDrag = useCallback(() => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d || d.dragging) return; // drag finalized via selection; nothing more to do
    // Plain click: restore a cut if one was clicked, else seek (and clear selection).
    const t = d.startTime;
    const hit = deletedRanges.find((r) => t >= r.start && t < r.end);
    if (hit) {
      restoreRange(hit.id);
      return;
    }
    setSelection(null);
    setCurrentTime(t);
    const video = document.querySelector('video') as HTMLVideoElement | null;
    if (video) video.currentTime = t;
  }, [deletedRanges, restoreRange, setCurrentTime]);

  const cutSelection = useCallback(() => {
    if (!selection) return;
    addTimeCut(selection.start, selection.end);
    setSelection(null);
  }, [selection, addTimeCut]);

  const handleRemoveSilences = useCallback(() => {
    const n = removeSilences(threshold);
    if (n === 0) {
      // Nothing matched — surface it lightly via the title attr update is enough.
    }
  }, [removeSilences, threshold]);

  const dur = getDur();
  const selLeft = selection && dur > 0 ? (selection.start / dur) * 100 : 0;
  const selWidth = selection && dur > 0 ? ((selection.end - selection.start) / dur) * 100 : 0;

  if (!videoUrl) {
    return (
      <div className="w-full h-full flex items-center justify-center text-editor-text-muted text-xs">
        Load a video to see the waveform
      </div>
    );
  }

  return (
    <div ref={containerRef} className="w-full h-full flex flex-col">
      <div className="flex items-center justify-between px-3 py-1 shrink-0 gap-2">
        <span className="text-[10px] text-editor-text-muted font-medium uppercase tracking-wider">
          Timeline
        </span>

        <div className="flex items-center gap-2">
          {/* Silence removal (manual) */}
          <label className="text-[10px] text-editor-text-muted">gap&gt;</label>
          <select
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value))}
            className="bg-editor-surface border border-editor-border rounded text-[10px] px-1 py-0.5 text-editor-text focus:outline-none"
            title="Minimum silence length to remove"
          >
            <option value={0.3}>0.3s</option>
            <option value={0.5}>0.5s</option>
            <option value={1}>1.0s</option>
            <option value={2}>2.0s</option>
          </select>
          <button
            onClick={handleRemoveSilences}
            className="flex items-center gap-1 px-2 py-0.5 text-[10px] bg-editor-accent/20 text-editor-accent rounded hover:bg-editor-accent/30 transition-colors"
            title="Cut all gaps longer than the threshold"
          >
            <Scissors className="w-3 h-3" />
            Remove silences
          </button>
          {silenceCount > 0 && (
            <button
              onClick={clearSilences}
              className="px-2 py-0.5 text-[10px] text-editor-text-muted hover:text-editor-text rounded transition-colors"
              title="Restore all auto-removed silences"
            >
              Clear ({silenceCount})
            </button>
          )}

          <div className="w-px h-4 bg-editor-border" />

          <button
            onClick={() => { zoomRef.current = Math.max(0.5, zoomRef.current - 0.5); drawStaticWaveform(); }}
            className="p-0.5 text-editor-text-muted hover:text-editor-text"
            title="Zoom out"
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => { zoomRef.current = Math.min(10, zoomRef.current + 0.5); drawStaticWaveform(); }}
            className="p-0.5 text-editor-text-muted hover:text-editor-text"
            title="Zoom in"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {audioError ? (
        <div className="flex-1 flex items-center justify-center gap-2 text-editor-text-muted text-xs">
          <AlertTriangle className="w-4 h-4 text-yellow-500" />
          <span>{audioError}</span>
        </div>
      ) : (
        <div className="flex-1 relative">
          <canvas ref={waveCanvasRef} className="absolute inset-0 w-full h-full" />
          <canvas
            ref={headCanvasRef}
            className="absolute inset-0 w-full h-full cursor-crosshair"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={finishDrag}
            onMouseLeave={finishDrag}
          />
          {/* Drag selection overlay + Cut action */}
          {selection && selWidth > 0 && (
            <div
              className="absolute top-0 bottom-0 bg-editor-accent/25 border-x border-editor-accent pointer-events-none"
              style={{ left: `${selLeft}%`, width: `${selWidth}%` }}
            >
              <button
                onClick={cutSelection}
                className="pointer-events-auto absolute -top-0.5 left-1/2 -translate-x-1/2 flex items-center gap-1 px-2 py-0.5 bg-editor-danger text-white rounded text-[10px] whitespace-nowrap shadow z-10"
              >
                <Scissors className="w-3 h-3" /> Cut {(selection.end - selection.start).toFixed(1)}s
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
