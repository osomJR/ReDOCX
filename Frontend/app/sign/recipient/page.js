"use client";

import { useEffect, useMemo, useState } from "react";
import { useLanguage } from "@/components/language_provider";
import {
  recipientSigningPageTranslations,
  resolveErrorMessage,
} from "@/lib/translations";
import { CheckCircle2, FileSignature, Loader2, ShieldCheck } from "lucide-react";



const TOKEN_SESSION_KEY = "redocx:recipient-signing-token";

function tokenFromFragment() {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const fragmentToken = String(params.get("token") || "").trim();
  const token =
    fragmentToken || String(window.sessionStorage.getItem(TOKEN_SESSION_KEY) || "").trim();
  if (token) {
    window.sessionStorage.setItem(TOKEN_SESSION_KEY, token);
    window.history.replaceState(null, "", window.location.pathname);
  }
  return token;
}

async function responsePayload(response) {
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error("SIGNING_LINK_INVALID");
    error.status = response.status;
    error.payload = data;
    error.code = data?.error?.code || data?.detail?.error || "SIGNING_LINK_INVALID";
    throw error;
  }
  return data;
}

function fieldKey(field) {
  return (
    field.field_id ||
    field.label ||
    `${field.document_id || "document_1"}:${field.field_type}:${field.page_number}`
  );
}

function initialsFromName(value) {
  return String(value || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 8);
}

function friendlyFieldLabel(field, t) {
  if (field?.label) return field.label;
  return t.fieldLabels[field?.field_type] || t.fieldLabels.fallback;
}

export default function RecipientSigningPage() {
  const { language } = useLanguage();
  const t =
    recipientSigningPageTranslations[language] ||
    recipientSigningPageTranslations.en;
  const [token, setToken] = useState("");
  const [context, setContext] = useState(null);
  const [documentUrl, setDocumentUrl] = useState("");
  const [activeDocumentId, setActiveDocumentId] = useState("");
  const [reviewedDocumentIds, setReviewedDocumentIds] = useState([]);
  const [fieldValues, setFieldValues] = useState({});
  const [signatureName, setSignatureName] = useState("");
  const [initials, setInitials] = useState("");
  const [consent, setConsent] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    const resolvedToken = tokenFromFragment();
    if (!resolvedToken) {
      setError(t.invalid);
      setLoading(false);
      return undefined;
    }
    setToken(resolvedToken);

    const controller = new AbortController();
    async function loadSigningRequest() {
      try {
        const headers = { "X-ReDOCX-Signing-Token": resolvedToken };
        const contextResponse = await fetch("/api/sign/recipient", {
          method: "GET",
          cache: "no-store",
          signal: controller.signal,
          headers,
        });
        const nextContext = await responsePayload(contextResponse);
        setContext(nextContext);
        const signerName = nextContext?.signer?.name || "";
        setSignatureName(signerName);
        setInitials(initialsFromName(signerName));
        setFieldValues(
          Object.fromEntries(
            (nextContext?.fields || [])
              .filter((field) => field.default_value != null)
              .map((field) => [fieldKey(field), String(field.default_value)]),
          ),
        );

        const firstDocument = nextContext?.documents?.[0] || {
          document_id: "document_1",
        };
        setActiveDocumentId(firstDocument.document_id);

        const documentResponse = await fetch(
          `/api/sign/recipient?document=1&document_id=${encodeURIComponent(
            firstDocument.document_id,
          )}`,
          {
            method: "GET",
            cache: "no-store",
            signal: controller.signal,
            headers,
          },
        );
        if (!documentResponse.ok) {
          await responsePayload(documentResponse);
        }
        setDocumentUrl(URL.createObjectURL(await documentResponse.blob()));
        setReviewedDocumentIds([firstDocument.document_id]);
      } catch (caught) {
        if (caught?.name !== "AbortError") {
          setError(resolveErrorMessage(caught, language, "SIGNING_LINK_INVALID"));
        }
      } finally {
        setLoading(false);
      }
    }

    void loadSigningRequest();
    return () => {
      controller.abort();
    };
  }, []);

  useEffect(
    () => () => {
      if (documentUrl) URL.revokeObjectURL(documentUrl);
    },
    [documentUrl],
  );

  async function selectDocument(documentId) {
    if (!token || documentId === activeDocumentId) return;
    setError("");
    try {
      const response = await fetch(
        `/api/sign/recipient?document=1&document_id=${encodeURIComponent(documentId)}`,
        {
          method: "GET",
          cache: "no-store",
          headers: { "X-ReDOCX-Signing-Token": token },
        },
      );
      if (!response.ok) await responsePayload(response);
      setDocumentUrl(URL.createObjectURL(await response.blob()));
      setActiveDocumentId(documentId);
      setReviewedDocumentIds((current) =>
        current.includes(documentId) ? current : [...current, documentId],
      );
    } catch (caught) {
      setError(resolveErrorMessage(caught, language, "SIGNING_LINK_INVALID"));
    }
  }

  const signableFields = useMemo(
    () =>
      (context?.fields || []).filter((field) =>
        ["signature", "initials"].includes(field.field_type),
      ),
    [context],
  );
  const signableField = signableFields[0] || null;
  const needsSignature = signableFields.some(
    (field) => field.field_type === "signature",
  );
  const needsInitials = signableFields.some(
    (field) => field.field_type === "initials",
  );
  const activeDocument =
    (context?.documents || []).find(
      (document) => document.document_id === activeDocumentId,
    ) || context?.documents?.[0];

  async function submitSignature(event) {
    event.preventDefault();
    setError("");

    if (!signableField) {
      setError(t.noAssignedSignature);
      return;
    }
    if (reviewedDocumentIds.length < (context?.documents?.length || 1)) {
      setError(t.reviewEveryDocument);
      return;
    }
    if (needsSignature && !signatureName.trim()) {
      setError(t.enterLegalSignature);
      return;
    }
    if (needsInitials && !initials.trim()) {
      setError(t.enterInitials);
      return;
    }
    for (const field of context?.fields || []) {
      const key = fieldKey(field);
      if (
        field.required &&
        field.field_type === "text" &&
        !String(fieldValues[key] || "").trim()
      ) {
        setError(t.requiredField);
        return;
      }
      if (
        field.required &&
        field.field_type === "checkbox" &&
        fieldValues[key] !== "true"
      ) {
        setError(t.requiredCheckbox);
        return;
      }
    }
    if (!consent) {
      setError(t.consentRequired);
      return;
    }

    const submittedFieldValues = { ...fieldValues };
    for (const field of signableFields) {
      submittedFieldValues[fieldKey(field)] =
        field.field_type === "initials" ? initials.trim() : signatureName.trim();
    }

    setSubmitting(true);
    try {
      const response = await fetch("/api/sign/recipient", {
        method: "POST",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-ReDOCX-Signing-Token": token,
        },
        body: JSON.stringify({
          signature: {
            operation: "add_signature",
            signature_type: "typed",
            typed_name:
              signatureName.trim() || context?.signer?.name || initials.trim(),
            consent_accepted: true,
            page_number: signableField.page_number,
            rectangle: signableField.rectangle,
          },
          field_values: submittedFieldValues,
        }),
      });
      setResult(await responsePayload(response));
      window.sessionStorage.removeItem(TOKEN_SESSION_KEY);
      setToken("");
    } catch (caught) {
      setError(resolveErrorMessage(caught, language, "SIGNATURE_APPLY_FAILED"));
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="app-page flex min-h-screen items-center justify-center p-6 app-text">
        <p className="inline-flex items-center gap-3">
          <Loader2 className="h-5 w-5 animate-spin" /> {t.loading}
        </p>
      </main>
    );
  }

  if (result) {
    return (
      <main className="app-page flex min-h-screen items-center justify-center p-6 app-text">
        <section className="w-full max-w-xl rounded-3xl border border-emerald-500/30 bg-emerald-500/10 p-8 text-center">
          <CheckCircle2 className="mx-auto h-12 w-12 text-emerald-500" />
          <h1 className="mt-4 text-2xl font-semibold">{t.completed}</h1>
          <p className="mt-3 text-sm app-text-muted">{t.completedHelp}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="app-page min-h-screen px-4 py-8 app-text md:px-8">
      <header className="mx-auto mb-6 max-w-7xl rounded-3xl border app-surface-strong p-6">
        <p className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] app-text-soft">
          <ShieldCheck className="h-4 w-4" /> {t.secureSign}
        </p>
        <h1 className="mt-3 text-3xl font-semibold">{t.title}</h1>
        {context ? (
          <>
            <p className="mt-2 app-text-muted">
              {context.document_filename} · {t.requestedFor} {context.signer?.name}
            </p>
            <p className="mt-3 max-w-3xl text-sm app-text-muted">
              {t.instructions}
            </p>
          </>
        ) : null}
      </header>

      {error ? (
        <p className="mx-auto mb-6 max-w-7xl rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-red-600 dark:text-red-300">
          {error}
        </p>
      ) : null}

      {context ? (
        <div className="mx-auto grid max-w-7xl gap-6 lg:grid-cols-[1.35fr_0.65fr]">
          <section className="overflow-hidden rounded-3xl border app-surface-strong">
            <h2 className="border-b px-5 py-4 text-lg font-semibold">
              {t.document}
            </h2>
            {(context.documents || []).length > 1 ? (
              <nav
                className="flex gap-2 overflow-x-auto border-b p-3"
                aria-label={t.envelopeDocuments}
              >
                {context.documents.map((document, index) => (
                  <button
                    key={document.document_id}
                    type="button"
                    onClick={() => selectDocument(document.document_id)}
                    className={`shrink-0 rounded-xl border px-3 py-2 text-left text-xs ${
                      document.document_id === activeDocumentId
                        ? "app-surface-strong font-semibold"
                        : "app-surface"
                    }`}
                  >
                    {index + 1}. {document.filename}
                    {reviewedDocumentIds.includes(document.document_id)
                      ? " ✓"
                      : ""}
                  </button>
                ))}
              </nav>
            ) : null}
            {documentUrl ? (
              <iframe
                title={activeDocument?.filename || context.document_filename}
                src={documentUrl}
                className="h-[72vh] w-full bg-white"
              />
            ) : null}
          </section>

          <form onSubmit={submitSignature} className="space-y-5">
            <section className="rounded-3xl border app-surface-strong p-5">
              <h2 className="flex items-center gap-2 text-lg font-semibold">
                <FileSignature className="h-5 w-5" /> {t.fields}
              </h2>
              <div className="mt-4 space-y-4">
                {needsSignature ? (
                  <label className="block text-sm font-medium">
                    {t.signature}
                    <span className="mt-1 block text-xs font-normal app-text-muted">
                      {t.signatureHelp}
                    </span>
                    <input
                      required
                      value={signatureName}
                      onChange={(event) => setSignatureName(event.target.value)}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 outline-none"
                    />
                  </label>
                ) : null}

                {needsInitials ? (
                  <label className="block text-sm font-medium">
                    {t.initials}
                    <span className="mt-1 block text-xs font-normal app-text-muted">
                      {t.initialsHelp}
                    </span>
                    <input
                      required
                      maxLength={8}
                      value={initials}
                      onChange={(event) => setInitials(event.target.value)}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 uppercase outline-none"
                    />
                  </label>
                ) : null}

                {(context.fields || []).map((field) => {
                  const key = fieldKey(field);
                  if (["signature", "initials"].includes(field.field_type)) {
                    return null;
                  }
                  if (["name", "email", "date_signed"].includes(field.field_type)) {
                    return (
                      <p key={key} className="rounded-2xl border app-surface p-3 text-sm app-text-muted">
                        <span className="font-medium app-text">
                          {friendlyFieldLabel(field, t)}
                        </span>{" "}
                        · {t.autoFilled}
                      </p>
                    );
                  }
                  if (field.field_type === "checkbox") {
                    return (
                      <label key={key} className="flex items-start gap-3 rounded-2xl border app-surface p-3 text-sm">
                        <input
                          type="checkbox"
                          required={Boolean(field.required)}
                          checked={fieldValues[key] === "true"}
                          onChange={(event) =>
                            setFieldValues((current) => ({
                              ...current,
                              [key]: String(event.target.checked),
                            }))
                          }
                        />
                        <span>
                          {friendlyFieldLabel(field, t)}
                          {field.required ? " *" : ""}
                        </span>
                      </label>
                    );
                  }
                  return (
                    <label key={key} className="block text-sm font-medium">
                      {friendlyFieldLabel(field, t)}
                      {field.required ? " *" : ""}
                      <input
                        required={Boolean(field.required)}
                        value={fieldValues[key] || ""}
                        onChange={(event) =>
                          setFieldValues((current) => ({
                            ...current,
                            [key]: event.target.value,
                          }))
                        }
                        className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 outline-none"
                      />
                    </label>
                  );
                })}
              </div>
            </section>

            <label className="flex items-start gap-3 rounded-3xl border app-surface-strong p-5 text-sm">
              <input
                type="checkbox"
                required
                checked={consent}
                onChange={(event) => setConsent(event.target.checked)}
              />
              {t.consent}
            </label>

            <button
              type="submit"
              disabled={submitting}
              className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 font-semibold text-[var(--app-button-text)] disabled:opacity-60"
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {submitting ? t.signing : t.submit}
            </button>
          </form>
        </div>
      ) : null}
    </main>
  );
}
