"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  useRoomContext,
} from "@livekit/components-react";
import { RoomEvent } from "livekit-client";

const AUDIO_CAPTURE_OPTIONS = Object.freeze({
  autoGainControl: true,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  voiceIsolation: { ideal: true },
});

const RECOVERY_DELAY_MS = 2_500;
const MAX_RECOVERY_ATTEMPTS = 2;
const MEDIA_STATS_INTERVAL_MS = 15_000;
const MAX_STATS_TRACKS = 12;

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
    starting: "Starting call…",
    liveCall: "Live call",
    connecting: "Connecting",
    connectingSecurely: "Connecting securely",
    reconnecting: "Restoring connection",
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
    starting: "Démarrage de l’appel…",
    liveCall: "Appel en cours",
    connecting: "Connexion",
    connectingSecurely: "Connexion sécurisée",
    reconnecting: "Rétablissement de la connexion",
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

function createTelemetryId(eventType) {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `${eventType}:${crypto.randomUUID()}`;
  }
  return `${eventType}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function reportTelemetry(onTelemetry, eventType, metrics = {}) {
  try {
    onTelemetry?.({
      eventType,
      clientEventId: createTelemetryId(eventType),
      occurredAt: new Date().toISOString(),
      metrics,
    });
  } catch {
    // Quality telemetry is best-effort and must never interrupt a call.
  }
}

function aggregateRtcReports(reports) {
  const metrics = {
    report_count: reports.length,
    inbound_packets_lost: 0,
    inbound_jitter_max_seconds: 0,
    inbound_frames_dropped: 0,
    inbound_fps_min: null,
    round_trip_time_max_seconds: 0,
  };

  for (const report of reports) {
    report?.forEach?.((stat) => {
      if (stat.type === "inbound-rtp" && !stat.isRemote) {
        metrics.inbound_packets_lost += Number(stat.packetsLost || 0);
        metrics.inbound_jitter_max_seconds = Math.max(
          metrics.inbound_jitter_max_seconds,
          Number(stat.jitter || 0),
        );
        metrics.inbound_frames_dropped += Number(stat.framesDropped || 0);
        const fps = Number(stat.framesPerSecond || 0);
        if (fps > 0) {
          metrics.inbound_fps_min =
            metrics.inbound_fps_min == null
              ? fps
              : Math.min(metrics.inbound_fps_min, fps);
        }
      }
      if (
        (stat.type === "candidate-pair" &&
          stat.state === "succeeded" &&
          (stat.nominated || stat.selected)) ||
        stat.type === "remote-inbound-rtp"
      ) {
        metrics.round_trip_time_max_seconds = Math.max(
          metrics.round_trip_time_max_seconds,
          Number(stat.currentRoundTripTime || stat.roundTripTime || 0),
        );
      }
    });
  }

  return metrics;
}

function sampleUiFrameRate(durationMs = 750) {
  return new Promise((resolve) => {
    const startedAt = performance.now();
    let frames = 0;
    const step = (now) => {
      frames += 1;
      if (now - startedAt >= durationMs) {
        resolve(Math.round((frames * 1000) / Math.max(1, now - startedAt)));
        return;
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}

function CallRoomObserver({ onTelemetry, onReconnecting, onReconnected }) {
  const room = useRoomContext();
  const longTaskMetricsRef = useRef({ count: 0, durationMs: 0 });

  useEffect(() => {
    if (typeof PerformanceObserver === "undefined") return undefined;
    let observer;
    try {
      observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          longTaskMetricsRef.current.count += 1;
          longTaskMetricsRef.current.durationMs += Number(entry.duration || 0);
        }
      });
      observer.observe({ entryTypes: ["longtask"] });
    } catch {
      return undefined;
    }
    return () => observer?.disconnect();
  }, []);

  useEffect(() => {
    if (!room) return undefined;

    const handleReconnecting = () => {
      reportTelemetry(onTelemetry, "connection.reconnecting");
      onReconnecting?.();
    };
    const handleReconnected = () => {
      reportTelemetry(onTelemetry, "connection.reconnected");
      onReconnected?.();
    };
    const handleConnectionQuality = (quality, participant) => {
      reportTelemetry(onTelemetry, "connection.quality", {
        quality: String(quality || "unknown"),
        participant_identity: String(participant?.identity || ""),
        participant_count: Number(room.numParticipants || 0),
      });
    };

    room.on(RoomEvent.Reconnecting, handleReconnecting);
    room.on(RoomEvent.Reconnected, handleReconnected);
    room.on(RoomEvent.ConnectionQualityChanged, handleConnectionQuality);

    return () => {
      room.off(RoomEvent.Reconnecting, handleReconnecting);
      room.off(RoomEvent.Reconnected, handleReconnected);
      room.off(RoomEvent.ConnectionQualityChanged, handleConnectionQuality);
    };
  }, [onReconnected, onReconnecting, onTelemetry, room]);

  useEffect(() => {
    if (!room) return undefined;

    const collect = async () => {
      const publications = [
        ...room.localParticipant.trackPublications.values(),
        ...[...room.remoteParticipants.values()].flatMap((participant) =>
          [...participant.trackPublications.values()],
        ),
      ]
        .filter((publication) => publication?.track?.getRTCStatsReport)
        .slice(0, MAX_STATS_TRACKS);

      const reports = await Promise.allSettled(
        publications.map((publication) => publication.track.getRTCStatsReport()),
      );
      const successfulReports = reports
        .filter((result) => result.status === "fulfilled")
        .map((result) => result.value);

      if (successfulReports.length) {
        const uiFps = await sampleUiFrameRate();
        const longTasks = longTaskMetricsRef.current;
        longTaskMetricsRef.current = { count: 0, durationMs: 0 };
        reportTelemetry(onTelemetry, "media.stats", {
          ...aggregateRtcReports(successfulReports),
          participant_count: Number(room.numParticipants || 0),
          sampled_track_count: successfulReports.length,
          ui_fps: uiFps,
          long_task_count: longTasks.count,
          long_task_duration_ms: Math.round(longTasks.durationMs),
          hardware_concurrency: Number(navigator.hardwareConcurrency || 0),
          device_memory_gb: Number(navigator.deviceMemory || 0),
        });
      }
    };

    const initialSampleId = window.setTimeout(() => {
      void collect().catch(() => {});
    }, 5_000);
    const intervalId = window.setInterval(() => {
      void collect().catch(() => {});
    }, MEDIA_STATS_INTERVAL_MS);

    return () => {
      window.clearTimeout(initialSampleId);
      window.clearInterval(intervalId);
    };
  }, [onTelemetry, room]);

  return null;
}

export default function TeamCallRoom({
  serverUrl,
  token,
  roomName,
  mediaType = "video",
  participantCount = 2,
  callCreatedAt = null,
  intentPreparedAt = null,
  intentKind = "join",
  language = "en",
  minimized = false,
  isHost = false,
  canEndForEveryone = isHost,
  onPrepareConnection,
  onRecover,
  onCancelPrejoin,
  onTelemetry,
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
  const [connectionGeneration, setConnectionGeneration] = useState(0);
  const leavingRef = useRef(false);
  const endingRef = useRef(false);
  const intentionalDisconnectRef = useRef(false);
  const recoveryAttemptsRef = useRef(0);
  const recoveryInFlightRef = useRef(false);
  const connectionRequestedAtRef = useRef(0);
  const mediaConnectionStartedAtRef = useRef(0);

  const normalizedMediaType = mediaType === "audio" ? "audio" : "video";
  const hasApprovedJoin = Boolean(joinPreferences);
  const canConnect = Boolean(serverUrl && token);
  const isLargeCall = Number(participantCount || 0) >= 9;

  const roomOptions = useMemo(
    () => ({
      adaptiveStream: {
        pauseVideoInBackground: true,
        pixelDensity: 1,
      },
      disconnectOnPageLeave: false,
      dynacast: true,
      publishDefaults: { dtx: true, forceStereo: false, red: true },
      videoCaptureDefaults: {
        resolution: isLargeCall
          ? { width: 640, height: 360, frameRate: 24 }
          : { width: 1280, height: 720, frameRate: 30 },
      },
    }),
    [isLargeCall],
  );

  const displayRoomName = useMemo(() => {
    if (!roomName) return t.teamCall;
    return String(roomName)
      .replace(/^org-/, t.organization)
      .replaceAll("-", " ");
  }, [roomName, t.organization, t.teamCall]);

  useEffect(() => {
    reportTelemetry(onTelemetry, "prejoin.opened", {
      media_type: normalizedMediaType,
      participant_count: Number(participantCount || 0),
    });
  }, [normalizedMediaType, onTelemetry, participantCount]);

  const leaveOnce = useCallback(
    async (reason = "user_left") => {
      if (leavingRef.current) return;
      leavingRef.current = true;
      intentionalDisconnectRef.current = true;
      try {
        await onLeave?.({ reason });
      } finally {
        leavingRef.current = false;
      }
    },
    [onLeave],
  );

  const endOnce = useCallback(async () => {
    if (endingRef.current || !canEndForEveryone) return;
    if (typeof window !== "undefined" && !window.confirm(t.endConfirm)) return;
    endingRef.current = true;
    intentionalDisconnectRef.current = true;
    try {
      await onEnd?.();
    } finally {
      endingRef.current = false;
    }
  }, [canEndForEveryone, onEnd, t.endConfirm]);

  const approveJoin = useCallback(async () => {
    if (connectionState === "connecting") return;
    const preferences = {
      audio: Boolean(microphoneEnabled),
      video: normalizedMediaType === "video" && Boolean(cameraEnabled),
    };
    connectionRequestedAtRef.current = performance.now();
    setConnectionError("");
    setConnectionState("connecting");
    reportTelemetry(onTelemetry, "connection.requested", {
      media_type: normalizedMediaType,
      audio_enabled: preferences.audio,
      video_enabled: preferences.video,
      participant_count: Number(participantCount || 0),
    });

    try {
      await onPrepareConnection?.(preferences);
      mediaConnectionStartedAtRef.current = performance.now();
      setJoinPreferences(preferences);
    } catch (error) {
      setConnectionError(callErrorMessage(error, t));
      setConnectionState("prejoin");
    }
  }, [
    cameraEnabled,
    connectionState,
    microphoneEnabled,
    normalizedMediaType,
    onPrepareConnection,
    onTelemetry,
    participantCount,
    t,
  ]);

  const handleConnected = useCallback(() => {
    recoveryAttemptsRef.current = 0;
    recoveryInFlightRef.current = false;
    setConnectionState("connected");
    setConnectionError("");
    const nowEpoch = Date.now();
    const callCreatedEpoch = callCreatedAt
      ? new Date(callCreatedAt).getTime()
      : 0;
    const requestToConnectedMs = Math.max(
      0,
      Math.round(performance.now() - connectionRequestedAtRef.current),
    );
    const mediaEstablishmentMs = mediaConnectionStartedAtRef.current
      ? Math.max(
          0,
          Math.round(performance.now() - mediaConnectionStartedAtRef.current),
        )
      : null;
    const normalizedIntentKind = intentKind === "start" ? "start" : "join";
    reportTelemetry(onTelemetry, "connection.connected", {
      connect_duration_ms: requestToConnectedMs,
      start_request_to_connected_ms:
        normalizedIntentKind === "start" ? requestToConnectedMs : null,
      join_request_to_connected_ms:
        normalizedIntentKind === "join" ? requestToConnectedMs : null,
      media_connection_establishment_ms: mediaEstablishmentMs,
      ice_media_establishment_ms: mediaEstablishmentMs,
      call_start_to_connected_ms:
        normalizedIntentKind === "start" &&
        Number.isFinite(callCreatedEpoch) &&
        callCreatedEpoch > 0
          ? Math.max(0, nowEpoch - callCreatedEpoch)
          : null,
      prejoin_to_connected_ms:
        Number(intentPreparedAt || 0) > 0
          ? Math.max(0, nowEpoch - Number(intentPreparedAt))
          : null,
      participant_count: Number(participantCount || 0),
    });
  }, [
    callCreatedAt,
    intentKind,
    intentPreparedAt,
    onTelemetry,
    participantCount,
  ]);

  const handleError = useCallback(
    (error) => {
      setConnectionError(callErrorMessage(error, t));
      setConnectionState("prejoin");
      setJoinPreferences(null);
    },
    [t],
  );

  const attemptRecovery = useCallback(async () => {
    if (intentionalDisconnectRef.current || !onRecover) return false;

    while (recoveryAttemptsRef.current < MAX_RECOVERY_ATTEMPTS) {
      recoveryAttemptsRef.current += 1;
      const attempt = recoveryAttemptsRef.current;
      setConnectionState("reconnecting");
      await new Promise((resolve) =>
        window.setTimeout(resolve, RECOVERY_DELAY_MS),
      );
      try {
        await onRecover({ attempt });
        connectionRequestedAtRef.current = performance.now();
        mediaConnectionStartedAtRef.current = performance.now();
        setConnectionGeneration((value) => value + 1);
        return true;
      } catch {
        // Try again within the bounded recovery window.
      }
    }

    return false;
  }, [onRecover]);

  const handleDisconnected = useCallback(async () => {
    if (intentionalDisconnectRef.current || recoveryInFlightRef.current) return;
    recoveryInFlightRef.current = true;
    reportTelemetry(onTelemetry, "connection.disconnected", {
      recoverable: true,
    });
    const recovered = await attemptRecovery();
    if (!recovered) {
      reportTelemetry(onTelemetry, "connection.recovery_failed", {
        attempts: recoveryAttemptsRef.current,
      });
      await leaveOnce("connection_recovery_failed");
    }
    recoveryInFlightRef.current = false;
  }, [attemptRecovery, leaveOnce, onTelemetry]);

  const handleCancelPrejoin = useCallback(async () => {
    intentionalDisconnectRef.current = true;
    await onCancelPrejoin?.();
  }, [onCancelPrejoin]);

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
              <h2 id="call-prejoin-title" className="mt-1 text-xl font-semibold">
                {t.chooseBeforeJoining}
              </h2>
              <p className="mt-2 text-sm leading-6 text-white/65">{t.privacyBody}</p>
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
              {microphoneEnabled ? <Mic className="h-5 w-5" /> : <MicOff className="h-5 w-5" />}
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
                <Camera className="h-5 w-5" />
              ) : (
                <CameraOff className="h-5 w-5" />
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
            <p role="alert" className="mt-4 rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
              {connectionError}
            </p>
          ) : null}

          <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={() => void handleCancelPrejoin()}
              disabled={connectionState === "connecting"}
              className="rounded-xl border border-white/15 px-4 py-3 text-sm font-semibold transition hover:bg-white/10 disabled:opacity-50"
            >
              {t.notNow}
            </button>
            <button
              type="button"
              onClick={() => void approveJoin()}
              disabled={connectionState === "connecting"}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-white px-4 py-3 text-sm font-semibold text-black transition hover:bg-white/90 disabled:opacity-60"
            >
              <ShieldCheck className="h-4 w-4" />
              {connectionState === "connecting" ? t.starting : t.join}
            </button>
          </div>
        </div>
      </section>
    );
  }

  if (!canConnect) {
    return (
      <section role="alertdialog" aria-live="assertive" className="fixed bottom-5 right-5 z-[140] max-w-sm rounded-3xl border border-red-400/30 bg-red-950/95 p-5 text-red-100 shadow-2xl">
        <h2 className="text-lg font-semibold">{t.unavailableTitle}</h2>
        <p className="mt-2 text-sm text-red-100/80">{t.unavailableBody}</p>
        <button type="button" onClick={() => void leaveOnce("missing_connection_details")} className="mt-4 inline-flex items-center gap-2 rounded-xl border border-red-200/30 px-3 py-2 text-sm font-semibold">
          <PhoneOff className="h-4 w-4" />
          {t.close}
        </button>
      </section>
    );
  }

  const statusLabel =
    connectionState === "connected"
      ? t.liveCall
      : connectionState === "reconnecting"
        ? t.reconnecting
        : t.connectingSecurely;

  return (
    <section className={`fixed z-[140] overflow-hidden border border-white/15 bg-black shadow-2xl transition-all ${
      minimized
        ? "bottom-4 right-4 h-20 w-[min(22rem,calc(100vw-2rem))] rounded-2xl"
        : "inset-0 rounded-none"
    }`}>
      {minimized ? (
        <div className="flex h-full items-center gap-3 bg-zinc-950 px-4 text-white">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-white/55">
              {normalizedMediaType === "audio" ? t.activeAudio : t.activeVideo}
            </p>
            <p className="truncate text-sm font-semibold">{displayRoomName}</p>
          </div>
          <button type="button" onClick={onRestore} aria-label={t.restore} className="rounded-xl border border-white/15 p-2.5 hover:bg-white/10">
            <Maximize2 className="h-4 w-4" />
          </button>
          <button type="button" onClick={() => void leaveOnce()} aria-label={t.leave} className="rounded-xl bg-red-600 p-2.5 hover:bg-red-500">
            <PhoneOff className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <header className="absolute inset-x-0 top-0 z-20 flex h-16 items-center gap-2 border-b border-white/10 bg-zinc-950/95 px-4 text-white md:px-5">
          <div className="min-w-0 flex-1">
            <p role="status" aria-live="polite" className="text-[10px] font-semibold uppercase tracking-[0.14em] text-white/55">
              {statusLabel}
            </p>
            <h2 className="truncate text-sm font-semibold md:text-base">{displayRoomName}</h2>
          </div>
          {canEndForEveryone ? (
            <button type="button" onClick={() => void endOnce()} aria-label={t.endEveryone} className="inline-flex rounded-xl border border-red-400/40 px-3 py-2 text-xs font-semibold text-red-100 hover:bg-red-500/15">
              <span className="hidden sm:inline">{t.endEveryoneLabel}</span>
              <PhoneOff className="h-4 w-4 sm:hidden" />
            </button>
          ) : null}
          <button type="button" onClick={onMinimize} aria-label={t.minimize} className="rounded-xl border border-white/15 p-2.5 hover:bg-white/10">
            <Minimize2 className="h-4 w-4" />
          </button>
          <button type="button" onClick={() => void leaveOnce()} aria-label={t.leave} className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-3 py-2.5 text-sm font-semibold hover:bg-red-500">
            <PhoneOff className="h-4 w-4" />
            <span className="hidden sm:inline">{t.leave}</span>
          </button>
        </header>
      )}

      <div className={`absolute inset-0 bg-black ${minimized ? "pointer-events-none opacity-0" : "pt-16 opacity-100"}`}>
        <LiveKitRoom
          key={`${roomName}:${connectionGeneration}:${token}`}
          serverUrl={serverUrl}
          token={token}
          connect
          audio={joinPreferences.audio ? AUDIO_CAPTURE_OPTIONS : false}
          video={joinPreferences.video ? roomOptions.videoCaptureDefaults : false}
          options={roomOptions}
          onConnected={handleConnected}
          onDisconnected={() => void handleDisconnected()}
          onError={handleError}
          data-lk-theme="default"
          className="redocx-livekit-room h-full min-h-0"
        >
          <CallRoomObserver
            onTelemetry={onTelemetry}
            onReconnecting={() => setConnectionState("reconnecting")}
            onReconnected={() => setConnectionState("connected")}
          />
          {!minimized ? <VideoConference /> : null}
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
