"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  FilePlus2,
  FileSignature,
  Loader2,
  Mail,
  Move,
  Plus,
  Search,
  Send,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { postAnalyzerFeature } from "@/lib/api_client";
import { esignaturePageTranslations,
  getPageRuntimeCopy,
  resolveErrorMessage,
} from "@/lib/translations";
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

function fieldTypeLabel(type, t) {
  return {
    signature: t.fieldTypeSignature,
    initials: t.fieldTypeInitials,
    date_signed: t.fieldTypeDate,
    name: t.fieldTypeName,
    email: t.fieldTypeEmail,
    text: t.fieldTypeText,
    checkbox: t.fieldTypeCheckbox,
  }[type] || type;
}

function statusLabel(status, t) {
  return {
    completed: t.statusCompleted,
    sent: t.statusSent,
    partially_signed: t.statusPartial,
    draft: t.statusDraft,
  }[status] || status || "—";
}

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

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function rectangleToEditable(rectangle) {
  const normalized = normalizeRectangle(rectangle);
  if (!normalized) return { ...DEFAULT_RECTANGLE };
  return Object.fromEntries(
    Object.entries(normalized).map(([key, value]) => [key, String(Number(value.toFixed(5)))]),
  );
}

function rectanglesOverlap(left, right) {
  const a = normalizeRectangle(left);
  const b = normalizeRectangle(right);
  if (!a || !b) return false;
  return (
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y
  );
}

function fieldCollisionSummary(field, fields, layout) {
  const fieldPage = parseInteger(field.page_number, 1);
  const previewPage = parseInteger(layout?.preview_page_number, 1);
  const expectedVectorPlacement = new Set([
    "signature_line_suggestion",
    "signature_page",
    "native_form_field",
  ]).has(field.placement_source);
  const contentHits = fieldPage === previewPage
    ? (layout?.content_regions || []).filter(
        (region) =>
          !(region.kind === "vector" && expectedVectorPlacement) &&
          rectanglesOverlap(field.rectangle, region.rectangle),
      )
    : [];
  const fieldHits = fields.filter(
    (other) =>
      other.id !== field.id &&
      parseInteger(other.page_number, 1) === parseInteger(field.page_number, 1) &&
      rectanglesOverlap(field.rectangle, other.rectangle),
  );
  const serverCollision = (layout?.collisions || []).find(
    (item) => String(item.field_id || "") === String(field.id),
  );
  const text = contentHits
    .filter((region) => region.kind === "text" && region.text)
    .map((region) => region.text)
    .join(" ")
    .trim();
  return {
    blocking: Boolean(contentHits.length || fieldHits.length || serverCollision?.blocking),
    text: text || String(serverCollision?.overlapping_text || "").trim(),
    contentHits,
    fieldHits,
    serverCollision,
  };
}

function layoutFieldPayload(field) {
  return {
    field_id: field.id,
    field_type: field.field_type,
    page_number: parseInteger(field.page_number, 1),
    rectangle: normalizeRectangle(field.rectangle),
    native_widget_name: field.native_widget_name || undefined,
    placement_source: field.placement_source || "manual",
  };
}

function suggestionToField(suggestion, fallbackEmail) {
  return {
    id: `field_${Date.now()}_${Math.random().toString(16).slice(2)}`,
    field_type: suggestion.field_type || "signature",
    assigned_to_email: suggestion.assigned_to_email || fallbackEmail || "",
    page_number: String(suggestion.page_number || 1),
    rectangle: rectangleToEditable(suggestion.rectangle || DEFAULT_RECTANGLE),
    required: true,
    label: suggestion.label || "Signature",
    default_value: "",
    native_widget_name: suggestion.native_widget_name || "",
    placement_source:
      suggestion.source === "native_form_field"
        ? "native_form_field"
        : suggestion.source === "signature_page"
          ? "signature_page"
          : "signature_line_suggestion",
  };
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
    native_widget_name: "",
    placement_source: "manual",
  };
}

function buildSignatureOperation({
  signatureType,
  typedName,
  drawnImageFile,
  uploadedImageFile,
  rectangle,
  pageNumber = 1,
}) {
  const base = {
    operation: "add_signature",
    page_number: parseInteger(pageNumber, 1),
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
    context.clearRect(0, 0, canvas.width, canvas.height);
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
    context.clearRect(0, 0, canvas.width, canvas.height);
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

function PdfFieldDesigner({
  t,
  layout,
  fields,
  selectedFieldId,
  onSelectField,
  onChangeRectangle,
}) {
  const hostRef = useRef(null);
  const interactionRef = useRef(null);

  useEffect(() => {
    function move(event) {
      const interaction = interactionRef.current;
      const host = hostRef.current;
      if (!interaction || !host) return;
      const bounds = host.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return;
      const dx = (event.clientX - interaction.clientX) / bounds.width;
      const dy = (event.clientY - interaction.clientY) / bounds.height;
      const start = interaction.rectangle;
      let next;
      if (interaction.mode === "resize") {
        next = {
          x: start.x,
          y: start.y,
          width: clamp(start.width + dx, 0.035, 1 - start.x),
          height: clamp(start.height + dy, 0.025, 1 - start.y),
        };
      } else {
        next = {
          ...start,
          x: clamp(start.x + dx, 0, 1 - start.width),
          y: clamp(start.y + dy, 0, 1 - start.height),
        };
      }
      onChangeRectangle(interaction.fieldId, rectangleToEditable(next));
    }

    function end() {
      interactionRef.current = null;
    }

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };
  }, [onChangeRectangle]);

  if (!layout?.preview_data_url) {
    return (
      <div className="rounded-2xl border border-dashed app-surface p-8 text-center text-sm app-text-muted">
        {t.designerEmpty}
      </div>
    );
  }

  const pageNumber = parseInteger(layout.preview_page_number, 1);
  const pageFields = fields.filter(
    (field) => parseInteger(field.page_number, 1) === pageNumber,
  );

  function begin(event, field, mode) {
    const rectangle = normalizeRectangle(field.rectangle);
    if (!rectangle) return;
    event.preventDefault();
    event.stopPropagation();
    onSelectField(field.id);
    interactionRef.current = {
      fieldId: field.id,
      mode,
      rectangle,
      clientX: event.clientX,
      clientY: event.clientY,
    };
  }

  return (
    <div
      ref={hostRef}
      className="relative mx-auto w-full max-w-4xl overflow-hidden rounded-2xl border bg-white shadow-sm touch-none"
      style={{ aspectRatio: `${layout.pages?.[pageNumber - 1]?.width || 612} / ${layout.pages?.[pageNumber - 1]?.height || 792}` }}
    >
      <img
        src={layout.preview_data_url}
        alt={`${t.previewPage} ${pageNumber}`}
        className="absolute inset-0 h-full w-full select-none object-fill"
        draggable={false}
      />
      {pageFields.map((field) => {
        const rectangle = normalizeRectangle(field.rectangle);
        if (!rectangle) return null;
        const collision = fieldCollisionSummary(field, fields, layout);
        const selected = field.id === selectedFieldId;
        return (
          <div
            key={field.id}
            onPointerDown={(event) => begin(event, field, "move")}
            onClick={() => onSelectField(field.id)}
            className={`absolute cursor-move border-2 bg-black/5 text-[10px] font-semibold shadow-sm ${
              collision.blocking
                ? "border-red-500 bg-red-500/10"
                : selected
                  ? "border-sky-500 bg-sky-500/10"
                  : "border-emerald-500 bg-emerald-500/10"
            }`}
            style={{
              left: `${rectangle.x * 100}%`,
              top: `${rectangle.y * 100}%`,
              width: `${rectangle.width * 100}%`,
              height: `${rectangle.height * 100}%`,
            }}
            title={collision.text || field.label || field.field_type}
          >
            <span className="pointer-events-none absolute left-1 top-1 max-w-[calc(100%-12px)] truncate rounded bg-white/90 px-1 py-0.5 text-neutral-900">
              {field.label || field.field_type}
              {field.assigned_to_email ? ` · ${field.assigned_to_email}` : ""}
            </span>
            <button
              type="button"
              aria-label={t.resizeField}
              onPointerDown={(event) => begin(event, field, "resize")}
              className="absolute bottom-0 right-0 h-4 w-4 cursor-se-resize border-l border-t border-current bg-white/90"
            />
          </div>
        );
      })}
    </div>
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
  const [layout, setLayout] = useState(null);
  const [layoutBusy, setLayoutBusy] = useState(false);
  const [layoutError, setLayoutError] = useState("");
  const [activePage, setActivePage] = useState(1);
  const [selectedFieldId, setSelectedFieldId] = useState("");
  const [addSignaturePage, setAddSignaturePage] = useState(false);
  const [signatureSuggestions, setSignatureSuggestions] = useState([]);

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
  const showRoutingMode = needsRecipients && visibleRecipients.length > 1;
  const effectiveRoutingMode = showRoutingMode ? routingMode : "sequential";
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

  const layoutSigners = useMemo(
    () =>
      signerOptions.map((option) => ({
        name: option.label || option.email || "Signer",
        email: option.email || "",
      })),
    [signerOptions],
  );

  const updateFieldRectangle = useCallback((fieldId, rectangle) => {
    setFields((current) =>
      current.map((field) => {
        if (field.id !== fieldId) return field;
        const detachNativeWidget = Boolean(field.native_widget_name);
        const detachDetectedLine =
          field.placement_source === "signature_line_suggestion";
        return {
          ...field,
          rectangle,
          ...(detachNativeWidget ? { native_widget_name: "" } : {}),
          ...(detachNativeWidget || detachDetectedLine
            ? { placement_source: "manual" }
            : {}),
        };
      }),
    );
  }, []);

  const requestLayout = useCallback(
    async ({
      sourceFile = file,
      pageNumber = activePage,
      addPage = addSignaturePage,
      detectSignatureLines = false,
      fieldSnapshot = fields,
      signerSnapshot = layoutSigners,
    } = {}) => {
      if (!sourceFile) return null;
      setLayoutBusy(true);
      setLayoutError("");
      try {
        const formData = new FormData();
        formData.append("file", sourceFile);
        formData.append(
          "fields_json",
          JSON.stringify(fieldSnapshot.map(layoutFieldPayload)),
        );
        formData.append("signers_json", JSON.stringify(signerSnapshot));
        formData.append("page_number", String(pageNumber));
        formData.append("add_signature_page", String(Boolean(addPage)));
        formData.append(
          "detect_signature_lines",
          String(Boolean(detectSignatureLines)),
        );
        const nextLayout = await postAnalyzerFeature(
          `${FEATURE_PATH}/layout`,
          formData,
          true,
        );
        setLayout(nextLayout);
        setActivePage(parseInteger(nextLayout?.preview_page_number, pageNumber));
        setSignatureSuggestions((current) => {
          const incoming = nextLayout?.signature_suggestions || [];
          const base = detectSignatureLines
            ? incoming
            : [
                ...current.filter((item) => item.source !== "signature_page"),
                ...incoming.filter((item) => item.source === "signature_page"),
              ];
          const unique = new Map(
            base.map((item) => [item.suggestion_id || JSON.stringify(item), item]),
          );
          return [...unique.values()];
        });
        return nextLayout;
      } catch (caught) {
        setLayoutError(resolveErrorMessage(caught, language, "PREVIEW_UNAVAILABLE"));
        return null;
      } finally {
        setLayoutBusy(false);
      }
    },
    [file, activePage, addSignaturePage, fields, layoutSigners, t.layoutFailed],
  );

  async function goToDesignerPage(pageNumber) {
    const maxPage = layout?.effective_page_count || layout?.page_count || 1;
    const nextPage = clamp(parseInteger(pageNumber, 1), 1, maxPage);
    setActivePage(nextPage);
    await requestLayout({ pageNumber: nextPage });
  }

  function addSuggestedField(suggestion) {
    const fallbackEmail =
      suggestion.assigned_to_email || signerOptions[0]?.email || signerEmail;
    const next = suggestionToField(suggestion, fallbackEmail);
    const nextFields = [...fields, next];
    setFields(nextFields);
    setSelectedFieldId(next.id);
    void requestLayout({
      pageNumber: parseInteger(next.page_number, 1),
      fieldSnapshot: nextFields,
    });
  }

  function importNativeFields() {
    const existing = new Set(
      fields.map((field) => field.native_widget_name).filter(Boolean),
    );
    const fallbackEmail = signerOptions[0]?.email || signerEmail;
    const additions = (layout?.native_form_fields || [])
      .filter((field) => !existing.has(field.native_widget_name))
      .map((field) => suggestionToField(field, fallbackEmail));
    if (!additions.length) return;
    const nextFields = [...fields, ...additions];
    setFields(nextFields);
    setSelectedFieldId(additions[0].id);
    void requestLayout({
      pageNumber: parseInteger(additions[0].page_number, 1),
      fieldSnapshot: nextFields,
    });
  }

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
      if (
        needsOwnerSignature &&
        assignedEmail === signerEmail.trim().toLowerCase() &&
        field.field_type === "checkbox" &&
        field.required &&
        String(field.default_value || "").toLowerCase() !== "true"
      ) {
        return t.ownerCheckboxRequired;
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
      const ownerSignatureField = fields.find(
        (item) =>
          item.assigned_to_email?.toLowerCase() === signerEmail.toLowerCase() &&
          ["signature", "initials"].includes(item.field_type),
      );
      const signatureRectangle = normalizeRectangle(
        ownerSignatureField?.rectangle || DEFAULT_RECTANGLE,
      );
      if (
        !buildSignatureOperation({
          ...signature,
          rectangle: signatureRectangle,
          pageNumber: ownerSignatureField?.page_number || 1,
        })
      )
        return t.badSignature;
    }
    return "";
  }

  async function handlePickedPdfFile(file) {
    if (!file) {
      setFile(null);
      setLayout(null);
      setActivePage(1);
      setAddSignaturePage(false);
      setSignatureSuggestions([]);
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
    setLayout(null);
    setActivePage(1);
    setAddSignaturePage(false);
    setSignatureSuggestions([]);
    await requestLayout({
      sourceFile: file,
      pageNumber: 1,
      addPage: false,
      fieldSnapshot: fields,
    });
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

    const preflight = await requestLayout({
      pageNumber: activePage,
      fieldSnapshot: fields,
      addPage: addSignaturePage,
    });
    if (!preflight) {
      setError(t.layoutFailed);
      return;
    }
    const blockedPlacements = (preflight.collisions || []).filter(
      (item) => item.blocking,
    );
    if (blockedPlacements.length) {
      setError(t.collisionBlocking);
      return;
    }

    const ownerEmail = signerEmail.trim().toLowerCase();
    const ownerSignatureField = fields.find(
      (item) =>
        item.assigned_to_email?.toLowerCase() === ownerEmail &&
        ["signature", "initials"].includes(item.field_type),
    );
    const ownerFieldRect = normalizeRectangle(
      ownerSignatureField?.rectangle || DEFAULT_RECTANGLE,
    );
    const ownerSignature = needsOwnerSignature
      ? buildSignatureOperation({
          ...signature,
          rectangle: ownerFieldRect,
          pageNumber: ownerSignatureField?.page_number || 1,
        })
      : null;

    const payload = {
      feature: "e_signature",
      action: workflow === "self_sign" ? "complete_signing" : "send",
      workflow,
      routing_mode: effectiveRoutingMode,
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
            signing_order:
              effectiveRoutingMode === "parallel"
                ? 1
                : parseInteger(recipient.signing_order, index + 1),
            required: Boolean(recipient.required),
          }))
        : [],
      fields: fields.map((field, index) => {
        const assignedEmail = field.assigned_to_email.trim().toLowerCase();
        const fallbackEmail = signerOptions[0]?.email || ownerEmail;
        return {
          field_id: field.id,
          assigned_to_email: signerOptions.some(
            (option) => option.email === assignedEmail,
          )
            ? assignedEmail
            : fallbackEmail,
          field_type: field.field_type,
          page_number: parseInteger(field.page_number, 1),
          rectangle: normalizeRectangle(field.rectangle),
          required: Boolean(field.required),
          label: field.label || getPageRuntimeCopy("eSignature", language).fieldFallback
      .replace("{type}", field.field_type)
      .replace("{number}", String(index + 1)),
          default_value: field.default_value || undefined,
          native_widget_name: field.native_widget_name || undefined,
          placement_source: field.placement_source || "manual",
        };
      }),
      email_subject: emailSubject.trim() || undefined,
      email_message: emailMessage.trim() || undefined,
      expires_in_days: Math.max(1, Math.min(180, parseInteger(expiresInDays, 30))),
      add_signature_page: addSignaturePage,
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
      setError(resolveErrorMessage(caught, language, "PROCESSING_FAILED"));
    } finally {
      setBusy(false);
    }
  }

  const result = response?.result || null;
  const signedPdfUrl = normalizeArtifactUrl(result?.signed_pdf?.download_url || result?.pdf_artifact?.download_url || result?.download_url);
  const certificateUrl = normalizeArtifactUrl(result?.audit_certificate?.download_url);
  const previewUrl = normalizeArtifactUrl(result?.latest_preview?.preview_pdf?.download_url || result?.preview?.download_url);

  const selectedField =
    fields.find((field) => field.id === selectedFieldId) || null;
  const selectedCollision = selectedField
    ? fieldCollisionSummary(selectedField, fields, layout)
    : null;
  const activeSuggestions = signatureSuggestions.filter(
    (suggestion) =>
      parseInteger(suggestion.page_number, 1) === parseInteger(activePage, 1),
  );
  const availableNativeFields = (layout?.native_form_fields || []).filter(
    (nativeField) =>
      !fields.some(
        (field) =>
          field.native_widget_name &&
          field.native_widget_name === nativeField.native_widget_name,
      ),
  );
  const blockingCollisionCount = (layout?.collisions || []).filter(
    (item) => item.blocking,
  ).length;

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
              {showRoutingMode ? (
                <div className="mt-4 rounded-2xl border app-surface p-4">
                  <label className="block text-sm font-medium app-text">
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
                  <p className="mt-2 text-xs app-text-muted">
                    {routingMode === "parallel"
                      ? t.routingHelpParallel
                      : t.routingHelpSequential}
                  </p>
                </div>
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
                  {visibleRecipients.map((recipient, index) => (
                    <div
                      key={recipient.id}
                      className="rounded-2xl border app-surface p-4"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <p className="text-sm font-semibold app-text">
                          {workflow === "send_to_single_recipient"
                            ? t.recipients
                            : `${t.recipients} ${index + 1}`}
                        </p>
                        {workflow !== "send_to_single_recipient" ? (
                          <button
                            type="button"
                            aria-label={t.back}
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
                            className="rounded-xl border border-red-400/30 bg-red-400/10 p-2 text-red-200"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        ) : null}
                      </div>

                      <div className="mt-3 grid gap-3 sm:grid-cols-2">
                        <label className="text-sm font-medium app-text">
                          {t.recipientName}
                          <input
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
                            className="mt-2 w-full rounded-xl border app-surface px-3 py-2 app-text"
                          />
                        </label>
                        <label className="text-sm font-medium app-text">
                          {t.recipientEmail}
                          <input
                            type="email"
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
                            className="mt-2 w-full rounded-xl border app-surface px-3 py-2 app-text"
                          />
                        </label>
                        {showRoutingMode && routingMode === "sequential" ? (
                          <label className="text-sm font-medium app-text sm:col-span-2">
                            {t.signingOrder}
                            <input
                              type="number"
                              min="1"
                              value={recipient.signing_order}
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
                              className="mt-2 w-28 rounded-xl border app-surface px-3 py-2 app-text"
                            />
                          </label>
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
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold app-text">
                    {t.visualDesigner}
                  </h2>
                  <p className="mt-1 text-sm app-text-muted">
                    {t.visualDesignerHelp}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    disabled={!file || layoutBusy || activePage <= 1}
                    onClick={() => void goToDesignerPage(activePage - 1)}
                    className="rounded-xl border app-surface p-2 app-text disabled:opacity-40"
                    aria-label={t.previousPage}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <span className="min-w-24 text-center text-xs font-semibold app-text-muted">
                    {t.pageOf
                      .replace("{page}", String(activePage))
                      .replace(
                        "{count}",
                        String(layout?.effective_page_count || layout?.page_count || 1),
                      )}
                  </span>
                  <button
                    type="button"
                    disabled={
                      !file ||
                      layoutBusy ||
                      activePage >=
                        (layout?.effective_page_count || layout?.page_count || 1)
                    }
                    onClick={() => void goToDesignerPage(activePage + 1)}
                    className="rounded-xl border app-surface p-2 app-text disabled:opacity-40"
                    aria-label={t.nextPage}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={!file || layoutBusy}
                  onClick={() => void requestLayout({ pageNumber: activePage })}
                  className="inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-2 text-xs font-semibold app-text disabled:opacity-40"
                >
                  {layoutBusy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Move className="h-4 w-4" />
                  )}
                  {t.refreshPreview}
                </button>
                <button
                  type="button"
                  disabled={!file || layoutBusy}
                  onClick={() =>
                    void requestLayout({
                      pageNumber: activePage,
                      detectSignatureLines: true,
                    })
                  }
                  className="inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-2 text-xs font-semibold app-text disabled:opacity-40"
                >
                  <Search className="h-4 w-4" />
                  {t.findSignatureLines}
                </button>
                {availableNativeFields.length ? (
                  <button
                    type="button"
                    onClick={importNativeFields}
                    className="inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-2 text-xs font-semibold app-text"
                  >
                    <FileSignature className="h-4 w-4" />
                    {t.importNativeFields.replace(
                      "{count}",
                      String(availableNativeFields.length),
                    )}
                  </button>
                ) : null}
              </div>

              <label className="mt-4 flex items-start gap-3 rounded-2xl border app-surface p-3 text-sm app-text">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={addSignaturePage}
                  disabled={!file || layoutBusy}
                  onChange={(event) => {
                    const next = event.target.checked;
                    setAddSignaturePage(next);
                    const originalCount = layout?.page_count || 1;
                    const nextFields = next
                      ? fields
                      : fields.filter(
                          (field) =>
                            parseInteger(field.page_number, 1) <= originalCount,
                        );
                    if (!next) {
                      setFields(nextFields);
                      if (
                        selectedFieldId &&
                        !nextFields.some((field) => field.id === selectedFieldId)
                      ) {
                        setSelectedFieldId("");
                      }
                    }
                    const nextPage = next
                      ? originalCount + 1
                      : Math.min(activePage, originalCount);
                    void requestLayout({
                      pageNumber: nextPage,
                      addPage: next,
                      fieldSnapshot: nextFields,
                    });
                  }}
                />
                <span>
                  <span className="flex items-center gap-2 font-semibold">
                    <FilePlus2 className="h-4 w-4" />
                    {t.addSignaturePage}
                  </span>
                  <span className="mt-1 block text-xs app-text-muted">
                    {t.addSignaturePageHelp}
                  </span>
                </span>
              </label>

              <div className="mt-4">
                <PdfFieldDesigner
                  t={t}
                  layout={layout}
                  fields={fields}
                  selectedFieldId={selectedFieldId}
                  onSelectField={setSelectedFieldId}
                  onChangeRectangle={updateFieldRectangle}
                />
              </div>

              {layoutError ? (
                <p className="mt-3 rounded-xl border border-red-400/30 bg-red-400/10 p-3 text-xs text-red-200">
                  {layoutError}
                </p>
              ) : null}

              {blockingCollisionCount ? (
                <p className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-100">
                  <AlertTriangle className="mr-1 inline h-4 w-4" />
                  {t.collisionSummary.replace(
                    "{count}",
                    String(blockingCollisionCount),
                  )}
                </p>
              ) : null}

              {selectedCollision?.blocking ? (
                <div className="mt-3 rounded-xl border border-red-400/30 bg-red-400/10 p-3 text-xs text-red-100">
                  <p className="font-semibold">{t.selectedAreaCollision}</p>
                  {selectedCollision.text ? (
                    <p className="mt-1">
                      {t.textUnderSelection}: “{selectedCollision.text}”
                    </p>
                  ) : null}
                  {selectedCollision.fieldHits.length ? (
                    <p className="mt-1">{t.fieldOverlap}</p>
                  ) : null}
                </div>
              ) : selectedField ? (
                <p className="mt-3 rounded-xl border border-emerald-400/30 bg-emerald-400/10 p-3 text-xs text-emerald-100">
                  {t.selectedAreaClear}
                </p>
              ) : null}

              {activeSuggestions.length ? (
                <div className="mt-4 space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-wide app-text-muted">
                    {t.signatureSuggestions}
                  </p>
                  {activeSuggestions.map((suggestion) => (
                    <div
                      key={suggestion.suggestion_id}
                      className="flex items-center justify-between gap-3 rounded-xl border app-surface p-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold app-text">
                          {suggestion.label || t.signatureSuggestion}
                        </p>
                        <p className="text-xs app-text-muted">
                          {suggestion.reason || t.senderConfirmationRequired}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => addSuggestedField(suggestion)}
                        className="shrink-0 rounded-lg bg-[var(--app-button-bg)] px-3 py-2 text-xs font-semibold text-[var(--app-button-text)]"
                      >
                        {t.addSuggestion}
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </section>

            <section className="rounded-3xl border app-surface-strong p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold app-text">{t.fields}</h2>
                <button
                  type="button"
                  onClick={() => {
                    const next = emptyField(
                      signerOptions[0]?.email || signerEmail,
                    );
                    next.page_number = String(activePage);
                    setFields((current) => [...current, next]);
                    setSelectedFieldId(next.id);
                  }}
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
                    onClick={() => {
                      setSelectedFieldId(field.id);
                      const fieldPage = parseInteger(field.page_number, 1);
                      if (fieldPage !== activePage) {
                        void goToDesignerPage(fieldPage);
                      }
                    }}
                    className={`rounded-2xl border p-4 ${
                      field.id === selectedFieldId ? "app-surface-strong" : "app-surface"
                    }`}
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
                      <label className="text-sm font-medium app-text">
                        {t.fieldType}
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
                          className="mt-2 w-full rounded-xl border app-surface px-3 py-2 app-text"
                        >
                          {fieldTypes.map((type) => (
                            <option key={type} value={type}>
                              {fieldTypeLabel(type, t)}
                            </option>
                          ))}
                        </select>
                      </label>

                      <label className="text-sm font-medium app-text">
                        {t.assignedTo}
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
                          className="mt-2 w-full rounded-xl border app-surface px-3 py-2 app-text"
                        >
                          {signerOptions.map((option) => (
                            <option key={option.email} value={option.email}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>

                      <label className="text-sm font-medium app-text sm:col-span-2">
                        {t.fieldLabel}
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
                          className="mt-2 w-full rounded-xl border app-surface px-3 py-2 app-text"
                        />
                      </label>

                      {field.field_type === "checkbox" ? (
                        <label className="flex items-center gap-3 rounded-xl border app-surface px-3 py-2 text-sm app-text sm:col-span-2">
                          <input
                            type="checkbox"
                            checked={
                              String(field.default_value || "").toLowerCase() ===
                              "true"
                            }
                            onChange={(event) =>
                              setFields((current) =>
                                current.map((item) =>
                                  item.id === field.id
                                    ? {
                                        ...item,
                                        default_value: String(
                                          event.target.checked,
                                        ),
                                      }
                                    : item,
                                ),
                              )
                            }
                          />
                          {t.checkedByDefault}
                        </label>
                      ) : field.field_type === "text" ? (
                        <label className="text-sm font-medium app-text sm:col-span-2">
                          {t.fieldValue}
                          <input
                            value={field.default_value || ""}
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
                            className="mt-2 w-full rounded-xl border app-surface px-3 py-2 app-text"
                          />
                        </label>
                      ) : null}
                    </div>

                    <details
                      className="mt-3 rounded-xl border app-surface p-3"
                      onClick={(event) => event.stopPropagation()}
                    >
                      <summary className="cursor-pointer text-xs font-semibold app-text-muted">
                        {t.advancedPlacement}
                      </summary>
                      <div className="mt-3 space-y-3">
                        <label className="block text-xs font-medium app-text">
                          {t.pageNumber}
                          <input
                            type="number"
                            min="1"
                            max={layout?.effective_page_count || undefined}
                            value={field.page_number}
                            onChange={(event) =>
                              setFields((current) =>
                                current.map((item) =>
                                  item.id === field.id
                                    ? {
                                        ...item,
                                        page_number: event.target.value,
                                      }
                                    : item,
                                ),
                              )
                            }
                            className="mt-2 w-full rounded-xl border app-surface px-3 py-2 app-text"
                          />
                        </label>
                        <p className="text-xs font-medium app-text-muted">
                          {t.rectangle}
                        </p>
                        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                          {["x", "y", "width", "height"].map((key) => (
                            <label key={key} className="text-xs app-text-muted">
                              {t[key]}
                              <input
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
                                className="mt-1 w-full rounded-xl border app-surface px-3 py-2 app-text"
                              />
                            </label>
                          ))}
                        </div>
                      </div>
                    </details>
                  </div>
                ))}
              </div>
            </section>

            {needsRecipients ? (
              <details className="rounded-3xl border app-surface-strong p-5">
                <summary className="cursor-pointer list-none">
                  <span className="flex items-center gap-2 text-lg font-semibold app-text">
                    <Mail className="h-4 w-4" />
                    {t.emailOptions}
                  </span>
                  <span className="mt-1 block text-sm app-text-muted">
                    {t.emailOptionsHelp}
                  </span>
                </summary>
                <div className="mt-4 space-y-4">
                  <p className="rounded-2xl border app-surface p-3 text-sm app-text-muted">
                    {t.sendEmails}
                  </p>
                  <label className="block text-sm font-medium app-text">
                    {t.emailSubject}
                    <input
                      value={emailSubject}
                      onChange={(event) => setEmailSubject(event.target.value)}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                    />
                  </label>
                  <label className="block text-sm font-medium app-text">
                    {t.emailMessage}
                    <textarea
                      value={emailMessage}
                      onChange={(event) => setEmailMessage(event.target.value)}
                      rows={4}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                    />
                  </label>
                  <label className="block text-sm font-medium app-text">
                    {t.expiresInDays}
                    <input
                      type="number"
                      min="1"
                      max="180"
                      value={expiresInDays}
                      onChange={(event) => setExpiresInDays(event.target.value)}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                    />
                  </label>
                </div>
              </details>
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
              {busy
                ? t.submitting
                : needsRecipients
                  ? t.sendForSignature
                  : t.signDocument}
            </button>

            {result ? (
              <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5">
                <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
                  <CheckCircle2 className="h-5 w-5" />
                  {t.resultTitle}
                </h2>
                <p className="mt-3 text-sm app-text-muted">
                  {result.status === "completed" ? t.successSigned : t.successSent}
                </p>
                <dl className="mt-4 grid gap-3 text-sm app-text-muted sm:grid-cols-2">
                  <div className="rounded-2xl border app-surface p-3">
                    <dt className="font-semibold app-text">{t.status}</dt>
                    <dd>{statusLabel(result.status, t)}</dd>
                  </div>
                  <div className="rounded-2xl border app-surface p-3">
                    <dt className="font-semibold app-text">{t.envelopeId}</dt>
                    <dd className="break-all">{result.envelope_id || "—"}</dd>
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
