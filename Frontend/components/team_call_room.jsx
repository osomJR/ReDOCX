"use client";

import { useCallback, useMemo, useRef } from "react";
import { Maximize2, Minimize2, PhoneOff, Video } from "lucide-react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  VideoConference,
} from "@livekit/components-react";

// Conferencing-grade browser capture defaults. Keeping the microphone mono
// gives WebRTC echo cancellation and packet-loss recovery the cleanest input.
const AUDIO_CAPTURE_OPTIONS = Object.freeze({
  autoGainControl: true,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  voiceIsolation: { ideal: true },
});

// Keep this object stable. Recreating LiveKit room options can recreate the
// underlying Room and disconnect an otherwise healthy call.
const ROOM_OPTIONS = Object.freeze({
  adaptiveStream: {
    pauseVideoInBackground: false,
  },
  disconnectOnPageLeave: false,
  dynacast: true,
  publishDefaults: {
    dtx: true,
    forceStereo: false,
    red: true,
  },
});

/**
 * Persistent LiveKit call surface.
 *
 * The component must be mounted above individual pages (the team realtime
 * provider does this) so client-side navigation does not destroy the LiveKit
 * Room. Minimizing only changes presentation; it never unmounts the room.
 */
export default function TeamCallRoom({
  serverUrl,
  token,
  roomName,
  minimized = false,
  onLeave,
  onMinimize,
  onRestore,
}) {
  const leavingRef = useRef(false);

  const canConnect = Boolean(serverUrl && token);

  const displayRoomName = useMemo(() => {
    if (!roomName) return "Team call";

    return String(roomName)
      .replace(/^org-/, "Organization ")
      .replaceAll("-", " ");
  }, [roomName]);

  const leaveOnce = useCallback(async () => {
    if (leavingRef.current) return;

    leavingRef.current = true;

    try {
      await onLeave?.();
    } finally {
      leavingRef.current = false;
    }
  }, [onLeave]);

  const handleDisconnected = useCallback(() => {
    void leaveOnce();
  }, [leaveOnce]);

  if (!canConnect) {
    return (
      <section className="fixed bottom-5 right-5 z-[140] max-w-sm rounded-3xl border border-red-400/30 bg-red-950/95 p-5 text-red-100 shadow-2xl">
        <h2 className="text-lg font-semibold">Call unavailable</h2>
        <p className="mt-2 text-sm text-red-100/80">
          Missing LiveKit server URL or participant token. Start or join the
          call again to request a fresh token.
        </p>
        <button
          type="button"
          onClick={() => void leaveOnce()}
          className="mt-4 inline-flex items-center gap-2 rounded-xl border border-red-200/30 px-3 py-2 text-sm font-semibold"
        >
          <PhoneOff className="h-4 w-4" />
          Close
        </button>
      </section>
    );
  }

  return (
    <section
      className={`fixed z-[140] overflow-hidden border border-[var(--app-border)] bg-black shadow-2xl transition-[inset,width,height,border-radius] duration-200 ${
        minimized
          ? "bottom-4 right-4 h-24 w-[min(22rem,calc(100vw-2rem))] rounded-2xl"
          : "inset-2 rounded-2xl md:inset-5 md:rounded-3xl"
      }`}
      aria-label="Active video call"
    >
      {minimized ? (
        <div className="absolute inset-0 z-20 flex items-center gap-3 bg-zinc-950/95 px-4 text-white">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/15 bg-white/5">
            <Video className="h-5 w-5" />
          </span>
          <button
            type="button"
            onClick={onRestore}
            className="min-w-0 flex-1 text-left"
          >
            <span className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-white/55">
              Live call
            </span>
            <span className="block truncate text-sm font-semibold">
              {displayRoomName}
            </span>
          </button>
          <button
            type="button"
            onClick={onRestore}
            aria-label="Restore call"
            className="rounded-xl border border-white/15 p-2.5 transition hover:bg-white/10"
          >
            <Maximize2 className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => void leaveOnce()}
            aria-label="Leave call"
            className="rounded-xl bg-red-600 p-2.5 transition hover:bg-red-500"
          >
            <PhoneOff className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <header className="absolute inset-x-0 top-0 z-20 flex h-16 items-center gap-3 border-b border-white/10 bg-zinc-950/95 px-4 text-white md:px-5">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-white/55">
              Live call
            </p>
            <h2 className="truncate text-sm font-semibold md:text-base">
              {displayRoomName}
            </h2>
          </div>
          <span className="hidden rounded-full border border-white/15 px-3 py-1 text-xs font-semibold text-white/65 sm:inline-flex">
            Connected by LiveKit
          </span>
          <button
            type="button"
            onClick={onMinimize}
            aria-label="Minimize call"
            className="rounded-xl border border-white/15 p-2.5 transition hover:bg-white/10"
          >
            <Minimize2 className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => void leaveOnce()}
            aria-label="Leave call"
            className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-3 py-2.5 text-sm font-semibold transition hover:bg-red-500"
          >
            <PhoneOff className="h-4 w-4" />
            <span className="hidden sm:inline">Leave</span>
          </button>
        </header>
      )}

      <div
        className={`absolute inset-0 bg-black ${
          minimized ? "pointer-events-none opacity-0" : "pt-16 opacity-100"
        }`}
      >
        <LiveKitRoom
          serverUrl={serverUrl}
          token={token}
          connect
          audio={AUDIO_CAPTURE_OPTIONS}
          video
          options={ROOM_OPTIONS}
          onDisconnected={handleDisconnected}
          data-lk-theme="default"
          className="redocx-livekit-room h-full min-h-0"
        >
          <VideoConference />
          <RoomAudioRenderer />
        </LiveKitRoom>
      </div>

      <style jsx global>{`
        .redocx-livekit-room,
        .redocx-livekit-room .lk-video-conference,
        .redocx-livekit-room .lk-video-conference-inner,
        .redocx-livekit-room .lk-grid-layout-wrapper {
          height: 100%;
          min-height: 0;
        }

        .redocx-livekit-room .lk-grid-layout {
          height: 100%;
          align-content: center;
          padding: 0.75rem;
        }

        .redocx-livekit-room .lk-participant-tile {
          min-height: 0;
          overflow: hidden;
          background: #050505;
        }

        .redocx-livekit-room .lk-participant-media-video,
        .redocx-livekit-room video {
          object-fit: contain !important;
          background: #050505;
        }

        .redocx-livekit-room .lk-control-bar {
          flex-shrink: 0;
          border-top-color: rgba(255, 255, 255, 0.12);
          background: rgba(9, 9, 11, 0.96);
        }
      `}</style>
    </section>
  );
}