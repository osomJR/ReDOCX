"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileSignature,
  Loader2,
  Mail,
  Plus,
  Send,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { postAnalyzerFeature } from "@/lib/api_client";
import { esignaturePageTranslations } from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import {
  FILE_SECURITY_POLICY,
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const FEATURE_PATH = "e-signature";
const MAX_PDF_SIZE_MB = 100;
const DEFAULT_RECTANGLE = { x: "0.62", y: "0.72", width: "0.26", height: "0.08" };

const copy = esignaturePageTranslations;

const fieldTypes = ["signature", "initials", "date_signed", "name", "email", "text", "checkbox"];
const workflows = [
  ["self_sign", "selfSign"],
  ["send_to_single_recipient", "sendSingle"],
  ["send_to_multiple_recipients", "sendMultiple"],
  ["self_sign_then_send", "selfSignThenSend"],
];

function systemLanguageFor(language) {
  return language === "fr" ? "french" : "english";
}

function fileSizeMb(file) {
  return file.size / (1024 * 1024);
}

function isPdf(file) {
  const type = String(file?.type || "").toLowerCase();
  const name = String(file?.name || "").toLowerCase();
  return type === "application/pdf" || type === "application/x-pdf" || name.endsWith(".pdf");
}

function isEmail(value) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(value || "").trim());
}

function normalizeArtifactUrl(url) {
  if (!url) return "";
  const raw = String(url);
  if (/^https?:\/\//i.test(raw)) return raw;
  return raw.replace(/^\/api\/v1\/analyzer\/artifacts\//, "/api/analyzer/artifacts/");
}

function parseInteger(value, fallback = 1) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : fallback;
}

function parseDecimal(value, fallback) {
  const parsed = Number.parseFloat(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeRectangle(rectangle) {
  const x = parseDecimal(rectangle.x, 0.62);
  const y = parseDecimal(rectangle.y, 0.72);
  const width = parseDecimal(rectangle.width, 0.26);
  const height = parseDecimal(rectangle.height, 0.08);
  if (x < 0 || y < 0 || width <= 0 || height <= 0 || x > 1 || y > 1 || x + width > 1 || y + height > 1) {
    return null;
  }
  return { x, y, width, height };
}

function emptyRecipient(order = 1) {
  return {
    id: `recipient_${Date.now()}_${Math.random().toString(16).slice(2)}`,
    name: "",
    email: "",
    signing_order: order,
    required: true,
  };
}

function emptyField(email = "") {
  return {
    id: `field_${Date.now()}_${Math.random().toString(16).slice(2)}`,
    field_type: "signature",
    assigned_to_email: email,
    page_number: "1",
    rectangle: { ...DEFAULT_RECTANGLE },
    required: true,
    label: "Signature",
    default_value: "",
  };
}

function buildSignatureOperation({
  signatureType,
  typedName,
  drawnImageFile,
  uploadedImageFile,
  rectangle,
}) {
  const base = {
    operation: "add_signature",
    page_number: 1,
    rectangle: rectangle || { x: 0.62, y: 0.72, width: 0.26, height: 0.08 },
    signature_type: signatureType,
    consent_accepted: true,
  };
  if (signatureType === "typed") {
    if (!typedName.trim()) return null;
    return { ...base, typed_name: typedName.trim() };
  }
  if (signatureType === "drawn") {
    if (!drawnImageFile) return null;
    return {
      ...base,
      signature_image_storage_key: "asset:op_self_signature",
    };
  }
  if (signatureType === "uploaded_image") {
    if (!uploadedImageFile) return null;
    return {
      ...base,
      signature_image_storage_key: "asset:op_self_signature",
    };
  }
  return null;
}

function useCanvasSignature(onCapture) {
  const canvasRef = useRef(null);
  const drawingRef = useRef(false);
  const pointsRef = useRef([]);
  const [hasDrawing, setHasDrawing] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    context.lineWidth = 2;
    context.lineCap = "round";
    context.strokeStyle = "#111111";
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
  }, []);

  function pointFromEvent(event) {
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const source = event.touches?.[0] || event;
    return {
      x: Math.max(0, Math.min(canvas.width, ((source.clientX - rect.left) / rect.width) * canvas.width)),
      y: Math.max(0, Math.min(canvas.height, ((source.clientY - rect.top) / rect.height) * canvas.height)),
    };
  }

  function start(event) {
    event.preventDefault();
    drawingRef.current = true;
    const point = pointFromEvent(event);
    pointsRef.current.push([point]);
    const context = canvasRef.current.getContext("2d");
    context.beginPath();
    context.moveTo(point.x, point.y);
  }

  function move(event) {
    if (!drawingRef.current) return;
    event.preventDefault();
    const point = pointFromEvent(event);
    const stroke = pointsRef.current[pointsRef.current.length - 1];
    stroke.push(point);
    const context = canvasRef.current.getContext("2d");
    context.lineTo(point.x, point.y);
    context.stroke();
    setHasDrawing(true);
  }

  function end() {
    const wasDrawing = drawingRef.current;
    drawingRef.current = false;
    const canvas = canvasRef.current;
    if (!wasDrawing || !canvas || !pointsRef.current.some((stroke) => stroke.length > 1)) {
      return;
    }
    canvas.toBlob((blob) => {
      if (!blob) return;
      onCapture(
        new File([blob], "op_self_signature.png", { type: "image/png" }),
      );
    }, "image/png");
  }

  function clear() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    pointsRef.current = [];
    setHasDrawing(false);
    onCapture(null);
  }

  return { canvasRef, hasDrawing, start, move, end, clear };
}

function AuthRequired({ t }) {
  return (
    <section className="rounded-3xl border border-amber-400/30 bg-amber-400/10 p-6">
      <h2 className="text-lg font-semibold app-text">{t.signInTitle}</h2>
      <p className="mt-2 text-sm app-text-muted">{t.signInDescription}</p>
      <a href="/auth/login?returnTo=/esignature" className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]">
        {t.signIn}
      </a>
    </section>
  );
}

function SignatureSourceControls({ t, values, setValues }) {
  const { canvasRef, hasDrawing, start, move, end, clear } =
    useCanvasSignature((file) =>
      setValues((current) => ({ ...current, drawnImageFile: file })),
    );
  const [imagePreview, setImagePreview] = useState("");
  const [assetError, setAssetError] = useState("");

  useEffect(
    () => () => {
      if (imagePreview) URL.revokeObjectURL(imagePreview);
    },
    [imagePreview],
  );

  return (
    <section className="rounded-3xl border app-surface-strong p-5">
      <h2 className="text-lg font-semibold app-text">{t.signatureSource}</h2>
      <div className="mt-4 grid gap-2 sm:grid-cols-3">
        {[
          ["typed", t.typed],
          ["drawn", t.drawn],
          ["uploaded_image", t.uploaded],
        ].map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() =>
              setValues((current) => ({ ...current, signatureType: value }))
            }
            className={`rounded-2xl border px-4 py-3 text-sm font-semibold transition ${values.signatureType === value ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]" : "app-surface app-text hover:bg-neutral-100 dark:hover:bg-[#2d2d33]"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {values.signatureType === "typed" ? (
        <label className="mt-4 block text-sm font-medium app-text">
          {t.typedName}
          <input
            value={values.typedName}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                typedName: event.target.value,
              }))
            }
            className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text outline-none focus:border-[var(--app-border-strong)]"
          />
        </label>
      ) : null}

      {values.signatureType === "drawn" ? (
        <div className="mt-4 space-y-3">
          <p className="text-sm app-text-muted">{t.drawHere}</p>
          <canvas
            ref={canvasRef}
            width={520}
            height={180}
            onMouseDown={start}
            onMouseMove={move}
            onMouseUp={end}
            onMouseLeave={end}
            onTouchStart={start}
            onTouchMove={move}
            onTouchEnd={end}
            className="h-44 w-full touch-none rounded-2xl border bg-white"
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={clear}
              className="rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text"
            >
              {t.clearDrawing}
            </button>
          </div>
          {hasDrawing && values.drawnImageFile ? (
            <p className="text-xs text-emerald-600 dark:text-emerald-300">
              {t.drawingCaptured}
            </p>
          ) : null}
        </div>
      ) : null}

      {values.signatureType === "uploaded_image" ? (
        <div className="mt-4 space-y-3">
          <input
            type="file"
            accept="image/png,image/jpeg,image/jpg,.png,.jpg,.jpeg"
            onChange={async (event) => {
              const selected = event.target.files?.[0] || null;
              const securityError = selected
                ? await validateBrowserUpload(
                    selected,
                    FILE_SECURITY_POLICY.pdfEditImage,
                  )
                : "";
              if (securityError) {
                setAssetError(securityError);
                setValues((current) => ({
                  ...current,
                  uploadedImageFile: null,
                }));
                event.target.value = "";
                return;
              }
              setAssetError("");
              const extension = String(selected?.name || "")
                .toLowerCase()
                .endsWith(".png")
                ? ".png"
                : ".jpg";
              const normalizedFile = selected
                ? new File([selected], `op_self_signature${extension}`, {
                    type: selected.type,
                  })
                : null;
              setValues((current) => ({
                ...current,
                uploadedImageFile: normalizedFile,
              }));
              setImagePreview((current) => {
                if (current) URL.revokeObjectURL(current);
                return selected ? URL.createObjectURL(selected) : "";
              });
            }}
            className="block w-full rounded-2xl border app-surface px-4 py-3 text-sm app-text file:mr-4 file:rounded-xl file:border-0 file:bg-[var(--app-button-bg)] file:px-4 file:py-2 file:text-[var(--app-button-text)]"
          />
          {imagePreview ? (
            <Image
              src={imagePreview}
              alt={t.uploadedPreview}
              width={520}
              height={180}
              unoptimized
              className="max-h-32 rounded-2xl border bg-white object-contain p-2"
            />
          ) : null}
          {assetError ? <p className="text-xs text-red-500">{assetError}</p> : null}
        </div>
      ) : null}
    </section>
  );
}

export default function ESignaturePage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked } = useAccount();
  const t = useMemo(() => copy[language] || copy.en, [language]);

  const [file, setFile] = useState(null);
  const [workflow, setWorkflow] = useState("self_sign");
  const [routingMode, setRoutingMode] = useState("sequential");
  const [signerName, setSignerName] = useState(user?.name || "");
  const [signerEmail, setSignerEmail] = useState(user?.email || "");
  const [recipients, setRecipients] = useState([emptyRecipient(1)]);
  const [fields, setFields] = useState([emptyField(user?.email || "")]);
  const [emailSubject, setEmailSubject] = useState("Please sign this document");
  const [emailMessage, setEmailMessage] = useState("Please review and sign this document.");
  const [expiresInDays, setExpiresInDays] = useState("30");
  const [sendEmails, setSendEmails] = useState(true);
  const [signature, setSignature] = useState({
    signatureType: "typed",
    typedName: user?.name || "",
    drawnImageFile: null,
    uploadedImageFile: null,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);

  useEffect(() => {
    if (user?.email && !signerEmail) setSignerEmail(user.email);
    if (user?.name && !signerName) setSignerName(user.name);
    if (user?.name && !signature.typedName)
      setSignature((current) => ({ ...current, typedName: user.name }));
  }, [user, signerEmail, signerName, signature.typedName]);

  const needsOwnerSignature = workflow === "self_sign" || workflow === "self_sign_then_send";
  const needsRecipients = workflow !== "self_sign";
  const visibleRecipients = useMemo(
    () =>
      workflow === "send_to_single_recipient"
        ? recipients.slice(0, 1)
        : recipients,
    [workflow, recipients],
  );
  const signerOptions = useMemo(() => {
    const options = [];
    if (workflow === "self_sign" || workflow === "self_sign_then_send") {
      options.push({ email: signerEmail.trim().toLowerCase(), label: signerName || signerEmail || "Owner" });
    }
    if (workflow !== "self_sign") {
      visibleRecipients.forEach((recipient) => {
        if (recipient.email.trim()) options.push({ email: recipient.email.trim().toLowerCase(), label: recipient.name || recipient.email });
      });
    }
    return options;
  }, [workflow, signerEmail, signerName, visibleRecipients]);

  useEffect(() => {
    const fallbackEmail = signerOptions[0]?.email || "";
    if (!fallbackEmail) return;
    setFields((current) =>
      current.map((field) =>
        String(field.assigned_to_email || "").trim()
          ? field
          : { ...field, assigned_to_email: fallbackEmail },
      ),
    );
  }, [signerOptions]);

  useEffect(() => {
    if (needsRecipients && !sendEmails) setSendEmails(true);
  }, [needsRecipients, sendEmails]);

  function selectWorkflow(value) {
    setWorkflow(value);
    const fallbackEmail =
      value === "self_sign" || value === "self_sign_then_send"
        ? signerEmail.trim().toLowerCase()
        : String(recipients[0]?.email || "").trim().toLowerCase();
    setFields((current) =>
      current.map((field) => ({
        ...field,
        assigned_to_email: fallbackEmail,
      })),
    );
    if (value === "send_to_single_recipient") {
      setRecipients((current) =>
        current.length ? current : [emptyRecipient(1)],
      );
    }
    if (value === "send_to_multiple_recipients") {
      setRecipients((current) => {
        const next = [...current];
        while (next.length < 2) next.push(emptyRecipient(next.length + 1));
        return next;
      });
    }
  }

  function validate() {
    if (!file) return t.noFile;
    if (!isPdf(file)) return t.invalidFile;
    if (fileSizeMb(file) > MAX_PDF_SIZE_MB) return t.tooLarge;
    if (needsOwnerSignature || workflow === "self_sign") {
      if (!signerName.trim()) return t.badName;
      if (!isEmail(signerEmail)) return t.badEmail;
    }
    if (needsRecipients) {
      if (
        workflow === "send_to_single_recipient" &&
        visibleRecipients.length !== 1
      ) {
        return t.singleRecipientRequired;
      }
      if (
        workflow === "send_to_multiple_recipients" &&
        visibleRecipients.length < 2
      ) {
        return t.multipleRecipientsRequired;
      }
      for (const recipient of visibleRecipients) {
        if (!recipient.name.trim() || !isEmail(recipient.email))
          return t.badRecipient;
      }
    }
    const signerEmails = signerOptions.map((option) => option.email);
    if (new Set(signerEmails).size !== signerEmails.length) {
      return t.duplicateSignerEmail;
    }
    const validSignerEmails = new Set(signerOptions.map((option) => option.email).filter(Boolean));
    for (const field of fields) {
      const assignedEmail = String(field.assigned_to_email || "").trim().toLowerCase();
      if (!isEmail(assignedEmail) || !validSignerEmails.has(assignedEmail)) return t.badEmail;
      if (!normalizeRectangle(field.rectangle)) return t.badRectangle;
      if (
        needsOwnerSignature &&
        assignedEmail === signerEmail.trim().toLowerCase() &&
        field.field_type === "text" &&
        field.required &&
        !String(field.default_value || "").trim()
      ) {
        return t.ownerTextRequired;
      }
    }
    const signableAssignees = new Set(
      fields
        .filter((field) => ["signature", "initials"].includes(field.field_type))
        .map((field) => String(field.assigned_to_email || "").trim().toLowerCase()),
    );
    for (const option of signerOptions) {
      if (!signableAssignees.has(option.email)) {
        return t.signerFieldRequired.replace("{signer}", option.label);
      }
    }
    if (needsOwnerSignature) {
      const signatureRectangle = normalizeRectangle(fields.find((item) => item.assigned_to_email?.toLowerCase() === signerEmail.toLowerCase())?.rectangle || DEFAULT_RECTANGLE);
      if (!buildSignatureOperation({ ...signature, rectangle: signatureRectangle })) return t.badSignature;
    }
    return "";
  }

  async function handlePickedPdfFile(file) {
    if (!file) {
      setFile(null);
      return;
    }

    const securityError = await validateBrowserUpload(
      file,
      FILE_SECURITY_POLICY.pdfTool,
    );
    if (securityError) {
      setFile(null);
      setError(securityError);
      return;
    }

    setError("");
    setFile(file);
  }
  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResponse(null);

    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }

    const ownerEmail = signerEmail.trim().toLowerCase();
    const ownerFieldRect = normalizeRectangle(fields.find((item) => item.assigned_to_email?.toLowerCase() === ownerEmail)?.rectangle || DEFAULT_RECTANGLE);
    const ownerSignature = needsOwnerSignature
      ? buildSignatureOperation({ ...signature, rectangle: ownerFieldRect })
      : null;

    const payload = {
      feature: "e_signature",
      action: workflow === "self_sign" ? "complete_signing" : "send",
      workflow,
      routing_mode: routingMode,
      self_signer: needsOwnerSignature
        ? {
            name: signerName.trim(),
            email: ownerEmail,
            signature: ownerSignature,
          }
        : null,
      recipients: needsRecipients
        ? visibleRecipients.map((recipient, index) => ({
            name: recipient.name.trim(),
            email: recipient.email.trim().toLowerCase(),
            role: "external_signer",
            signing_order: routingMode === "parallel" ? 1 : parseInteger(recipient.signing_order, index + 1),
            required: Boolean(recipient.required),
          }))
        : [],
      fields: fields.map((field, index) => {
        const assignedEmail = field.assigned_to_email.trim().toLowerCase();
        const fallbackEmail = signerOptions[0]?.email || ownerEmail;
        return {
        field_id: field.id,
        assigned_to_email: signerOptions.some((option) => option.email === assignedEmail) ? assignedEmail : fallbackEmail,
        field_type: field.field_type,
        page_number: parseInteger(field.page_number, 1),
        rectangle: normalizeRectangle(field.rectangle),
        required: Boolean(field.required),
        label: field.label || `${field.field_type} ${index + 1}`,
        default_value: field.default_value || undefined,
      };
      }),
      email_subject: emailSubject.trim() || undefined,
      email_message: emailMessage.trim() || undefined,
      expires_in_days: Math.max(1, Math.min(180, parseInteger(expiresInDays, 30))),
      generate_preview_after_each_signature: true,
    };

    const formData = new FormData();
    formData.append("file", file);
    formData.append("payload_json", JSON.stringify(payload));
    formData.append("send_emails", String(sendEmails));
    formData.append("system_language", systemLanguageFor(language));
    const signatureAsset =
      signature.signatureType === "drawn"
        ? signature.drawnImageFile
        : signature.signatureType === "uploaded_image"
          ? signature.uploadedImageFile
          : null;
    if (signatureAsset) {
      formData.append("signature_assets", signatureAsset);
    }

    setBusy(true);
    try {
      const data = await postAnalyzerFeature(FEATURE_PATH, formData, true);
      setResponse(data);
    } catch (caught) {
      setError(caught?.message || t.failed);
    } finally {
      setBusy(false);
    }
  }

  const result = response?.result || null;
  const signedPdfUrl = normalizeArtifactUrl(result?.signed_pdf?.download_url || result?.pdf_artifact?.download_url || result?.download_url);
  const certificateUrl = normalizeArtifactUrl(result?.audit_certificate?.download_url);
  const previewUrl = normalizeArtifactUrl(result?.latest_preview?.preview_pdf?.download_url || result?.preview?.download_url);

  if (!authChecked) return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen p-6 app-text">{t.loading}</main>
    </AppSidebarLayout>
  );
  if (!user) return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen p-6"><AuthRequired t={t} /></main>
    </AppSidebarLayout>
  );

  return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen px-4 py-6 app-text md:px-8">
        <button
          type="button"
          onClick={() => router.back()}
          className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted hover:app-text"
        >
          <ArrowLeft className="h-4 w-4" /> {t.back}
        </button>

        <section className="mb-8 rounded-3xl border app-surface-strong p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">
            {t.badge}
          </p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight app-text md:text-4xl">
            {t.title}
          </h1>
          <p className="mt-3 max-w-3xl app-text-muted">{t.description}</p>
        </section>

        <form
          onSubmit={handleSubmit}
          className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]"
        >
          <div className="space-y-6">
            <section className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">
                {t.uploadTitle}
              </h2>
              <p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p>
              <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center transition hover:bg-neutral-100 dark:hover:bg-[#2d2d33]">
                <UploadCloud className="h-10 w-10 app-text-muted" />
                <span className="mt-3 text-sm font-semibold app-text">
                  {file?.name || t.chooseFile}
                </span>
                <input
                  type="file"
                  accept="application/pdf,.pdf"
                  className="hidden"
                  onChange={(event) =>
                    handlePickedPdfFile(event.target.files?.[0] || null)
                  }
                />
              </label>
            </section>

            <section className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">{t.workflow}</h2>
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                {workflows.map(([value, labelKey]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => selectWorkflow(value)}
                    className={`rounded-2xl border px-4 py-3 text-left text-sm font-semibold ${workflow === value ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]" : "app-surface app-text"}`}
                  >
                    {t[labelKey]}
                  </button>
                ))}
              </div>
              {workflow !== "self_sign" ? (
                <label className="mt-4 block text-sm font-medium app-text">
                  {t.routingMode}
                  <select
                    value={routingMode}
                    onChange={(event) => setRoutingMode(event.target.value)}
                    className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                  >
                    <option value="sequential">{t.sequential}</option>
                    <option value="parallel">{t.parallel}</option>
                  </select>
                </label>
              ) : null}
            </section>

            {workflow === "self_sign" || workflow === "self_sign_then_send" ? (
              <section className="rounded-3xl border app-surface-strong p-5">
                <h2 className="text-lg font-semibold app-text">
                  {t.signerDetails}
                </h2>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <label className="text-sm font-medium app-text">
                    {t.signerName}
                    <input
                      value={signerName}
                      onChange={(event) => setSignerName(event.target.value)}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                    />
                  </label>
                  <label className="text-sm font-medium app-text">
                    {t.signerEmail}
                    <input
                      type="email"
                      value={signerEmail}
                      onChange={(event) => {
                        const previousEmail = signerEmail.trim().toLowerCase();
                        const nextEmail = event.target.value;
                        setSignerEmail(nextEmail);
                        setFields((current) =>
                          current.map((field) =>
                            String(field.assigned_to_email || "")
                              .trim()
                              .toLowerCase() === previousEmail
                              ? {
                                  ...field,
                                  assigned_to_email: nextEmail
                                    .trim()
                                    .toLowerCase(),
                                }
                              : field,
                          ),
                        );
                      }}
                      readOnly={Boolean(user?.email)}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                    />
                  </label>
                </div>
              </section>
            ) : null}

            {needsRecipients ? (
              <section className="rounded-3xl border app-surface-strong p-5">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-lg font-semibold app-text">
                    {t.recipients}
                  </h2>
                  {workflow !== "send_to_single_recipient" ? (
                    <button
                      type="button"
                      onClick={() =>
                        setRecipients((current) => [
                          ...current,
                          emptyRecipient(current.length + 1),
                        ])
                      }
                      className="inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text"
                    >
                      <Plus className="h-4 w-4" />
                      {t.addRecipient}
                    </button>
                  ) : null}
                </div>
                <div className="mt-4 space-y-3">
                  {visibleRecipients.map((recipient) => (
                    <div
                      key={recipient.id}
                      className="rounded-2xl border app-surface p-4"
                    >
                      <div className="grid gap-3 sm:grid-cols-[1fr_1fr_120px_auto]">
                        <input
                          placeholder={t.recipientName}
                          value={recipient.name}
                          onChange={(event) =>
                            setRecipients((current) =>
                              current.map((item) =>
                                item.id === recipient.id
                                  ? { ...item, name: event.target.value }
                                  : item,
                              ),
                            )
                          }
                          className="rounded-xl border app-surface px-3 py-2 app-text"
                        />
                        <input
                          placeholder={t.recipientEmail}
                          value={recipient.email}
                          onChange={(event) => {
                            const previousEmail = recipient.email
                              .trim()
                              .toLowerCase();
                            const nextEmail = event.target.value;
                            setRecipients((current) =>
                              current.map((item) =>
                                item.id === recipient.id
                                  ? { ...item, email: nextEmail }
                                  : item,
                              ),
                            );
                            setFields((current) =>
                              current.map((field) =>
                                String(field.assigned_to_email || "")
                                  .trim()
                                  .toLowerCase() === previousEmail
                                  ? {
                                      ...field,
                                      assigned_to_email: nextEmail
                                        .trim()
                                        .toLowerCase(),
                                    }
                                  : field,
                              ),
                            );
                          }}
                          className="rounded-xl border app-surface px-3 py-2 app-text"
                        />
                        <input
                          type="number"
                          min="1"
                          disabled={routingMode === "parallel"}
                          value={
                            routingMode === "parallel"
                              ? 1
                              : recipient.signing_order
                          }
                          onChange={(event) =>
                            setRecipients((current) =>
                              current.map((item) =>
                                item.id === recipient.id
                                  ? {
                                      ...item,
                                      signing_order: event.target.value,
                                    }
                                  : item,
                              ),
                            )
                          }
                          className="rounded-xl border app-surface px-3 py-2 app-text"
                        />
                        {workflow !== "send_to_single_recipient" ? (
                          <button
                            type="button"
                            onClick={() => {
                              const removedEmail = recipient.email
                                .trim()
                                .toLowerCase();
                              const fallbackEmail =
                                signerOptions.find(
                                  (option) => option.email !== removedEmail,
                                )?.email || "";
                              setRecipients((current) =>
                                current.filter(
                                  (item) => item.id !== recipient.id,
                                ),
                              );
                              setFields((current) =>
                                current.map((field) =>
                                  String(field.assigned_to_email || "")
                                    .trim()
                                    .toLowerCase() === removedEmail
                                    ? {
                                        ...field,
                                        assigned_to_email: fallbackEmail,
                                      }
                                    : field,
                                ),
                              );
                            }}
                            className="rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-red-200"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}

            {needsOwnerSignature ? (
              <SignatureSourceControls
                t={t}
                values={signature}
                setValues={setSignature}
              />
            ) : null}
          </div>

          <div className="space-y-6">
            <section className="rounded-3xl border app-surface-strong p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold app-text">{t.fields}</h2>
                <button
                  type="button"
                  onClick={() =>
                    setFields((current) => [
                      ...current,
                      emptyField(signerOptions[0]?.email || signerEmail),
                    ])
                  }
                  className="inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text"
                >
                  <Plus className="h-4 w-4" />
                  {t.addField}
                </button>
              </div>
              <div className="mt-4 space-y-4">
                {fields.map((field) => (
                  <div
                    key={field.id}
                    className="rounded-2xl border app-surface p-4"
                  >
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <FileSignature className="h-4 w-4 app-text-muted" />
                      <button
                        type="button"
                        onClick={() =>
                          setFields((current) =>
                            current.filter((item) => item.id !== field.id),
                          )
                        }
                        className="text-red-300"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <select
                        value={field.field_type}
                        onChange={(event) =>
                          setFields((current) =>
                            current.map((item) =>
                              item.id === field.id
                                ? { ...item, field_type: event.target.value }
                                : item,
                            ),
                          )
                        }
                        className="rounded-xl border app-surface px-3 py-2 app-text"
                      >
                        {fieldTypes.map((type) => (
                          <option key={type} value={type}>
                            {type}
                          </option>
                        ))}
                      </select>
                      <select
                        value={field.assigned_to_email}
                        onChange={(event) =>
                          setFields((current) =>
                            current.map((item) =>
                              item.id === field.id
                                ? {
                                    ...item,
                                    assigned_to_email: event.target.value,
                                  }
                                : item,
                            ),
                          )
                        }
                        className="rounded-xl border app-surface px-3 py-2 app-text"
                      >
                        {signerOptions.map((option) => (
                          <option key={option.email} value={option.email}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      <input
                        type="number"
                        min="1"
                        value={field.page_number}
                        onChange={(event) =>
                          setFields((current) =>
                            current.map((item) =>
                              item.id === field.id
                                ? { ...item, page_number: event.target.value }
                                : item,
                            ),
                          )
                        }
                        className="rounded-xl border app-surface px-3 py-2 app-text"
                      />
                      <input
                        value={field.label}
                        onChange={(event) =>
                          setFields((current) =>
                            current.map((item) =>
                              item.id === field.id
                                ? { ...item, label: event.target.value }
                                : item,
                            ),
                          )
                        }
                        className="rounded-xl border app-surface px-3 py-2 app-text"
                      />
                      {!["signature", "initials", "name", "email", "date_signed"].includes(
                        field.field_type,
                      ) ? (
                        <input
                          value={field.default_value || ""}
                          placeholder={t.fieldValue}
                          onChange={(event) =>
                            setFields((current) =>
                              current.map((item) =>
                                item.id === field.id
                                  ? {
                                      ...item,
                                      default_value: event.target.value,
                                    }
                                  : item,
                              ),
                            )
                          }
                          className="rounded-xl border app-surface px-3 py-2 app-text sm:col-span-2"
                        />
                      ) : null}
                    </div>
                    <div className="mt-3 grid grid-cols-4 gap-2">
                      {["x", "y", "width", "height"].map((key) => (
                        <input
                          key={key}
                          aria-label={t[key]}
                          value={field.rectangle[key]}
                          onChange={(event) =>
                            setFields((current) =>
                              current.map((item) =>
                                item.id === field.id
                                  ? {
                                      ...item,
                                      rectangle: {
                                        ...item.rectangle,
                                        [key]: event.target.value,
                                      },
                                    }
                                  : item,
                              ),
                            )
                          }
                          className="rounded-xl border app-surface px-3 py-2 app-text"
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {needsRecipients ? (
              <section className="rounded-3xl border app-surface-strong p-5">
                <h2 className="text-lg font-semibold app-text">
                  <Mail className="mr-2 inline h-4 w-4" />
                  Email
                </h2>
                <div className="mt-4 space-y-3">
                  <input
                    value={emailSubject}
                    onChange={(event) => setEmailSubject(event.target.value)}
                    placeholder={t.emailSubject}
                    className="w-full rounded-2xl border app-surface px-4 py-3 app-text"
                  />
                  <textarea
                    value={emailMessage}
                    onChange={(event) => setEmailMessage(event.target.value)}
                    placeholder={t.emailMessage}
                    rows={4}
                    className="w-full rounded-2xl border app-surface px-4 py-3 app-text"
                  />
                  <input
                    type="number"
                    min="1"
                    max="180"
                    value={expiresInDays}
                    onChange={(event) => setExpiresInDays(event.target.value)}
                    className="w-full rounded-2xl border app-surface px-4 py-3 app-text"
                  />
                  <label className="flex items-center gap-3 text-sm app-text">
                    <input
                      type="checkbox"
                      checked={sendEmails}
                      disabled
                    />
                    {t.sendEmails}
                  </label>
                </div>
              </section>
            ) : null}

            {error ? (
              <p className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">
                <AlertTriangle className="mr-2 inline h-4 w-4" />
                {error}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={busy}
              className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-60"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              {busy ? t.submitting : t.submit}
            </button>

            {result ? (
              <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5">
                <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
                  <CheckCircle2 className="h-5 w-5" />
                  {t.resultTitle}
                </h2>
                <dl className="mt-4 space-y-2 text-sm app-text-muted">
                  <div>
                    <dt className="font-semibold app-text">{t.envelopeId}</dt>
                    <dd>{result.envelope_id || "—"}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold app-text">{t.status}</dt>
                    <dd>{result.status || "—"}</dd>
                  </div>
                </dl>
                <div className="mt-4 flex flex-wrap gap-2">
                  {signedPdfUrl ? (
                    <a
                      href={signedPdfUrl}
                      className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"
                    >
                      <Download className="h-4 w-4" />
                      {t.downloadSigned}
                    </a>
                  ) : null}
                  {certificateUrl ? (
                    <a
                      href={certificateUrl}
                      className="rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text"
                    >
                      {t.downloadCertificate}
                    </a>
                  ) : null}
                  {previewUrl ? (
                    <a
                      href={previewUrl}
                      className="rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text"
                    >
                      {t.openPreview}
                    </a>
                  ) : null}
                </div>
              </section>
            ) : null}
          </div>
        </form>
      </main>
    </AppSidebarLayout>
  );
}
