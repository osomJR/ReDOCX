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

const COPY = {
  en: {
    secureConnectionError: "The secure media connection could not be established.",
    teamCall: "Team call",
    organization: "Organization ",
    endConfirm: "End this call for everyone?",
    unavailableTitle: "Call unavailable",
    unavailableBody: "Missing LiveKit connection details. Start or join the call again to request a fresh short-lived token.",
    close: "Close",
    devicePrivacy: "Device privacy",
    chooseBeforeJoining: "Choose before joining",
    privacyBody: "Your microphone and camera are off. ReDOCX will not request or publish either device until you choose a setting and press Join.",
    microphone: "Microphone",
    camera: "Camera",
    onWhenJoin: "On when you join",
    offWhenJoin: "Off when you join",
    unavailableAudio: "Unavailable for an audio call",
    cancelCall: "Cancel call",
    notNow: "Not now",
    join: "Join with selected settings",
    liveCall: "Live call",
    connecting: "Connecting",
    connectingSecurely: "Connecting securely",
    restore: "Restore call",
    leave: "Leave call",
    endEveryone: "End call for everyone",
    endEveryoneLabel: "End for everyone",
    minimize: "Minimize call",
    activeAudio: "Active audio call",
    activeVideo: "Active video call",
  },
  fr: {
    secureConnectionError: "La connexion multimédia sécurisée n’a pas pu être établie.",
    teamCall: "Appel d’équipe",
    organization: "Organisation ",
    endConfirm: "Mettre fin à cet appel pour tout le monde ?",
    unavailableTitle: "Appel indisponible",
    unavailableBody: "Les informations de connexion LiveKit sont manquantes. Démarrez ou rejoignez de nouveau l’appel pour obtenir un nouveau jeton à courte durée de vie.",
    close: "Fermer",
    devicePrivacy: "Confidentialité des appareils",
    chooseBeforeJoining: "Choisissez avant de rejoindre",
    privacyBody: "Votre microphone et votre caméra sont désactivés. ReDOCX ne demandera ni ne diffusera ces appareils avant votre choix et votre validation.",
    microphone: "Microphone",
    camera: "Caméra",
    onWhenJoin: "Activé à votre arrivée",
    offWhenJoin: "Désactivé à votre arrivée",
    unavailableAudio: "Indisponible pour un appel audio",
    cancelCall: "Annuler l’appel",
    notNow: "Pas maintenant",
    join: "Rejoindre avec ces paramètres",
    liveCall: "Appel en cours",
    connecting: "Connexion",
    connectingSecurely: "Connexion sécurisée",
    restore: "Restaurer l’appel",
    leave: "Quitter l’appel",
    endEveryone: "Mettre fin à l’appel pour tout le monde",
    endEveryoneLabel: "Terminer pour tous",
    minimize: "Réduire l’appel",
    activeAudio: "Appel audio actif",
    activeVideo: "Appel vidéo actif",
  },
};


function callErrorMessage(error, t) {
  return error?.message || t.secureConnectionError;
}

export default function TeamCallRoom({
  serverUrl,
  token,
  roomName,
  mediaType = "video",
  language = "en",
  minimized = false,
  isHost = false,
  canEndForEveryone = isHost,
  onEnd,
  onLeave,
  onMinimize,
  onRestore,
}) {
  const t = COPY[language] || COPY.en;
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
    if (!roomName) return t.teamCall;
    return String(roomName)
      .replace(/^org-/, t.organization)
      .replaceAll("-", " ");
  }, [roomName, t.organization, t.teamCall]);

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
      !window.confirm(t.endConfirm)
    ) {
      return;
    }
    endingRef.current = true;
    try {
      await onEnd?.();
    } finally {
      endingRef.current = false;
    }
  }, [canEndForEveryone, onEnd, t.endConfirm]);

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
    setConnectionError(callErrorMessage(error, t));
    setConnectionState("prejoin");
    setJoinPreferences(null);
  }, [t]);

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
      <section role="alertdialog" aria-live="assertive" className="fixed bottom-5 right-5 z-[140] max-w-sm rounded-3xl border border-red-400/30 bg-red-950/95 p-5 text-red-100 shadow-2xl">
        <h2 className="text-lg font-semibold">{t.unavailableTitle}</h2>
        <p className="mt-2 text-sm text-red-100/80">
          {t.unavailableBody}
        </p>
        <button
          type="button"
          onClick={() => void leaveOnce()}
          className="mt-4 inline-flex items-center gap-2 rounded-xl border border-red-200/30 px-3 py-2 text-sm font-semibold"
        >
          <PhoneOff aria-hidden="true" className="h-4 w-4" />
          {t.close}
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
                <Headphones aria-hidden="true" className="h-5 w-5" />
              ) : (
                <Video aria-hidden="true" className="h-5 w-5" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-white/55">
                {t.devicePrivacy}
              </p>
              <h2
                id="call-prejoin-title"
                className="mt-1 text-xl font-semibold"
              >
                {t.chooseBeforeJoining}
              </h2>
              <p className="mt-2 text-sm leading-6 text-white/65">
                {t.privacyBody}
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
                <Mic aria-hidden="true" className="h-5 w-5" />
              ) : (
                <MicOff aria-hidden="true" className="h-5 w-5" />
              )}
              <span>
                <span className="block text-sm font-semibold">{t.microphone}</span>
                <span className="mt-0.5 block text-xs text-white/55">
                  {microphoneEnabled ? t.onWhenJoin : t.offWhenJoin}
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
                <Camera aria-hidden="true" className="h-5 w-5" />
              ) : (
                <CameraOff aria-hidden="true" className="h-5 w-5" />
              )}
              <span>
                <span className="block text-sm font-semibold">{t.camera}</span>
                <span className="mt-0.5 block text-xs text-white/55">
                  {normalizedMediaType === "audio"
                    ? t.unavailableAudio
                    : cameraEnabled
                      ? t.onWhenJoin
                      : t.offWhenJoin}
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
              {isHost ? t.cancelCall : t.notNow}
            </button>
            <button
              type="button"
              onClick={approveJoin}
              disabled={connectionState === "connecting"}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-5 py-3 text-sm font-semibold text-black transition hover:bg-emerald-400 disabled:opacity-60"
            >
              <ShieldCheck aria-hidden="true" className="h-4 w-4" />
              {t.join}
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
      aria-label={normalizedMediaType === "audio" ? t.activeAudio : t.activeVideo}
    >
      {minimized ? (
        <div className="absolute inset-0 z-20 flex items-center gap-3 bg-zinc-950/95 px-4 text-white">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/15 bg-white/5">
            <Video aria-hidden="true" className="h-5 w-5" />
          </span>
          <button
            type="button"
            onClick={onRestore}
            className="min-w-0 flex-1 text-left"
          >
            <span role="status" aria-live="polite" className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-white/55">
              {connectionState === "connected" ? t.liveCall : t.connecting}
            </span>
            <span className="block truncate text-sm font-semibold">
              {displayRoomName}
            </span>
          </button>
          <button
            type="button"
            onClick={onRestore}
            aria-label={t.restore}
            className="rounded-xl border border-white/15 p-2.5 transition hover:bg-white/10"
          >
            <Maximize2 aria-hidden="true" className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => void leaveOnce()}
            aria-label={t.leave}
            className="rounded-xl bg-red-600 p-2.5 transition hover:bg-red-500"
          >
            <PhoneOff aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <header className="absolute inset-x-0 top-0 z-20 flex h-16 items-center gap-2 border-b border-white/10 bg-zinc-950/95 px-4 text-white md:px-5">
          <div className="min-w-0 flex-1">
            <p role="status" aria-live="polite" className="text-[10px] font-semibold uppercase tracking-[0.14em] text-white/55">
              {connectionState === "connected"
                ? t.liveCall
                : t.connectingSecurely}
            </p>
            <h2 className="truncate text-sm font-semibold md:text-base">
              {displayRoomName}
            </h2>
          </div>
          {canEndForEveryone ? (
            <button
              type="button"
              onClick={() => void endOnce()}
              aria-label={t.endEveryone}
              className="inline-flex rounded-xl border border-red-400/40 px-3 py-2 text-xs font-semibold text-red-100 transition hover:bg-red-500/15"
            >
              <span className="hidden sm:inline">{t.endEveryoneLabel}</span>
              <PhoneOff aria-hidden="true" className="h-4 w-4 sm:hidden" />
            </button>
          ) : null}
          <button
            type="button"
            onClick={onMinimize}
            aria-label={t.minimize}
            className="rounded-xl border border-white/15 p-2.5 transition hover:bg-white/10"
          >
            <Minimize2 aria-hidden="true" className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => void leaveOnce()}
            aria-label={t.leave}
            className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-3 py-2.5 text-sm font-semibold transition hover:bg-red-500"
          >
            <PhoneOff aria-hidden="true" className="h-4 w-4" />
            <span className="hidden sm:inline">{t.leave}</span>
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
