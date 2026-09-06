"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  buildAnalyzerArtifactUrl,
  normalizeAnalyzerArtifactUrl,
} from "@/lib/api_client";

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function resultNodeFromResponse(responseData) {
  const candidates = [
    responseData?.result,
    responseData?.analyzer_response?.result,
    responseData?.analyzerResponse?.result,
    responseData?.data?.result,
    responseData?.response?.result,
    responseData,
  ];

  return candidates.find(isObject) || null;
}

function normalizeArtifactUrl(artifact) {
  if (!isObject(artifact)) return "";

  const returnedUrl = String(
    artifact.download_url || artifact.downloadUrl || "",
  ).trim();
  const storageKey = String(
    artifact.storage_key || artifact.storageKey || "",
  ).trim();

  const normalized = returnedUrl
    ? normalizeAnalyzerArtifactUrl(returnedUrl)
    : storageKey
      ? buildAnalyzerArtifactUrl(storageKey)
      : "";

  if (!normalized) return "";

  try {
    const parsed = new URL(
      normalized,
      typeof window !== "undefined"
        ? window.location.origin
        : "https://redocx.invalid",
    );
    parsed.searchParams.set("disposition", "inline");

    if (
      typeof window !== "undefined" &&
      parsed.origin === window.location.origin
    ) {
      return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    }

    return parsed.toString();
  } catch {
    const separator = normalized.includes("?") ? "&" : "?";
    return `${normalized}${separator}disposition=inline`;
  }
}

function normalizeSubtitleCues(value) {
  if (!Array.isArray(value)) return [];

  return value
    .map((cue) => {
      if (!isObject(cue)) return null;

      const startSeconds = Number(cue.start_seconds ?? cue.startSeconds);
      const endSeconds = Number(cue.end_seconds ?? cue.endSeconds);
      const text = String(cue.text || "").trim();
      const speakerLabel = String(
        cue.speaker_label || cue.speakerLabel || "",
      ).trim();

      if (
        !Number.isFinite(startSeconds) ||
        !Number.isFinite(endSeconds) ||
        startSeconds < 0 ||
        endSeconds <= startSeconds ||
        !text
      ) {
        return null;
      }

      return {
        startSeconds,
        endSeconds,
        text,
        speakerLabel,
      };
    })
    .filter(Boolean)
    .sort((left, right) =>
      left.startSeconds === right.startSeconds
        ? left.endSeconds - right.endSeconds
        : left.startSeconds - right.startSeconds,
    );
}

export function extractTranscriptionSubtitlePayload(responseData) {
  const result = resultNodeFromResponse(responseData);
  if (!result) return null;

  const playbackArtifact =
    result.playback_artifact || result.playbackArtifact || null;
  const cues = normalizeSubtitleCues(
    result.subtitle_cues || result.subtitleCues || [],
  );
  const playbackUrl = normalizeArtifactUrl(playbackArtifact);
  const mediaType = String(
    playbackArtifact?.media_type || playbackArtifact?.mediaType || "",
  )
    .trim()
    .toLowerCase();
  const mimeType = String(
    playbackArtifact?.mime_type || playbackArtifact?.mimeType || "",
  ).trim();

  if (!playbackUrl || !cues.length || !["audio", "video"].includes(mediaType)) {
    return null;
  }

  return {
    playbackUrl,
    mediaType,
    mimeType:
      mimeType || (mediaType === "video" ? "video/mp4" : "audio/mp4"),
    cues,
  };
}

function formatVttTimestamp(totalSeconds) {
  const safe = Math.max(0, Number(totalSeconds) || 0);
  const totalMilliseconds = Math.round(safe * 1000);
  const hours = Math.floor(totalMilliseconds / 3_600_000);
  const minutes = Math.floor((totalMilliseconds % 3_600_000) / 60_000);
  const seconds = Math.floor((totalMilliseconds % 60_000) / 1000);
  const milliseconds = totalMilliseconds % 1000;

  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

function escapeVttText(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function buildWebVtt(cues) {
  const blocks = cues.map((cue, index) => {
    const speakerPrefix = cue.speakerLabel
      ? `${escapeVttText(cue.speakerLabel)}: `
      : "";
    return [
      String(index + 1),
      `${formatVttTimestamp(cue.startSeconds)} --> ${formatVttTimestamp(cue.endSeconds)}`,
      `${speakerPrefix}${escapeVttText(cue.text)}`,
    ].join("\n");
  });

  return `WEBVTT\n\n${blocks.join("\n\n")}\n`;
}

function activeCuesAtTime(cues, currentTime) {
  const time = Number(currentTime);
  if (!Number.isFinite(time) || time < 0) return [];

  return cues.filter(
    (cue) => time >= cue.startSeconds && time < cue.endSeconds,
  );
}

export default function TranscriptionSubtitlePlayer({
  responseData,
  title = "Synchronized playback",
  subtitleLabel = "Subtitles",
  className = "",
}) {
  const payload = useMemo(
    () => extractTranscriptionSubtitlePayload(responseData),
    [responseData],
  );
  const mediaRef = useRef(null);
  const animationFrameRef = useRef(null);
  const activeKeyRef = useRef("");
  const [activeCues, setActiveCues] = useState([]);
  const [trackUrl, setTrackUrl] = useState("");

  useEffect(() => {
    if (!payload?.cues?.length) {
      setTrackUrl("");
      return undefined;
    }

    const url = URL.createObjectURL(
      new Blob([buildWebVtt(payload.cues)], { type: "text/vtt;charset=utf-8" }),
    );
    setTrackUrl(url);

    return () => URL.revokeObjectURL(url);
  }, [payload]);

  useEffect(() => {
    const media = mediaRef.current;
    if (!media || !payload) return undefined;

    let disposed = false;

    const sync = () => {
      if (disposed) return;

      const nextCues = activeCuesAtTime(payload.cues, media.currentTime);
      const nextKey = nextCues
        .map(
          (cue) =>
            `${cue.startSeconds}:${cue.endSeconds}:${cue.speakerLabel}:${cue.text}`,
        )
        .join("|");

      if (nextKey !== activeKeyRef.current) {
        activeKeyRef.current = nextKey;
        setActiveCues(nextCues);
      }
    };

    const stopLoop = () => {
      if (animationFrameRef.current != null) {
        cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
    };

    const runLoop = () => {
      stopLoop();
      const tick = () => {
        sync();
        if (!disposed && !media.paused && !media.ended) {
          animationFrameRef.current = requestAnimationFrame(tick);
        } else {
          animationFrameRef.current = null;
        }
      };
      tick();
    };

    const handlePause = () => {
      stopLoop();
      sync();
    };

    const handleEnded = () => {
      stopLoop();
      sync();
    };

    media.addEventListener("play", runLoop);
    media.addEventListener("pause", handlePause);
    media.addEventListener("ended", handleEnded);
    media.addEventListener("seeked", sync);
    media.addEventListener("timeupdate", sync);
    media.addEventListener("loadedmetadata", sync);
    sync();

    return () => {
      disposed = true;
      stopLoop();
      media.removeEventListener("play", runLoop);
      media.removeEventListener("pause", handlePause);
      media.removeEventListener("ended", handleEnded);
      media.removeEventListener("seeked", sync);
      media.removeEventListener("timeupdate", sync);
      media.removeEventListener("loadedmetadata", sync);
    };
  }, [payload]);

  if (!payload) return null;

  const caption = activeCues.length ? activeCues : [];

  return (
    <section
      className={[
        "rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-sm font-semibold app-text">{title}</p>
        <span className="rounded-full border border-[var(--app-border)] px-2.5 py-1 text-[11px] app-text-soft">
          {subtitleLabel}
        </span>
      </div>

      {payload.mediaType === "video" ? (
        <div className="relative overflow-hidden rounded-xl bg-black">
          <video
            ref={mediaRef}
            src={payload.playbackUrl}
            controls
            playsInline
            preload="metadata"
            className="block max-h-[70vh] w-full bg-black"
          >
            {trackUrl ? (
              <track
                key={trackUrl}
                kind="subtitles"
                src={trackUrl}
                srcLang="und"
                label={subtitleLabel}
              />
            ) : null}
          </video>

          <div
            className="pointer-events-none absolute inset-x-3 bottom-14 flex justify-center px-2 text-center"
            aria-live="off"
            aria-atomic="true"
          >
            {caption.length ? (
              <div className="max-w-[92%] space-y-1 rounded-lg bg-black/80 px-3 py-2 text-sm font-medium leading-6 text-white shadow-2xl md:text-base">
                {caption.map((cue) => (
                  <p
                    key={`${cue.startSeconds}-${cue.endSeconds}-${cue.text}`}
                    className="whitespace-pre-line"
                  >
                    {cue.speakerLabel ? (
                      <span className="mr-1 font-bold">{cue.speakerLabel}:</span>
                    ) : null}
                    {cue.text}
                  </p>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <audio
            ref={mediaRef}
            src={payload.playbackUrl}
            controls
            preload="metadata"
            className="w-full"
          >
            {trackUrl ? (
              <track
                key={trackUrl}
                kind="subtitles"
                src={trackUrl}
                srcLang="und"
                label={subtitleLabel}
              />
            ) : null}
          </audio>

          <div
            className="flex min-h-20 items-center justify-center rounded-xl bg-black/90 px-4 py-3 text-center text-sm font-medium leading-6 text-white md:text-base"
            aria-live="off"
            aria-atomic="true"
          >
            {caption.length ? (
              <div className="space-y-1">
                {caption.map((cue) => (
                  <p
                    key={`${cue.startSeconds}-${cue.endSeconds}-${cue.text}`}
                    className="whitespace-pre-line"
                  >
                    {cue.speakerLabel ? (
                      <span className="mr-1 font-bold">{cue.speakerLabel}:</span>
                    ) : null}
                    {cue.text}
                  </p>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}
