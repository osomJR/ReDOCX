"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, FileSignature, Loader2, ShieldCheck } from "lucide-react";

const COPY = {
  title: "Review and sign document",
  loading: "Loading your secure signing request...",
  invalid: "This signing link is invalid or no longer available.",
  document: "Document",
  fields: "Your fields",
  signature: "Type your legal signature",
  initials: "Type your initials",
  consent:
    "I have reviewed the document and agree to use this electronic signature.",
  submit: "Sign document",
  signing: "Applying signature...",
  completed: "Your signature was applied successfully.",
};

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
    throw new Error(
      data?.detail?.message || data?.message || COPY.invalid,
    );
  }
  return data;
}

function fieldKey(field) {
  return (
    field.field_id ||
    field.label ||
    `${field.field_type}:${field.page_number}`
  );
}

export default function RecipientSigningPage() {
  const [token, setToken] = useState("");
  const [context, setContext] = useState(null);
  const [documentUrl, setDocumentUrl] = useState("");
  const [fieldValues, setFieldValues] = useState({});
  const [signatureName, setSignatureName] = useState("");
  const [consent, setConsent] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    const resolvedToken = tokenFromFragment();
    if (!resolvedToken) {
      setError(COPY.invalid);
      setLoading(false);
      return undefined;
    }
    setToken(resolvedToken);

    const controller = new AbortController();
    let objectUrl = "";

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
        setSignatureName(nextContext?.signer?.name || "");
        setFieldValues(
          Object.fromEntries(
            (nextContext?.fields || [])
              .filter((field) => field.default_value != null)
              .map((field) => [fieldKey(field), String(field.default_value)]),
          ),
        );

        const documentResponse = await fetch(
          "/api/sign/recipient?document=1",
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
        objectUrl = URL.createObjectURL(await documentResponse.blob());
        setDocumentUrl(objectUrl);
      } catch (caught) {
        if (caught?.name !== "AbortError") {
          setError(caught?.message || COPY.invalid);
        }
      } finally {
        setLoading(false);
      }
    }

    void loadSigningRequest();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, []);

  const signableField = useMemo(
    () =>
      (context?.fields || []).find((field) =>
        ["signature", "initials"].includes(field.field_type),
      ) || null,
    [context],
  );

  async function submitSignature(event) {
    event.preventDefault();
    setError("");

    if (!signableField) {
      setError("No signature or initials field is assigned to you.");
      return;
    }
    if (!signatureName.trim()) {
      setError(
        signableField.field_type === "initials"
          ? "Enter your initials."
          : "Enter your legal signature.",
      );
      return;
    }
    if (!consent) {
      setError("You must accept the electronic-signature consent statement.");
      return;
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
            typed_name: signatureName.trim(),
            consent_accepted: true,
            page_number: signableField.page_number,
            rectangle: signableField.rectangle,
          },
          field_values: fieldValues,
        }),
      });
      setResult(await responsePayload(response));
      window.sessionStorage.removeItem(TOKEN_SESSION_KEY);
      setToken("");
    } catch (caught) {
      setError(caught?.message || "Could not apply your signature.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="app-page flex min-h-screen items-center justify-center p-6 app-text">
        <p className="inline-flex items-center gap-3">
          <Loader2 className="h-5 w-5 animate-spin" /> {COPY.loading}
        </p>
      </main>
    );
  }

  if (result) {
    return (
      <main className="app-page flex min-h-screen items-center justify-center p-6 app-text">
        <section className="w-full max-w-xl rounded-3xl border border-emerald-500/30 bg-emerald-500/10 p-8 text-center">
          <CheckCircle2 className="mx-auto h-12 w-12 text-emerald-500" />
          <h1 className="mt-4 text-2xl font-semibold">{COPY.completed}</h1>
          <p className="mt-2 app-text-muted">
            Envelope status: {result?.result?.status || "signed"}
          </p>
          <p className="mt-4 text-sm app-text-muted">
            You may safely close this page. This one-time signing link is now invalid.
          </p>
        </section>
      </main>
    );
  }

  return (
    <main className="app-page min-h-screen px-4 py-8 app-text md:px-8">
      <header className="mx-auto mb-6 max-w-7xl rounded-3xl border app-surface-strong p-6">
        <p className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] app-text-soft">
          <ShieldCheck className="h-4 w-4" /> Secure ReDOCX Sign
        </p>
        <h1 className="mt-3 text-3xl font-semibold">{COPY.title}</h1>
        {context ? (
          <p className="mt-2 app-text-muted">
            {context.document_filename} · Requested for {context.signer?.name}
          </p>
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
              {COPY.document}
            </h2>
            {documentUrl ? (
              <iframe
                title={context.document_filename}
                src={documentUrl}
                className="h-[72vh] w-full bg-white"
              />
            ) : null}
          </section>

          <form onSubmit={submitSignature} className="space-y-5">
            <section className="rounded-3xl border app-surface-strong p-5">
              <h2 className="flex items-center gap-2 text-lg font-semibold">
                <FileSignature className="h-5 w-5" /> {COPY.fields}
              </h2>
              <div className="mt-4 space-y-4">
                {(context.fields || []).map((field) => {
                  const key = fieldKey(field);
                  if (["signature", "initials"].includes(field.field_type)) {
                    return (
                      <label key={key} className="block text-sm font-medium">
                        {field.field_type === "initials"
                          ? COPY.initials
                          : COPY.signature}
                        <input
                          value={signatureName}
                          onChange={(event) => setSignatureName(event.target.value)}
                          className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 outline-none"
                        />
                      </label>
                    );
                  }
                  if (["name", "email", "date_signed"].includes(field.field_type)) {
                    return (
                      <p key={key} className="rounded-2xl border app-surface p-3 text-sm app-text-muted">
                        {field.label || field.field_type}: filled automatically
                      </p>
                    );
                  }
                  if (field.field_type === "checkbox") {
                    return (
                      <label key={key} className="flex items-start gap-3 text-sm">
                        <input
                          type="checkbox"
                          checked={fieldValues[key] === "true"}
                          onChange={(event) =>
                            setFieldValues((current) => ({
                              ...current,
                              [key]: String(event.target.checked),
                            }))
                          }
                        />
                        {field.label || "Confirm"}
                      </label>
                    );
                  }
                  return (
                    <label key={key} className="block text-sm font-medium">
                      {field.label || "Text"}
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
                checked={consent}
                onChange={(event) => setConsent(event.target.checked)}
              />
              {COPY.consent}
            </label>

            <button
              type="submit"
              disabled={submitting}
              className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 font-semibold text-[var(--app-button-text)] disabled:opacity-60"
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {submitting ? COPY.signing : COPY.submit}
            </button>
          </form>
        </div>
      ) : null}
    </main>
  );
}
