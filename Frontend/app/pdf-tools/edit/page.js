"use client";

import { useMemo, useRef, useState } from "react";
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

const FEATURE_PATH = "pdf/edit";
const MAX_PDF_SIZE_MB = 50;
const DEFAULT_RECTANGLE = { x: "0.12", y: "0.12", width: "0.5", height: "0.08" };

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

function systemLanguageFor(language) { return language === "fr" ? "french" : "english"; }
function isPdf(file) { const type = String(file?.type || "").toLowerCase(); const name = String(file?.name || "").toLowerCase(); return type === "application/pdf" || type === "application/x-pdf" || name.endsWith(".pdf"); }
function fileSizeMb(file) { return file.size / (1024 * 1024); }
function parseInteger(value, fallback = 1) { const parsed = Number.parseInt(String(value ?? ""), 10); return Number.isFinite(parsed) && parsed >= 1 ? parsed : fallback; }
function parseFloatSafe(value, fallback) { const parsed = Number.parseFloat(String(value ?? "")); return Number.isFinite(parsed) ? parsed : fallback; }
function normalizePdfFilename(value, fallback) { const raw = String(value || "").trim() || fallback; return raw.toLowerCase().endsWith(".pdf") ? raw : `${raw}.pdf`; }
function normalizeArtifactUrl(url) { if (!url) return ""; const raw = String(url); if (/^https?:\/\//i.test(raw)) return raw; return raw.replace(/^\/api\/v1\/analyzer\/artifacts\//, "/api/analyzer/artifacts/"); }
function normalizeRectangle(rectangle) { const x = parseFloatSafe(rectangle.x, 0.12); const y = parseFloatSafe(rectangle.y, 0.12); const width = parseFloatSafe(rectangle.width, 0.5); const height = parseFloatSafe(rectangle.height, 0.08); if (x < 0 || y < 0 || width <= 0 || height <= 0 || x > 1 || y > 1 || x + width > 1 || y + height > 1) return null; return { x, y, width, height }; }
function uid(prefix) { return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`; }

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
      <a href="/auth/login?returnTo=/pdf-tools/edit" className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]">{t.signIn}</a>
    </section>
  );
}

function buildOperation({ operationType, pageNumber, rectangle, textValue, fontSize, colorHex, opacity, svgPath, strokeWidth, imageStorageKey, imageMimeType, signatureType, typedName, signatureSvgStorageKey, signatureImageStorageKey }) {
  const rect = normalizeRectangle(rectangle);
  if (!rect) return { error: "badRectangle" };
  const base = { operation_id: uid("op"), operation: operationType, page_number: parseInteger(pageNumber, 1), rectangle: rect };

  if (operationType === "add_text") {
    if (!textValue.trim()) return { error: "needsText" };
    return { value: { ...base, text: textValue.trim(), font_size: parseFloatSafe(fontSize, 12), font_family: "Helvetica", color_hex: colorHex || "#111111" } };
  }
  if (["remove_text", "remove_image", "whiteout", "remove_signature"].includes(operationType)) return { value: { ...base } };
  if (operationType === "highlight") return { value: { ...base, color_hex: colorHex || "#FFF176", opacity: Math.max(0.05, Math.min(1, parseFloatSafe(opacity, 0.35))) } };
  if (operationType === "draw") {
    if (!svgPath.trim()) return { error: "needsPath" };
    return { value: { ...base, path_svg: svgPath.trim(), stroke_width: parseFloatSafe(strokeWidth, 2), stroke_color_hex: colorHex || "#111111" } };
  }
  if (operationType === "add_image") {
    if (!imageStorageKey.trim()) return { error: "needsImageKey" };
    return { value: { ...base, image_storage_key: imageStorageKey.trim(), image_mime_type: imageMimeType || "image/png" } };
  }
  if (operationType === "add_signature") {
    if (signatureType === "typed") {
      if (!typedName.trim()) return { error: "needsSignature" };
      return { value: { ...base, signature_type: "typed", typed_name: typedName.trim(), consent_accepted: true } };
    }
    if (signatureType === "drawn") {
      if (!signatureSvgStorageKey.trim()) return { error: "needsSignature" };
      return { value: { ...base, signature_type: "drawn", signature_svg_storage_key: signatureSvgStorageKey.trim(), consent_accepted: true } };
    }
    if (!signatureImageStorageKey.trim()) return { error: "needsSignature" };
    return { value: { ...base, signature_type: "uploaded_image", signature_image_storage_key: signatureImageStorageKey.trim(), consent_accepted: true } };
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
  const [rectangle, setRectangle] = useState({ ...DEFAULT_RECTANGLE });
  const [textValue, setTextValue] = useState("");
  const [fontSize, setFontSize] = useState("12");
  const [colorHex, setColorHex] = useState("#111111");
  const [opacity, setOpacity] = useState("0.35");
  const [svgPath, setSvgPath] = useState("M 0.1 0.5 L 0.4 0.2 L 0.9 0.8");
  const [strokeWidth, setStrokeWidth] = useState("2");
  const [imageStorageKey, setImageStorageKey] = useState("");
  const [imageMimeType, setImageMimeType] = useState("image/png");
  const [signatureType, setSignatureType] = useState("typed");
  const [typedName, setTypedName] = useState(user?.name || "");
  const [signatureSvgStorageKey, setSignatureSvgStorageKey] = useState("");
  const [signatureImageStorageKey, setSignatureImageStorageKey] = useState("");
  const [operations, setOperations] = useState([]);
  const [outputFilename, setOutputFilename] = useState("edited-document.pdf");
  const [generatePreview, setGeneratePreview] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);

  function addOperation() {
    const result = buildOperation({ operationType, pageNumber, rectangle, textValue, fontSize, colorHex, opacity, svgPath, strokeWidth, imageStorageKey, imageMimeType, signatureType, typedName, signatureSvgStorageKey, signatureImageStorageKey });
    if (result.error) {
      setError(t[result.error] || t.failed);
      return;
    }
    setOperations((current) => [...current, result.value]);
    setError("");
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResponse(null);
    if (!file) return setError(t.noFile);
    if (!isPdf(file)) return setError(t.invalidFile);
    if (fileSizeMb(file) > MAX_PDF_SIZE_MB) return setError(t.tooLarge);
    if (!operations.length) return setError(t.noOperations);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("operations_json", JSON.stringify(operations));
    formData.append("output_filename", normalizePdfFilename(outputFilename, "edited-document.pdf"));
    formData.append("generate_preview", String(generatePreview));
    formData.append("system_language", systemLanguageFor(language));

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
  const outputUrl = normalizeArtifactUrl(result?.download_url || result?.pdf_artifact?.download_url || result?.file?.download_url);
  const previewUrl = normalizeArtifactUrl(result?.preview?.download_url || result?.preview_pdf?.download_url);

  if (!authChecked) return <main className="app-page min-h-screen p-6 app-text">{t.loading}</main>;
  if (!user) return <main className="app-page min-h-screen p-6"><AuthRequired t={t} /></main>;

  return (
    <main className="app-page min-h-screen px-4 py-6 app-text md:px-8">
      <button type="button" onClick={() => router.back()} className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted"><ArrowLeft className="h-4 w-4" />{t.back}</button>
      <section className="mb-8 rounded-3xl border app-surface-strong p-6"><p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">{t.badge}</p><h1 className="mt-3 text-3xl font-semibold app-text md:text-4xl">{t.title}</h1><p className="mt-3 max-w-3xl app-text-muted">{t.description}</p></section>

      <form onSubmit={handleSubmit} className="grid gap-6 lg:grid-cols-[1fr_0.95fr]">
        <div className="space-y-6">
          <section className="rounded-3xl border app-surface-strong p-5">
            <h2 className="text-lg font-semibold app-text">{t.uploadTitle}</h2><p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p>
            <button type="button" onClick={() => fileInputRef.current?.click()} className="mt-4 flex w-full flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center"><UploadCloud className="h-10 w-10 app-text-muted" /><span className="mt-3 text-sm font-semibold app-text">{file?.name || t.chooseFile}</span></button>
            <input ref={fileInputRef} type="file" accept="application/pdf,.pdf" className="hidden" onChange={(event) => setFile(event.target.files?.[0] || null)} />
          </section>

          <section className="rounded-3xl border app-surface-strong p-5">
            <h2 className="text-lg font-semibold app-text">{t.operationBuilder}</h2>
            <label className="mt-4 block text-sm font-medium app-text">{t.operationType}<select value={operationType} onChange={(event) => setOperationType(event.target.value)} className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text">{operationOptions.map(([value, key]) => <option key={value} value={value}>{t[key]}</option>)}</select></label>
            <div className="mt-4 grid gap-3 sm:grid-cols-2"><input type="number" min="1" value={pageNumber} onChange={(event) => setPageNumber(event.target.value)} className="rounded-2xl border app-surface px-4 py-3 app-text" /><input value={outputFilename} onChange={(event) => setOutputFilename(event.target.value)} className="rounded-2xl border app-surface px-4 py-3 app-text" /></div>
            <div className="mt-4 grid grid-cols-4 gap-2">{["x", "y", "width", "height"].map((key) => <input key={key} aria-label={key} value={rectangle[key]} onChange={(event) => setRectangle((current) => ({ ...current, [key]: event.target.value }))} className="rounded-xl border app-surface px-3 py-2 app-text" />)}</div>

            {operationType === "add_text" ? <div className="mt-4 grid gap-3"><textarea value={textValue} onChange={(event) => setTextValue(event.target.value)} rows={3} placeholder={t.text} className="rounded-2xl border app-surface px-4 py-3 app-text" /><input type="number" value={fontSize} onChange={(event) => setFontSize(event.target.value)} className="rounded-2xl border app-surface px-4 py-3 app-text" /></div> : null}
            {operationType === "draw" ? <div className="mt-4 grid gap-3"><textarea value={svgPath} onChange={(event) => setSvgPath(event.target.value)} rows={3} placeholder={t.svgPath} className="rounded-2xl border app-surface px-4 py-3 app-text" /><input type="number" value={strokeWidth} onChange={(event) => setStrokeWidth(event.target.value)} className="rounded-2xl border app-surface px-4 py-3 app-text" /></div> : null}
            {operationType === "highlight" ? <input type="number" step="0.05" min="0.05" max="1" value={opacity} onChange={(event) => setOpacity(event.target.value)} className="mt-4 w-full rounded-2xl border app-surface px-4 py-3 app-text" /> : null}
            {["add_text", "draw", "highlight"].includes(operationType) ? <input type="color" value={colorHex} onChange={(event) => setColorHex(event.target.value)} className="mt-4 h-12 w-full rounded-2xl border app-surface p-1" /> : null}
            {operationType === "add_image" ? <div className="mt-4 grid gap-3"><input value={imageStorageKey} onChange={(event) => setImageStorageKey(event.target.value)} placeholder={t.imageStorageKey} className="rounded-2xl border app-surface px-4 py-3 app-text" /><select value={imageMimeType} onChange={(event) => setImageMimeType(event.target.value)} className="rounded-2xl border app-surface px-4 py-3 app-text"><option value="image/png">image/png</option><option value="image/jpeg">image/jpeg</option><option value="image/webp">image/webp</option></select></div> : null}
            {operationType === "add_signature" ? <div className="mt-4 space-y-3"><select value={signatureType} onChange={(event) => setSignatureType(event.target.value)} className="w-full rounded-2xl border app-surface px-4 py-3 app-text"><option value="typed">{t.typed}</option><option value="drawn">{t.drawn}</option><option value="uploaded_image">{t.uploaded}</option></select>{signatureType === "typed" ? <input value={typedName} onChange={(event) => setTypedName(event.target.value)} placeholder={t.typedName} className="w-full rounded-2xl border app-surface px-4 py-3 app-text" /> : null}{signatureType === "drawn" ? <input value={signatureSvgStorageKey} onChange={(event) => setSignatureSvgStorageKey(event.target.value)} placeholder={t.signatureSvgStorageKey} className="w-full rounded-2xl border app-surface px-4 py-3 app-text" /> : null}{signatureType === "uploaded_image" ? <input value={signatureImageStorageKey} onChange={(event) => setSignatureImageStorageKey(event.target.value)} placeholder={t.signatureImageStorageKey} className="w-full rounded-2xl border app-surface px-4 py-3 app-text" /> : null}<p className="text-xs app-text-soft">{t.assetKeyHelp}</p></div> : null}

            <label className="mt-4 flex items-center gap-3 text-sm app-text"><input type="checkbox" checked={generatePreview} onChange={(event) => setGeneratePreview(event.target.checked)} />{t.generatePreview}</label>
            <button type="button" onClick={addOperation} className="mt-4 inline-flex items-center gap-2 rounded-2xl border app-surface px-4 py-3 text-sm font-semibold app-text"><Plus className="h-4 w-4" />{t.addOperation}</button>
          </section>
        </div>

        <div className="space-y-6">
          <section className="rounded-3xl border app-surface-strong p-5"><h2 className="text-lg font-semibold app-text">{t.operations}</h2>{operations.length ? <div className="mt-4 space-y-3">{operations.map((operation) => <div key={operation.operation_id} className="flex items-center justify-between gap-3 rounded-2xl border app-surface p-4"><span className="inline-flex items-center gap-2 text-sm font-semibold app-text"><OperationIcon operation={operation.operation} />{operation.operation}</span><button type="button" onClick={() => setOperations((current) => current.filter((item) => item.operation_id !== operation.operation_id))} className="text-red-300"><Trash2 className="h-4 w-4" /></button></div>)}</div> : <p className="mt-3 text-sm app-text-muted">{t.noOperations}</p>}</section>
          {error ? <p className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200"><AlertTriangle className="mr-2 inline h-4 w-4" />{error}</p> : null}
          <button type="submit" disabled={busy} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-60">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FilePenLine className="h-4 w-4" />}{busy ? t.editing : t.edit}</button>
          {result ? <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5"><h2 className="flex items-center gap-2 text-lg font-semibold app-text"><CheckCircle2 className="h-5 w-5" />{t.resultTitle}</h2><p className="mt-3 text-sm app-text-muted">{t.requested}: {result.operations_requested ?? operations.length} · {t.applied}: {result.operations_applied ?? "—"}</p><div className="mt-4 flex flex-wrap gap-2">{outputUrl ? <a href={outputUrl} className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"><Download className="h-4 w-4" />{t.download}</a> : null}{previewUrl ? <a href={previewUrl} className="rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text">{t.preview}</a> : null}</div></section> : null}
        </div>
      </form>
    </main>
  );
}
