"use client";

import { CheckCircle2, Files, X } from "lucide-react";

const labels = {
  en: {
    acceptedOne: "1 file accepted",
    acceptedMany: (count) => `${count} files accepted`,
    limit: (count) => `Plan limit: ${count}`,
    confirmation: "All selected files are listed below and will be processed.",
    listLabel: "Selected files",
    remove: "Remove",
  },
  fr: {
    acceptedOne: "1 fichier accepté",
    acceptedMany: (count) => `${count} fichiers acceptés`,
    limit: (count) => `Limite du forfait : ${count}`,
    confirmation:
      "Tous les fichiers sélectionnés sont affichés ci-dessous et seront traités.",
    listLabel: "Fichiers sélectionnés",
    remove: "Retirer",
  },
};

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Confirmation for a validated file selection. File mutation remains owned by
 * the page so validation state and parallel metadata collections stay aligned.
 */
export default function SelectedFilesSummary({
  files = [],
  limit,
  language = "en",
  className = "mt-5",
  renderDetails,
  onRemoveFile,
  disabled = false,
}) {
  const selectedFiles = Array.from(files || []).filter(Boolean);
  if (!selectedFiles.length) return null;

  const t = labels[language] || labels.en;
  const acceptedLabel =
    selectedFiles.length === 1
      ? t.acceptedOne
      : t.acceptedMany(selectedFiles.length);

  return (
    <section
      className={`${className} rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-4`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <div className="flex items-start gap-3">
        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium text-emerald-100">{acceptedLabel}</p>
            {Number.isFinite(limit) && limit > 0 ? (
              <p className="text-xs text-emerald-100/70">{t.limit(limit)}</p>
            ) : null}
          </div>

          {selectedFiles.length > 1 ? (
            <p className="mt-1 text-xs text-emerald-100/70">{t.confirmation}</p>
          ) : null}

          <ul
            className="mt-3 max-h-48 space-y-2 overflow-y-auto pr-1"
            aria-label={t.listLabel}
          >
            {selectedFiles.map((file, index) => {
              const details = renderDetails?.(file, index);

              return (
                <li
                  key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
                  className="flex items-start gap-2 rounded-xl border border-emerald-300/10 bg-black/10 px-3 py-2"
                >
                  <Files className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" />
                  <div className="min-w-0 flex-1">
                    <p
                      className="truncate text-sm text-emerald-100"
                      title={file.name}
                    >
                      {index + 1}. {file.name}
                    </p>
                    <p className="text-xs text-emerald-100/70">
                      {formatBytes(file.size)}
                    </p>
                    {details ? (
                      <div className="mt-0.5 text-xs text-emerald-100/70">
                        {details}
                      </div>
                    ) : null}
                  </div>
                  {typeof onRemoveFile === "function" ? (
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onRemoveFile(file, index)}
                      className="rounded-lg p-1 text-emerald-100/70 transition hover:bg-emerald-300/10 hover:text-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
                      aria-label={`${t.remove} ${file.name}`}
                      title={`${t.remove} ${file.name}`}
                    >
                      <X className="h-4 w-4" />
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </section>
  );
}