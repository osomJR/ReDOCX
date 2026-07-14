"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  Eraser,
  FilePenLine,
  Highlighter,
  Image as ImageIcon,
  Loader2,
  PenLine,
  Plus,
  Trash2,
  Type,
  UploadCloud,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { postAnalyzerFeature } from "@/lib/api_client";
import { editPdfPageTranslations } from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import {
  FILE_SECURITY_POLICY,
  getFileExtension,
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const FEATURE_PATH = "pdf/edit";
const DEFAULT_RECTANGLE = { x: "10", y: "10", width: "80", height: "12" };
const POSITION_PRESETS = {
  top: { x: "10", y: "8", width: "80", height: "12" },
  center: { x: "10", y: "44", width: "80", height: "12" },
  bottom: { x: "10", y: "80", width: "80", height: "12" },
};

const copy = editPdfPageTranslations;
const operationOptions = [
  ["add_text", "addText"],
  ["remove_text", "removeText"],
  ["add_image", "addImage"],
  ["remove_image", "removeImage"],
  ["draw", "draw"],
  ["highlight", "highlight"],
  ["whiteout", "whiteout"],
  ["add_signature", "addSignature"],
  ["remove_signature", "removeSignature"],
];

function systemLanguageFor(language) {
  return language === "fr" ? "french" : "english";
}
function parseFloatSafe(value, fallback) {
  const parsed = Number.parseFloat(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : fallback;
}
function normalizePdfFilename(value, fallback) {
  const raw = String(value || "").trim() || fallback;
  return raw.toLowerCase().endsWith(".pdf") ? raw : `${raw}.pdf`;
}
function normalizeArtifactUrl(url) {
  if (!url) return "";
  const raw = String(url);
  if (/^https?:\/\//i.test(raw)) return raw;
  return raw.replace(
    /^\/api\/v1\/analyzer\/artifacts\//,
    "/api/analyzer/artifacts/",
  );
}
function uid(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}
function assetFilename(operationId, file) {
  return `${operationId}${getFileExtension(file?.name) || ".png"}`;
}

function normalizeRectangle(rectangle) {
  const x = Number.parseFloat(String(rectangle.x ?? ""));
  const y = Number.parseFloat(String(rectangle.y ?? ""));
  const width = Number.parseFloat(String(rectangle.width ?? ""));
  const height = Number.parseFloat(String(rectangle.height ?? ""));
  if (![x, y, width, height].every(Number.isFinite)) return null;
  if (
    x < 0 ||
    y < 0 ||
    width <= 0 ||
    height <= 0 ||
    x > 100 ||
    y > 100 ||
    x + width > 100 ||
    y + height > 100
  )
    return null;
  return { x: x / 100, y: y / 100, width: width / 100, height: height / 100 };
}

function strokesToSvgPath(strokes) {
  return strokes
    .filter((stroke) => stroke.length >= 2)
    .map((stroke) =>
      stroke
        .map(
          (point, index) =>
            `${index ? "L" : "M"} ${point.x.toFixed(5)} ${point.y.toFixed(5)}`,
        )
        .join(" "),
    )
    .join(" ");
}

function strokesToPngFile(strokes, filename) {
  return new Promise((resolve, reject) => {
    const canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 360;
    const context = canvas.getContext("2d");
    if (!context)
      return reject(new Error("Could not prepare the signature image."));
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = "#111111";
    context.lineWidth = 7;
    context.lineCap = "round";
    context.lineJoin = "round";
    for (const stroke of strokes) {
      if (stroke.length < 2) continue;
      context.beginPath();
      context.moveTo(stroke[0].x * canvas.width, stroke[0].y * canvas.height);
      for (const point of stroke.slice(1))
        context.lineTo(point.x * canvas.width, point.y * canvas.height);
      context.stroke();
    }
    canvas.toBlob((blob) => {
      if (!blob)
        return reject(new Error("Could not prepare the signature image."));
      resolve(new File([blob], filename, { type: "image/png" }));
    }, "image/png");
  });
}

function CanvasPad({
  label,
  help,
  clearLabel,
  color = "#111111",
  strokes,
  onChange,
}) {
  const canvasRef = useRef(null);
  const activeStrokes = useRef(strokes);
  const drawing = useRef(false);

  function redraw(nextStrokes = activeStrokes.current) {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = color;
    context.lineWidth = 5;
    context.lineCap = "round";
    context.lineJoin = "round";
    for (const stroke of nextStrokes) {
      if (stroke.length < 2) continue;
      context.beginPath();
      context.moveTo(stroke[0].x * canvas.width, stroke[0].y * canvas.height);
      for (const point of stroke.slice(1))
        context.lineTo(point.x * canvas.width, point.y * canvas.height);
      context.stroke();
    }
  }

  useEffect(() => {
    activeStrokes.current = strokes;
    redraw(strokes);
  }, [strokes, color]);

  function pointFor(event) {
    const canvas = canvasRef.current;
    const bounds = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
    };
  }

  function start(event) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drawing.current = true;
    activeStrokes.current = [...activeStrokes.current, [pointFor(event)]];
  }

  function move(event) {
    if (!drawing.current) return;
    event.preventDefault();
    activeStrokes.current[activeStrokes.current.length - 1].push(
      pointFor(event),
    );
    redraw();
  }

  function finish(event) {
    if (!drawing.current) return;
    drawing.current = false;
    if (event?.currentTarget?.hasPointerCapture?.(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    onChange(activeStrokes.current.filter((stroke) => stroke.length >= 2));
  }

  function clear() {
    activeStrokes.current = [];
    redraw([]);
    onChange([]);
  }

  return (
    <fieldset className="rounded-2xl border app-surface p-4">
      <legend className="px-2 text-sm font-semibold app-text">{label}</legend>
      <p className="mb-3 text-xs app-text-muted">{help}</p>
      <canvas
        ref={canvasRef}
        width="900"
        height="240"
        className="h-44 w-full touch-none rounded-xl border bg-white cursor-crosshair"
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={finish}
        onPointerCancel={finish}
        aria-label={label}
      />
      <button
        type="button"
        onClick={clear}
        className="mt-3 rounded-xl border app-surface px-3 py-2 text-xs font-semibold app-text"
      >
        {clearLabel}
      </button>
    </fieldset>
  );
}

function OperationIcon({ operation }) {
  if (operation === "add_text") return <Type className="h-4 w-4" />;
  if (operation === "highlight") return <Highlighter className="h-4 w-4" />;
  if (operation === "draw") return <PenLine className="h-4 w-4" />;
  if (operation === "add_image") return <ImageIcon className="h-4 w-4" />;
  if (operation === "add_signature") return <FilePenLine className="h-4 w-4" />;
  return <Eraser className="h-4 w-4" />;
}

function AuthRequired({ t }) {
  return (
    <section className="rounded-3xl border border-amber-400/30 bg-amber-400/10 p-6">
      <h2 className="text-lg font-semibold app-text">{t.signInTitle}</h2>
      <p className="mt-2 text-sm app-text-muted">{t.signInDescription}</p>
      <a
        href="/auth/login?returnTo=/pdf-tools/edit"
        className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]"
      >
        {t.signIn}
      </a>
    </section>
  );
}

async function buildOperation({
  operationType,
  pageNumber,
  rectangle,
  textValue,
  fontSize,
  colorHex,
  opacity,
  drawStrokes,
  strokeWidth,
  imageFile,
  signatureType,
  typedName,
  signatureStrokes,
  signatureImageFile,
  consentAccepted,
}) {
  const rect = normalizeRectangle(rectangle);
  if (!rect) return { error: "badRectangle" };
  const parsedPageNumber = Number.parseInt(String(pageNumber ?? ""), 10);
  if (!Number.isFinite(parsedPageNumber) || parsedPageNumber < 1)
    return { error: "badPage" };
  const operationId = uid("op");
  const base = {
    operation_id: operationId,
    operation: operationType,
    page_number: parsedPageNumber,
    rectangle: rect,
  };

  if (operationType === "add_text") {
    if (!textValue.trim()) return { error: "needsText" };
    const parsedFontSize = parseFloatSafe(fontSize, 12);
    if (parsedFontSize < 4 || parsedFontSize > 96)
      return { error: "badFontSize" };
    return {
      value: {
        ...base,
        text: textValue.trim(),
        font_size: parsedFontSize,
        font_family: "Helvetica",
        color_hex: colorHex || "#111111",
      },
    };
  }
  if (
    ["remove_text", "remove_image", "whiteout", "remove_signature"].includes(
      operationType,
    )
  )
    return { value: base };
  if (operationType === "highlight")
    return {
      value: {
        ...base,
        color_hex: colorHex || "#FFF176",
        opacity: Math.max(0.05, Math.min(1, parseFloatSafe(opacity, 35) / 100)),
      },
    };
  if (operationType === "draw") {
    const path = strokesToSvgPath(drawStrokes);
    if (!path) return { error: "needsDrawing" };
    const parsedStrokeWidth = parseFloatSafe(strokeWidth, 2);
    if (parsedStrokeWidth < 0.25 || parsedStrokeWidth > 25)
      return { error: "badStrokeWidth" };
    return {
      value: {
        ...base,
        path_svg: path,
        stroke_width: parsedStrokeWidth,
        stroke_color_hex: colorHex || "#111111",
      },
    };
  }
  if (operationType === "add_image") {
    if (!imageFile) return { error: "needsImage" };
    return {
      value: {
        ...base,
        image_storage_key: `asset:${operationId}`,
        image_mime_type: imageFile.type || "image/png",
        _assetFile: imageFile,
      },
    };
  }
  if (operationType === "add_signature") {
    if (!consentAccepted) return { error: "needsConsent" };
    if (signatureType === "typed") {
      if (!typedName.trim()) return { error: "needsSignature" };
      return {
        value: {
          ...base,
          signature_type: "typed",
          typed_name: typedName.trim(),
          consent_accepted: true,
        },
      };
    }
    if (signatureType === "drawn") {
      if (!strokesToSvgPath(signatureStrokes))
        return { error: "needsSignature" };
      const asset = await strokesToPngFile(
        signatureStrokes,
        `${operationId}.png`,
      );
      return {
        value: {
          ...base,
          signature_type: "drawn",
          signature_svg_storage_key: `asset:${operationId}`,
          consent_accepted: true,
          _assetFile: asset,
        },
      };
    }
    if (!signatureImageFile) return { error: "needsSignature" };
    return {
      value: {
        ...base,
        signature_type: "uploaded_image",
        signature_image_storage_key: `asset:${operationId}`,
        consent_accepted: true,
        _assetFile: signatureImageFile,
      },
    };
  }
  return { error: "failed" };
}

export default function EditPdfPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked } = useAccount();
  const t = useMemo(() => copy[language] || copy.en, [language]);
  const fileInputRef = useRef(null);

  const [file, setFile] = useState(null);
  const [operationType, setOperationType] = useState("add_text");
  const [pageNumber, setPageNumber] = useState("1");
  const [positionPreset, setPositionPreset] = useState("top");
  const [rectangle, setRectangle] = useState({ ...DEFAULT_RECTANGLE });
  const [textValue, setTextValue] = useState("");
  const [fontSize, setFontSize] = useState("12");
  const [colorHex, setColorHex] = useState("#111111");
  const [opacity, setOpacity] = useState("35");
  const [drawStrokes, setDrawStrokes] = useState([]);
  const [strokeWidth, setStrokeWidth] = useState("2");
  const [imageFile, setImageFile] = useState(null);
  const [signatureType, setSignatureType] = useState("typed");
  const [typedName, setTypedName] = useState("");
  const [signatureStrokes, setSignatureStrokes] = useState([]);
  const [signatureImageFile, setSignatureImageFile] = useState(null);
  const [consentAccepted, setConsentAccepted] = useState(false);
  const [operations, setOperations] = useState([]);
  const [outputFilename, setOutputFilename] = useState("edited-document.pdf");
  const [generatePreview, setGeneratePreview] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);

  function updatePreset(value) {
    setPositionPreset(value);
    if (POSITION_PRESETS[value]) setRectangle({ ...POSITION_PRESETS[value] });
  }

  async function pickEditImage(nextFile, setter) {
    if (!nextFile) return setter(null);
    const securityError = await validateBrowserUpload(
      nextFile,
      FILE_SECURITY_POLICY.pdfEditImage,
    );
    if (securityError) {
      setter(null);
      setError(securityError);
      return;
    }
    setter(nextFile);
    setError("");
  }

  async function addOperation() {
    try {
      const result = await buildOperation({
        operationType,
        pageNumber,
        rectangle,
        textValue,
        fontSize,
        colorHex,
        opacity,
        drawStrokes,
        strokeWidth,
        imageFile,
        signatureType,
        typedName,
        signatureStrokes,
        signatureImageFile,
        consentAccepted,
      });
      if (result.error) {
        setError(t[result.error] || t.failed);
        return;
      }
      setOperations((current) => [...current, result.value]);
      setTextValue("");
      setDrawStrokes([]);
      setImageFile(null);
      setSignatureStrokes([]);
      setSignatureImageFile(null);
      setConsentAccepted(false);
      setError("");
    } catch (caught) {
      setError(caught?.message || t.failed);
    }
  }

  async function handlePickedPdfFile(nextFile) {
    if (!nextFile) return setFile(null);
    const securityError = await validateBrowserUpload(
      nextFile,
      FILE_SECURITY_POLICY.pdfTool,
    );
    if (securityError) {
      setFile(null);
      setError(securityError);
      return;
    }
    setError("");
    setFile(nextFile);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResponse(null);
    if (!file) return setError(t.noFile);
    if (!operations.length) return setError(t.noOperations);

    const formData = new FormData();
    formData.append("file", file);
    const payloadOperations = operations.map(
      ({ _assetFile, ...operation }) => operation,
    );
    formData.append("operations_json", JSON.stringify(payloadOperations));
    for (const operation of operations) {
      if (operation._assetFile)
        formData.append(
          "edit_assets",
          operation._assetFile,
          assetFilename(operation.operation_id, operation._assetFile),
        );
    }
    formData.append(
      "output_filename",
      normalizePdfFilename(outputFilename, "edited-document.pdf"),
    );
    formData.append("generate_preview", String(generatePreview));
    formData.append("system_language", systemLanguageFor(language));

    setBusy(true);
    try {
      setResponse(await postAnalyzerFeature(FEATURE_PATH, formData, true));
    } catch (caught) {
      setError(caught?.message || t.failed);
    } finally {
      setBusy(false);
    }
  }

  const result = response?.result || null;
  const outputUrl = normalizeArtifactUrl(
    result?.download_url ||
      result?.pdf_artifact?.download_url ||
      result?.file?.download_url,
  );
  const previewUrl = normalizeArtifactUrl(
    result?.preview?.download_url || result?.preview_pdf?.download_url,
  );
  if (!authChecked)
    return (
      <AppSidebarLayout>
        <main className="app-page min-h-screen p-6 app-text">{t.loading}</main>
      </AppSidebarLayout>
    );
  if (!user)
    return (
      <AppSidebarLayout>
        <main className="app-page min-h-screen p-6">
          <AuthRequired t={t} />
        </main>
      </AppSidebarLayout>
    );

  return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen px-4 py-6 app-text md:px-8">
        <button
          type="button"
          onClick={() => router.back()}
          className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted"
        >
          <ArrowLeft className="h-4 w-4" />
          {t.back}
        </button>
        <section className="mb-8 rounded-3xl border app-surface-strong p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">
            {t.badge}
          </p>
          <h1 className="mt-3 text-3xl font-semibold app-text md:text-4xl">
            {t.title}
          </h1>
          <p className="mt-3 max-w-3xl app-text-muted">{t.description}</p>
        </section>

        <form
          onSubmit={handleSubmit}
          className="grid gap-6 lg:grid-cols-[1fr_0.95fr]"
        >
          <div className="space-y-6">
            <section className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">
                1. {t.uploadTitle}
              </h2>
              <p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="mt-4 flex w-full flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center"
              >
                <UploadCloud className="h-10 w-10 app-text-muted" />
                <span className="mt-3 text-sm font-semibold app-text">
                  {file?.name || t.chooseFile}
                </span>
                {file ? (
                  <span className="mt-1 text-xs text-emerald-300">
                    {t.fileReady}
                  </span>
                ) : null}
              </button>
              {file ? (
                <button
                  type="button"
                  onClick={() => {
                    setFile(null);
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                  className="mt-3 inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-2 text-xs font-semibold text-red-300"
                >
                  <Trash2 className="h-4 w-4" />
                  {t.removeFile}
                </button>
              ) : null}
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(event) =>
                  handlePickedPdfFile(event.target.files?.[0] || null)
                }
              />
            </section>

            <section className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">
                2. {t.outputSettings}
              </h2>
              <label className="mt-4 block text-sm font-medium app-text">
                {t.outputFilename}
                <input
                  value={outputFilename}
                  onChange={(event) => setOutputFilename(event.target.value)}
                  className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                />
              </label>
              <label className="mt-4 flex items-start gap-3 text-sm app-text">
                <input
                  type="checkbox"
                  checked={generatePreview}
                  onChange={(event) => setGeneratePreview(event.target.checked)}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium">{t.generatePreview}</span>
                  <span className="mt-1 block text-xs app-text-muted">
                    {t.generatePreviewHelp}
                  </span>
                </span>
              </label>
            </section>

            <section className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">
                3. {t.operationBuilder}
              </h2>
              <p className="mt-1 text-sm app-text-muted">
                {t.operationBuilderHelp}
              </p>
              <label className="mt-4 block text-sm font-medium app-text">
                {t.operationType}
                <select
                  value={operationType}
                  onChange={(event) => setOperationType(event.target.value)}
                  className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                >
                  {operationOptions.map(([value, key]) => (
                    <option key={value} value={value}>
                      {t[key]}
                    </option>
                  ))}
                </select>
              </label>
              <p className="mt-2 rounded-xl border app-surface p-3 text-xs app-text-muted">
                {t[`${operationType}Help`]}
              </p>

              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                <label className="text-sm font-medium app-text">
                  {t.pageNumber}
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={pageNumber}
                    onChange={(event) => setPageNumber(event.target.value)}
                    className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                  />
                </label>
                <label className="text-sm font-medium app-text">
                  {t.positionPreset}
                  <select
                    value={positionPreset}
                    onChange={(event) => updatePreset(event.target.value)}
                    className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                  >
                    <option value="top">{t.top}</option>
                    <option value="center">{t.center}</option>
                    <option value="bottom">{t.bottom}</option>
                    <option value="custom">{t.custom}</option>
                  </select>
                </label>
              </div>

              <fieldset className="mt-4 rounded-2xl border app-surface p-4">
                <legend className="px-2 text-sm font-semibold app-text">
                  {t.positionAndSize}
                </legend>
                <p className="mb-3 text-xs app-text-muted">{t.rectangleHelp}</p>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {[
                    ["x", t.leftPercent],
                    ["y", t.topPercent],
                    ["width", t.widthPercent],
                    ["height", t.heightPercent],
                  ].map(([key, label]) => (
                    <label key={key} className="text-xs font-medium app-text">
                      {label}
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="1"
                        value={rectangle[key]}
                        onChange={(event) => {
                          setPositionPreset("custom");
                          setRectangle((current) => ({
                            ...current,
                            [key]: event.target.value,
                          }));
                        }}
                        className="mt-1 w-full rounded-xl border app-surface px-3 py-2 app-text"
                      />
                    </label>
                  ))}
                </div>
              </fieldset>

              {operationType === "add_text" ? (
                <div className="mt-4 grid gap-3">
                  <label className="text-sm font-medium app-text">
                    {t.text}
                    <textarea
                      value={textValue}
                      onChange={(event) => setTextValue(event.target.value)}
                      rows={3}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                    />
                  </label>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm font-medium app-text">
                      {t.fontSize}
                      <input
                        type="number"
                        min="4"
                        max="96"
                        value={fontSize}
                        onChange={(event) => setFontSize(event.target.value)}
                        className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                      />
                    </label>
                    <label className="text-sm font-medium app-text">
                      {t.color}
                      <input
                        type="color"
                        value={colorHex}
                        onChange={(event) => setColorHex(event.target.value)}
                        className="mt-2 h-12 w-full rounded-2xl border app-surface p-1"
                      />
                    </label>
                  </div>
                </div>
              ) : null}

              {operationType === "draw" ? (
                <div className="mt-4 grid gap-3">
                  <CanvasPad
                    label={t.drawPad}
                    help={t.drawPadHelp}
                    clearLabel={t.clearDrawing}
                    color={colorHex}
                    strokes={drawStrokes}
                    onChange={setDrawStrokes}
                  />
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm font-medium app-text">
                      {t.strokeWidth}
                      <input
                        type="number"
                        min="0.25"
                        max="25"
                        step="0.25"
                        value={strokeWidth}
                        onChange={(event) => setStrokeWidth(event.target.value)}
                        className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                      />
                    </label>
                    <label className="text-sm font-medium app-text">
                      {t.color}
                      <input
                        type="color"
                        value={colorHex}
                        onChange={(event) => setColorHex(event.target.value)}
                        className="mt-2 h-12 w-full rounded-2xl border app-surface p-1"
                      />
                    </label>
                  </div>
                </div>
              ) : null}

              {operationType === "highlight" ? (
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <label className="text-sm font-medium app-text">
                    {t.opacity}: {opacity}%
                    <input
                      type="range"
                      min="5"
                      max="100"
                      step="5"
                      value={opacity}
                      onChange={(event) => setOpacity(event.target.value)}
                      className="mt-3 w-full"
                    />
                  </label>
                  <label className="text-sm font-medium app-text">
                    {t.color}
                    <input
                      type="color"
                      value={colorHex}
                      onChange={(event) => setColorHex(event.target.value)}
                      className="mt-2 h-12 w-full rounded-2xl border app-surface p-1"
                    />
                  </label>
                </div>
              ) : null}

              {operationType === "add_image" ? (
                <div className="mt-4 grid gap-3">
                  <label className="text-sm font-medium app-text">
                    {t.chooseImage}
                    <input
                      type="file"
                      accept="image/png,image/jpeg,.png,.jpg,.jpeg"
                      onChange={(event) =>
                        pickEditImage(
                          event.target.files?.[0] || null,
                          setImageFile,
                        )
                      }
                      className="mt-2 block w-full rounded-2xl border app-surface px-4 py-3 text-sm app-text"
                    />
                  </label>
                  {imageFile ? (
                    <p className="text-xs text-emerald-300">
                      {t.selectedImage}: {imageFile.name}
                    </p>
                  ) : (
                    <p className="text-xs app-text-muted">{t.imageHelp}</p>
                  )}
                </div>
              ) : null}

              {operationType === "add_signature" ? (
                <div className="mt-4 space-y-3">
                  <label className="block text-sm font-medium app-text">
                    {t.signatureType}
                    <select
                      value={signatureType}
                      onChange={(event) => setSignatureType(event.target.value)}
                      className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                    >
                      <option value="typed">{t.typed}</option>
                      <option value="drawn">{t.drawn}</option>
                      <option value="uploaded_image">{t.uploaded}</option>
                    </select>
                  </label>
                  {signatureType === "typed" ? (
                    <label className="block text-sm font-medium app-text">
                      {t.typedName}
                      <input
                        value={typedName}
                        onChange={(event) => setTypedName(event.target.value)}
                        className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                      />
                    </label>
                  ) : null}
                  {signatureType === "drawn" ? (
                    <CanvasPad
                      label={t.signaturePad}
                      help={t.signaturePadHelp}
                      clearLabel={t.clearSignature}
                      strokes={signatureStrokes}
                      onChange={setSignatureStrokes}
                    />
                  ) : null}
                  {signatureType === "uploaded_image" ? (
                    <div>
                      <label className="block text-sm font-medium app-text">
                        {t.chooseSignatureImage}
                        <input
                          type="file"
                          accept="image/png,image/jpeg,.png,.jpg,.jpeg"
                          onChange={(event) =>
                            pickEditImage(
                              event.target.files?.[0] || null,
                              setSignatureImageFile,
                            )
                          }
                          className="mt-2 block w-full rounded-2xl border app-surface px-4 py-3 text-sm app-text"
                        />
                      </label>
                      {signatureImageFile ? (
                        <p className="mt-2 text-xs text-emerald-300">
                          {t.selectedImage}: {signatureImageFile.name}
                        </p>
                      ) : (
                        <p className="mt-2 text-xs app-text-muted">
                          {t.imageHelp}
                        </p>
                      )}
                    </div>
                  ) : null}
                  <label className="flex items-start gap-3 rounded-2xl border app-surface p-3 text-sm app-text">
                    <input
                      type="checkbox"
                      checked={consentAccepted}
                      onChange={(event) =>
                        setConsentAccepted(event.target.checked)
                      }
                      className="mt-1"
                    />
                    <span>{t.signatureConsent}</span>
                  </label>
                  <p className="text-xs app-text-muted">{t.signatureNotice}</p>
                </div>
              ) : null}

              {[
                "remove_text",
                "remove_image",
                "whiteout",
                "remove_signature",
              ].includes(operationType) ? (
                <p className="mt-4 rounded-2xl border border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-100">
                  {t.permanentRemovalWarning}
                </p>
              ) : null}

              <button
                type="button"
                onClick={addOperation}
                className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-2xl border app-surface px-4 py-3 text-sm font-semibold app-text"
              >
                <Plus className="h-4 w-4" />
                {t.addOperation}
              </button>
              <p className="mt-2 text-center text-xs app-text-muted">
                {t.addOperationHelp}
              </p>
            </section>
          </div>

          <div className="space-y-6">
            <section className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">
                4. {t.operations}
              </h2>
              <p className="mt-1 text-sm app-text-muted">{t.operationsHelp}</p>
              {operations.length ? (
                <div className="mt-4 space-y-3">
                  {operations.map((operation, index) => (
                    <div
                      key={operation.operation_id}
                      className="flex items-start justify-between gap-3 rounded-2xl border app-surface p-4"
                    >
                      <div>
                        <span className="inline-flex items-center gap-2 text-sm font-semibold app-text">
                          <OperationIcon operation={operation.operation} />
                          {index + 1}.{" "}
                          {t[
                            operationOptions.find(
                              ([value]) => value === operation.operation,
                            )?.[1]
                          ] || operation.operation}
                        </span>
                        <p className="mt-1 text-xs app-text-muted">
                          {t.pageNumber}: {operation.page_number} ·{" "}
                          {Math.round(operation.rectangle.x * 100)}% /{" "}
                          {Math.round(operation.rectangle.y * 100)}% ·{" "}
                          {Math.round(operation.rectangle.width * 100)}% ×{" "}
                          {Math.round(operation.rectangle.height * 100)}%
                        </p>
                      </div>
                      <button
                        type="button"
                        aria-label={t.removeOperation}
                        onClick={() =>
                          setOperations((current) =>
                            current.filter(
                              (item) =>
                                item.operation_id !== operation.operation_id,
                            ),
                          )
                        }
                        className="text-red-300"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-4 rounded-2xl border border-dashed app-surface p-5 text-center text-sm app-text-muted">
                  {t.noOperations}
                </p>
              )}
            </section>
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
                <FilePenLine className="h-4 w-4" />
              )}
              {busy ? t.editing : t.edit}
            </button>
            {result ? (
              <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5">
                <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
                  <CheckCircle2 className="h-5 w-5" />
                  {t.resultTitle}
                </h2>
                <p className="mt-3 text-sm app-text-muted">
                  {t.requested}:{" "}
                  {result.operations_requested ?? operations.length} ·{" "}
                  {t.applied}: {result.operations_applied ?? "—"}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {outputUrl ? (
                    <a
                      href={outputUrl}
                      className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"
                    >
                      <Download className="h-4 w-4" />
                      {t.download}
                    </a>
                  ) : null}
                  {previewUrl ? (
                    <a
                      href={previewUrl}
                      className="rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text"
                    >
                      {t.preview}
                    </a>
                  ) : null}
                </div>
                {generatePreview && (previewUrl || outputUrl) ? (
                  <iframe
                    title={t.previewTitle}
                    src={previewUrl || outputUrl}
                    className="mt-5 h-[520px] w-full rounded-2xl border bg-white"
                  />
                ) : null}
              </section>
            ) : null}
          </div>
        </form>
      </main>
    </AppSidebarLayout>
  );
}