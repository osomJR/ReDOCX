"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import {
  Camera,
  CameraOff,
  Headphones,
  Maximize2,
  Mic,
  MicOff,
  Minimize2,
  PhoneOff,
  ShieldCheck,
  Video,
} from "lucide-react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  VideoConference,
} from "@livekit/components-react";

const AUDIO_CAPTURE_OPTIONS = Object.freeze({
  autoGainControl: true,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  voiceIsolation: { ideal: true },
});

const ROOM_OPTIONS = Object.freeze({
  adaptiveStream: { pauseVideoInBackground: false },
  disconnectOnPageLeave: false,
  dynacast: true,
  publishDefaults: { dtx: true, forceStereo: false, red: true },
});

function callErrorMessage(error) {
  return (
    error?.message || "The secure media connection could not be established."
  );
}

export default function TeamCallRoom({
  serverUrl,
  token,
  roomName,
  mediaType = "video",
  minimized = false,
  isHost = false,
  canEndForEveryone = isHost,
  onEnd,
  onLeave,
  onMinimize,
  onRestore,
}) {
  const [joinPreferences, setJoinPreferences] = useState(null);
  const [microphoneEnabled, setMicrophoneEnabled] = useState(false);
  const [cameraEnabled, setCameraEnabled] = useState(false);
  const [connectionState, setConnectionState] = useState("prejoin");
  const [connectionError, setConnectionError] = useState("");
  const leavingRef = useRef(false);
  const endingRef = useRef(false);
  const connectedRef = useRef(false);

  const normalizedMediaType = mediaType === "audio" ? "audio" : "video";
  const canConnect = Boolean(serverUrl && token);
  const hasApprovedJoin = Boolean(joinPreferences);

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

  const endOnce = useCallback(async () => {
    if (endingRef.current || !canEndForEveryone) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm("End this call for everyone?")
    ) {
      return;
    }
    endingRef.current = true;
    try {
      await onEnd?.();
    } finally {
      endingRef.current = false;
    }
  }, [canEndForEveryone, onEnd]);

  const approveJoin = useCallback(() => {
    // This click is the consent boundary. No LiveKit room and no capture track
    // exist before it. Both devices default to off.
    setConnectionError("");
    setConnectionState("connecting");
    setJoinPreferences({
      audio: Boolean(microphoneEnabled),
      video: normalizedMediaType === "video" && Boolean(cameraEnabled),
    });
  }, [cameraEnabled, microphoneEnabled, normalizedMediaType]);

  const handleConnected = useCallback(() => {
    connectedRef.current = true;
    setConnectionState("connected");
    setConnectionError("");
  }, []);

  const handleError = useCallback((error) => {
    connectedRef.current = false;
    setConnectionError(callErrorMessage(error));
    setConnectionState("prejoin");
    setJoinPreferences(null);
  }, []);

  const handleDisconnected = useCallback(() => {
    if (connectedRef.current) {
      connectedRef.current = false;
      void leaveOnce();
      return;
    }
    setConnectionState("prejoin");
    setJoinPreferences(null);
  }, [leaveOnce]);

  if (!canConnect) {
    return (
      <section className="fixed bottom-5 right-5 z-[140] max-w-sm rounded-3xl border border-red-400/30 bg-red-950/95 p-5 text-red-100 shadow-2xl">
        <h2 className="text-lg font-semibold">Call unavailable</h2>
        <p className="mt-2 text-sm text-red-100/80">
          Missing LiveKit connection details. Start or join the call again to
          request a fresh short-lived token.
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

  if (!hasApprovedJoin) {
    return (
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="call-prejoin-title"
        className="fixed inset-0 z-[150] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
      >
        <div className="w-full max-w-lg rounded-3xl border border-white/15 bg-zinc-950 p-5 text-white shadow-2xl md:p-7">
          <div className="flex items-start gap-4">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/15 bg-white/5">
              {normalizedMediaType === "audio" ? (
                <Headphones className="h-5 w-5" />
              ) : (
                <Video className="h-5 w-5" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-white/55">
                Device privacy
              </p>
              <h2
                id="call-prejoin-title"
                className="mt-1 text-xl font-semibold"
              >
                Choose before joining
              </h2>
              <p className="mt-2 text-sm leading-6 text-white/65">
                Your microphone and camera are off. ReDOCX will not request or
                publish either device until you choose a setting and press Join.
              </p>
            </div>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <button
              type="button"
              aria-pressed={microphoneEnabled}
              onClick={() => setMicrophoneEnabled((enabled) => !enabled)}
              className={`flex items-center gap-3 rounded-2xl border p-4 text-left transition ${
                microphoneEnabled
                  ? "border-emerald-400/50 bg-emerald-400/10"
                  : "border-white/15 bg-white/5 hover:bg-white/10"
              }`}
            >
              {microphoneEnabled ? (
                <Mic className="h-5 w-5" />
              ) : (
                <MicOff className="h-5 w-5" />
              )}
              <span>
                <span className="block text-sm font-semibold">Microphone</span>
                <span className="mt-0.5 block text-xs text-white/55">
                  {microphoneEnabled ? "On when you join" : "Off when you join"}
                </span>
              </span>
            </button>

            <button
              type="button"
              aria-pressed={cameraEnabled}
              disabled={normalizedMediaType === "audio"}
              onClick={() => setCameraEnabled((enabled) => !enabled)}
              className={`flex items-center gap-3 rounded-2xl border p-4 text-left transition disabled:cursor-not-allowed disabled:opacity-45 ${
                cameraEnabled && normalizedMediaType === "video"
                  ? "border-emerald-400/50 bg-emerald-400/10"
                  : "border-white/15 bg-white/5 hover:bg-white/10"
              }`}
            >
              {cameraEnabled && normalizedMediaType === "video" ? (
                <Camera className="h-5 w-5" />
              ) : (
                <CameraOff className="h-5 w-5" />
              )}
              <span>
                <span className="block text-sm font-semibold">Camera</span>
                <span className="mt-0.5 block text-xs text-white/55">
                  {normalizedMediaType === "audio"
                    ? "Unavailable for an audio call"
                    : cameraEnabled
                      ? "On when you join"
                      : "Off when you join"}
                </span>
              </span>
            </button>
          </div>

          {connectionError ? (
            <p
              role="alert"
              className="mt-4 rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-2 text-sm text-red-100"
            >
              {connectionError}
            </p>
          ) : null}

          <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={() => void leaveOnce()}
              className="rounded-xl border border-white/15 px-4 py-3 text-sm font-semibold text-white/75 transition hover:bg-white/10"
            >
              {isHost ? "Cancel call" : "Not now"}
            </button>
            <button
              type="button"
              onClick={approveJoin}
              disabled={connectionState === "connecting"}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-5 py-3 text-sm font-semibold text-black transition hover:bg-emerald-400 disabled:opacity-60"
            >
              <ShieldCheck className="h-4 w-4" />
              Join with selected settings
            </button>
          </div>
        </div>
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
      aria-label={`Active ${normalizedMediaType} call`}
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
              {connectionState === "connected" ? "Live call" : "Connecting"}
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
        <header className="absolute inset-x-0 top-0 z-20 flex h-16 items-center gap-2 border-b border-white/10 bg-zinc-950/95 px-4 text-white md:px-5">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-white/55">
              {connectionState === "connected"
                ? "Live call"
                : "Connecting securely"}
            </p>
            <h2 className="truncate text-sm font-semibold md:text-base">
              {displayRoomName}
            </h2>
          </div>
          {canEndForEveryone ? (
            <button
              type="button"
              onClick={() => void endOnce()}
              aria-label="End call for everyone"
              className="inline-flex rounded-xl border border-red-400/40 px-3 py-2 text-xs font-semibold text-red-100 transition hover:bg-red-500/15"
            >
              <span className="hidden sm:inline">End for everyone</span>
              <PhoneOff className="h-4 w-4 sm:hidden" />
            </button>
          ) : null}
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
        className={`absolute inset-0 bg-black ${minimized ? "pointer-events-none opacity-0" : "pt-16 opacity-100"}`}
      >
        <LiveKitRoom
          serverUrl={serverUrl}
          token={token}
          connect
          audio={joinPreferences.audio ? AUDIO_CAPTURE_OPTIONS : false}
          video={joinPreferences.video}
          options={ROOM_OPTIONS}
          onConnected={handleConnected}
          onDisconnected={handleDisconnected}
          onError={handleError}
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
