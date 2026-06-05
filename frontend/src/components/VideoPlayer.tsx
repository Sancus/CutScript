import { useRef, useCallback, useState, useEffect } from 'react';
import { useEditorStore } from '../store/editorStore';
import { useVideoSync } from '../hooks/useVideoSync';
import { Play, Pause, SkipBack, SkipForward, Volume2, Volume1, VolumeX } from 'lucide-react';

export default function VideoPlayer() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const videoUrl = useEditorStore((s) => s.videoUrl);
  const isPlaying = useEditorStore((s) => s.isPlaying);
  const duration = useEditorStore((s) => s.duration);
  const { seekTo, togglePlay } = useVideoSync(videoRef);

  const [displayTime, setDisplayTime] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let raf = 0;
    const tick = () => {
      setDisplayTime(video.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [videoUrl]);

  // Apply volume/mute to the media element (also when a new video loads).
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = volume;
    video.muted = muted;
  }, [volume, muted, videoUrl]);

  const toggleMute = useCallback(() => setMuted((m) => !m), []);
  const handleVolume = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseFloat(e.target.value);
    setVolume(v);
    setMuted(v === 0);
  }, []);

  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

  const handleProgressClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const ratio = (e.clientX - rect.left) / rect.width;
      seekTo(ratio * duration);
    },
    [seekTo, duration],
  );

  const skip = useCallback(
    (delta: number) => {
      const video = videoRef.current;
      if (!video) return;
      seekTo(Math.max(0, Math.min(duration, video.currentTime + delta)));
    },
    [seekTo, duration],
  );

  if (!videoUrl) {
    return (
      <div className="w-full h-full flex items-center justify-center text-editor-text-muted text-sm">
        No video loaded
      </div>
    );
  }

  return (
    <div className="w-full h-full flex flex-col">
      <div className="flex-1 flex items-center justify-center bg-black rounded-lg overflow-hidden min-h-0">
        <video
          ref={videoRef}
          src={videoUrl}
          className="max-w-full max-h-full object-contain"
          playsInline
          onClick={togglePlay}
        />
      </div>

      <div className="pt-2 space-y-1.5 shrink-0">
        <div
          className="h-1.5 bg-editor-border rounded-full cursor-pointer group"
          onClick={handleProgressClick}
        >
          <div
            className="h-full bg-editor-accent rounded-full relative transition-all group-hover:h-2"
            style={{ width: duration > 0 ? `${(displayTime / duration) * 100}%` : '0%' }}
          >
            <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full opacity-0 group-hover:opacity-100 transition-opacity" />
          </div>
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1">
            <ControlButton onClick={() => skip(-5)} title="Back 5s">
              <SkipBack className="w-4 h-4" />
            </ControlButton>
            <ControlButton onClick={togglePlay} title={isPlaying ? 'Pause' : 'Play'} primary>
              {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5 ml-0.5" />}
            </ControlButton>
            <ControlButton onClick={() => skip(5)} title="Forward 5s">
              <SkipForward className="w-4 h-4" />
            </ControlButton>
          </div>

          <div className="flex items-center gap-2 text-xs text-editor-text-muted">
            <button
              onClick={toggleMute}
              title={muted ? 'Unmute' : 'Mute'}
              className="hover:text-editor-text transition-colors"
            >
              {muted || volume === 0 ? (
                <VolumeX className="w-4 h-4" />
              ) : volume < 0.5 ? (
                <Volume1 className="w-4 h-4" />
              ) : (
                <Volume2 className="w-4 h-4" />
              )}
            </button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={muted ? 0 : volume}
              onChange={handleVolume}
              title="Volume"
              className="w-20 cursor-pointer"
              style={{ accentColor: '#6366f1' }}
            />
            <span className="font-mono ml-1">
              {formatTime(displayTime)} / {formatTime(duration)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function ControlButton({
  children,
  onClick,
  title,
  primary,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  primary?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`p-1.5 rounded-md transition-colors ${
        primary
          ? 'bg-editor-accent/20 text-editor-accent hover:bg-editor-accent/30'
          : 'text-editor-text-muted hover:text-editor-text hover:bg-editor-surface'
      }`}
    >
      {children}
    </button>
  );
}
