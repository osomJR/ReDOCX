"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileSignature,
  Loader2,
  PenLine,
} from "lucide-react";

function normalizeArtifactUrl(url) {
  if (!url) return "";
  const raw = String(url);
  if (/^https?:\/\//i.test(raw)) return raw;
  return raw.replace(/^\/api\/v1\/analyzer\/artifacts\//, "/api/analyzer/artifacts/");
}

function fieldTypeLabel(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function messageFromError(error) {
  return (
    error?.payload?.detail?.message ||
    error?.payload?.detail?.error ||
    error?.payload?.error?.message ||
    error?.payload?.message ||
    error?.message ||
    "Request failed."
  );
}

async function readJson(response) {
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const error = new Error(
      data?.detail?.message ||
        data?.detail?.error ||
        data?.error?.message ||
        data?.message ||
        "Request failed.",
    );
    error.status = response.status;
    error.payload = data;
    throw error;
  }

  return data;
}

function firstSignableField(fields) {
  return (
    fields.find((field) =>
      ["signature", "initials"].includes(String(field?.field_type || "")),
    ) || fields[0] || null
  );
}

function defaultFieldValue(field, signer) {
  const type = String(field?.field_type || "");

  if (type === "name") return signer?.name || "";
  if (type === "email") return signer?.email || "";
  if (type === "date_signed") return new Date().toISOString().slice(0, 10);
  if (type === "checkbox") return "true";

  return field?.default_value || "";
}

function ResultLinks({ result }) {
  const signedPdfUrl = normalizeArtifactUrl(result?.signed_pdf?.download_url);
  const certificateUrl = normalizeArtifactUrl(result?.audit_certificate?.download_url);
  const previewUrl = normalizeArtifactUrl(result?.latest_preview?.preview_pdf?.download_url);

  if (!signedPdfUrl && !certificateUrl && !previewUrl) {
    return null;
  }

  return (
    <div className="mt-5 flex flex-wrap gap-3">
      {signedPdfUrl ? (
        <a
          href={signedPdfUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)]"
        >
          <Download className="h-4 w-4" />
          Download signed PDF
        </a>
      ) : null}

      {certificateUrl ? (
        <a
          href={certificateUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 rounded-2xl border app-surface px-4 py-2.5 text-sm font-semibold app-text"
        >
          <Download className="h-4 w-4" />
          Download certificate
        </a>
      ) : null}

      {previewUrl ? (
        <a
          href={previewUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 rounded-2xl border app-surface px-4 py-2.5 text-sm font-semibold app-text"
        >
          <FileSignature className="h-4 w-4" />
          Open preview
        </a>
      ) : null}
    </div>
  );
}

export default function RecipientSigningPage() {
  const params = useParams();
  const token = useMemo(() => String(params?.token || "").trim(), [params?.token]);

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [context, setContext] = useState(null);
  const [typedName, setTypedName] = useState("");
  const [consentAccepted, setConsentAccepted] = useState(false);
  const [fieldValues, setFieldValues] = useState({});
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function loadSigningContext() {
      if (!token) {
        setError("Missing signing token.");
        setLoading(false);
        return;
      }

      setLoading(true);
      setError("");

      try {
        const data = await fetch(`/api/esignature/recipient/${encodeURIComponent(token)}`, {
          method: "GET",
          cache: "no-store",
        }).then(readJson);

        if (cancelled) return;

        setContext(data);
        setTypedName(data?.signer?.name || "");

        const nextValues = {};
        for (const field of data?.fields || []) {
          const key = field.field_id || field.label || `${field.field_type}:${field.page_number}`;
          nextValues[key] = defaultFieldValue(field, data?.signer);
        }
        setFieldValues(nextValues);
      } catch (err) {
        if (!cancelled) {
          setError(messageFromError(err));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadSigningContext();

    return () => {
      cancelled = true;
    };
  }, [token]);

  const fields = context?.fields || [];
  const signer = context?.signer || {};
  const envelope = context?.envelope || {};
  const documentUrl = normalizeArtifactUrl(context?.document?.download_url);
  const signableField = firstSignableField(fields);
  const alreadySigned = Boolean(context?.already_signed || result);

  function setFieldValue(field, value) {
    const key = field.field_id || field.label || `${field.field_type}:${field.page_number}`;
    setFieldValues((current) => ({
      ...current,
      [key]: value,
    }));
  }

  async function handleSubmit(event) {
    event.preventDefault();

    if (submitting || alreadySigned) return;

    setError("");
    setResult(null);

    if (!signableField) {
      setError("No signing fields are assigned to this recipient.");
      return;
    }

    if (!typedName.trim()) {
      setError("Type your name to create your signature.");
      return;
    }

    if (!consentAccepted) {
      setError("You must accept the electronic signature consent before signing.");
      return;
    }

    setSubmitting(true);

    try {
      const data = await fetch(`/api/esignature/recipient/${encodeURIComponent(token)}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          signature: {
            operation: "add_signature",
            page_number: signableField.page_number,
            rectangle: signableField.rectangle,
            signature_type: "typed",
            typed_name: typedName.trim(),
            consent_accepted: true,
          },
          field_values: fieldValues,
        }),
      }).then(readJson);

      setResult(data?.result || data);
      setContext((current) =>
        current
          ? {
              ...current,
              already_signed: true,
              signer: {
                ...current.signer,
                status: "signed",
              },
              envelope: {
                ...current.envelope,
                status: data?.result?.status || current.envelope?.status,
              },
            }
          : current,
      );
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[var(--app-bg)] px-4 py-8 text-[var(--app-text)] sm:px-6 lg:px-8">
      <div className="mx-auto w-full max-w-4xl">
        <header className="rounded-3xl border app-surface-strong p-6 shadow-sm">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] app-text-soft">
                ReDOCX Sign
              </p>
              <h1 className="mt-2 text-2xl font-semibold app-text">
                Review and sign document
              </h1>
              <p className="mt-2 text-sm app-text-muted">
                Complete the fields assigned to your email address, then submit your electronic signature.
              </p>
            </div>

            <div className="flex h-12 w-12 items-center justify-center rounded-2xl border app-surface">
              <FileSignature className="h-6 w-6 app-text-muted" />
            </div>
          </div>
        </header>

        {loading ? (
          <section className="mt-6 rounded-3xl border app-surface-strong p-6">
            <div className="flex items-center gap-3 text-sm app-text-muted">
              <Loader2 className="h-5 w-5 animate-spin" />
              Loading signing request...
            </div>
          </section>
        ) : null}

        {error ? (
          <section className="mt-6 rounded-3xl border border-red-400/30 bg-red-400/10 p-5">
            <div className="flex gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-500" />
              <div>
                <h2 className="text-sm font-semibold text-red-600 dark:text-red-200">
                  Signing request unavailable
                </h2>
                <p className="mt-1 text-sm text-red-700 dark:text-red-100/80">
                  {error}
                </p>
              </div>
            </div>
          </section>
        ) : null}

        {!loading && context && !error ? (
          <form onSubmit={handleSubmit} className="mt-6 grid gap-6">
            <section className="rounded-3xl border app-surface-strong p-6">
              <div className="grid gap-4 md:grid-cols-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                    Envelope
                  </p>
                  <p className="mt-1 break-all text-sm font-medium app-text">
                    {envelope.envelope_id}
                  </p>
                </div>

                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                    Signer
                  </p>
                  <p className="mt-1 text-sm font-medium app-text">
                    {signer.name || "Signer"}
                  </p>
                  <p className="text-xs app-text-muted">{signer.email}</p>
                </div>

                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                    Status
                  </p>
                  <p className="mt-1 text-sm font-medium app-text">
                    {String(signer.status || envelope.status || "pending").replaceAll("_", " ")}
                  </p>
                </div>
              </div>

              {documentUrl ? (
                <a
                  href={documentUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-5 inline-flex items-center gap-2 rounded-2xl border app-surface px-4 py-2.5 text-sm font-semibold app-text"
                >
                  <FileSignature className="h-4 w-4" />
                  Open document
                </a>
              ) : (
                <p className="mt-5 rounded-2xl border app-surface px-4 py-3 text-sm app-text-muted">
                  Document preview is not public for this link. The assigned signing fields are shown below.
                </p>
              )}
            </section>

            <section className="rounded-3xl border app-surface-strong p-6">
              <h2 className="text-lg font-semibold app-text">Assigned fields</h2>

              {fields.length ? (
                <div className="mt-4 grid gap-3">
                  {fields.map((field, index) => {
                    const key = field.field_id || field.label || `${field.field_type}:${field.page_number}:${index}`;
                    const type = String(field.field_type || "");
                    const isCheckbox = type === "checkbox";
                    const isEditableText = ["text", "checkbox"].includes(type);

                    return (
                      <div key={key} className="rounded-2xl border app-surface p-4">
                        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                          <div>
                            <p className="text-sm font-semibold app-text">
                              {field.label || fieldTypeLabel(type)}
                            </p>
                            <p className="text-xs app-text-muted">
                              Page {field.page_number} · {fieldTypeLabel(type)}
                            </p>
                          </div>

                          {field.required ? (
                            <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-3 py-1 text-xs font-semibold text-amber-700 dark:text-amber-200">
                              Required
                            </span>
                          ) : null}
                        </div>

                        {isEditableText ? (
                          <div className="mt-3">
                            {isCheckbox ? (
                              <label className="inline-flex items-center gap-2 text-sm app-text">
                                <input
                                  type="checkbox"
                                  checked={String(fieldValues[key] || "").toLowerCase() !== "false"}
                                  onChange={(event) => setFieldValue(field, event.target.checked ? "true" : "false")}
                                  className="h-4 w-4 rounded border-[var(--app-border)]"
                                  disabled={alreadySigned}
                                />
                                Mark as checked
                              </label>
                            ) : (
                              <input
                                value={fieldValues[key] || ""}
                                onChange={(event) => setFieldValue(field, event.target.value)}
                                disabled={alreadySigned}
                                placeholder={field.default_value || "Enter value"}
                                className="w-full rounded-2xl border app-surface px-4 py-3 text-sm app-text outline-none focus:border-[var(--app-border-strong)] disabled:opacity-60"
                              />
                            )}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="mt-3 text-sm app-text-muted">
                  No fields are assigned to this signer.
                </p>
              )}
            </section>

            <section className="rounded-3xl border app-surface-strong p-6">
              <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
                <PenLine className="h-5 w-5" />
                Electronic signature
              </h2>

              <label className="mt-4 block text-sm font-medium app-text">
                Type your full name
                <input
                  value={typedName}
                  onChange={(event) => setTypedName(event.target.value)}
                  disabled={alreadySigned}
                  className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text outline-none focus:border-[var(--app-border-strong)] disabled:opacity-60"
                />
              </label>

              <label className="mt-4 flex gap-3 rounded-2xl border app-surface p-4 text-sm app-text">
                <input
                  type="checkbox"
                  checked={consentAccepted || alreadySigned}
                  disabled={alreadySigned}
                  onChange={(event) => setConsentAccepted(event.target.checked)}
                  className="mt-1 h-4 w-4 shrink-0 rounded border-[var(--app-border)]"
                />
                <span>
                  I agree to sign this document electronically and understand that my typed name will be applied as my signature.
                </span>
              </label>

              {result ? (
                <div className="mt-5 rounded-2xl border border-emerald-400/30 bg-emerald-400/10 p-4">
                  <div className="flex gap-3">
                    <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />
                    <div>
                      <h3 className="text-sm font-semibold text-emerald-700 dark:text-emerald-200">
                        Signature submitted
                      </h3>
                      <p className="mt-1 text-sm text-emerald-700 dark:text-emerald-100/80">
                        Envelope status: {String(result?.status || "signed").replaceAll("_", " ")}
                      </p>
                    </div>
                  </div>

                  <ResultLinks result={result} />
                </div>
              ) : null}

              <button
                type="submit"
                disabled={submitting || alreadySigned}
                className="mt-5 inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <PenLine className="h-4 w-4" />}
                {alreadySigned ? "Signed" : "Submit signature"}
              </button>
            </section>
          </form>
        ) : null}
      </div>
    </main>
  );
}
