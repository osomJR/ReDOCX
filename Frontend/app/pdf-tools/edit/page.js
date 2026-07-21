"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  Eraser,
  FileImage,
  FilePenLine,
  Highlighter,
  Image as ImageIcon,
  Loader2,
  MousePointer2,
  PenLine,
  Plus,
  Redo2,
  Replace,
  RotateCcw,
  Trash2,
  Type,
  Undo2,
  UploadCloud,
  ZoomIn,
  ZoomOut,
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
const BASE_RENDER_SCALE = 1.25;
const MIN_RECT_SIZE = 0.012;
const MAX_HISTORY = 60;
const copy = editPdfPageTranslations;

const VISUAL_COPY = {
  en: {
    editorTitle: "Edit directly on the document",
    editorHelp:
      "Choose a tool, then click and drag over the PDF. ReDOCX converts your visual changes into secure PDF operations when you submit.",
    loadingPdf: "Preparing your PDF…",
    renderFailed: "The PDF could not be displayed. Confirm that it is a valid, unencrypted PDF.",
    select: "Select",
    correctText: "Correct text",
    addText: "Add text",
    highlight: "Highlight",
    whiteout: "Whiteout",
    draw: "Draw",
    image: "Image",
    signature: "Signature",
    dragInstruction: "Drag on the page to place this edit.",
    drawInstruction: "Draw directly on the page.",
    imageInstruction: "Choose an image, then drag on the page to place it.",
    selectInstruction: "Select an edit to move, resize, update, or delete it.",
    currentPage: "Page",
    previousPage: "Previous page",
    nextPage: "Next page",
    zoomOut: "Zoom out",
    zoomIn: "Zoom in",
    undo: "Undo",
    redo: "Redo",
    clearAll: "Clear all edits",
    inspector: "Edit properties",
    noSelection: "Select an edit on the document to change its properties.",
    replacementText: "Replacement text",
    insertedText: "Text",
    fontSize: "Font size",
    color: "Color",
    opacity: "Opacity",
    strokeWidth: "Stroke width",
    replaceImage: "Replace image",
    signatureType: "Signature type",
    typed: "Typed",
    drawn: "Drawn",
    uploaded: "Uploaded image",
    typedName: "Name shown as signature",
    signaturePad: "Draw signature",
    clearSignature: "Clear signature",
    signatureConsent:
      "I confirm that I am authorized to apply this signature to the document.",
    deleteEdit: "Delete edit",
    duplicateEdit: "Duplicate edit",
    edits: "Document edits",
    noEdits: "No edits have been added yet.",
    correction: "Text correction",
    textAddition: "Added text",
    imageAddition: "Added image",
    signatureAddition: "Signature",
    drawing: "Drawing",
    editCount: "edits",
    chooseImage: "Choose image",
    chooseSignatureImage: "Choose signature image",
    noText: "Every text correction or text addition must contain text.",
    noDrawing: "Every drawing must contain at least one stroke.",
    noImage: "Every image edit must include an image.",
    noSignature: "Every signature must include a name, drawing, or uploaded image.",
    noConsent: "Signature authorization must be confirmed before processing.",
    tooManyOperations: "This document exceeds the 500-operation processing limit.",
    tooManyAssets: "A single edit request can include at most 20 image or signature files.",
    badPlacement: "Create a larger edit region inside the page.",
    removeFileConfirm: "Remove this PDF and discard all edits?",
    clearConfirm: "Discard all edits on this PDF?",
    visualWorkflow: "Visual editing workspace",
    hiddenCoordinates:
      "Placement is captured automatically. Users never need to enter page coordinates.",
    selectedFile: "Selected PDF",
    changePdf: "Change PDF",
    readyToProcess: "Review the visual edits, then submit the document for processing.",
    processingNote:
      "Text corrections permanently remove the selected content before inserting the replacement text.",
  },
  fr: {
    editorTitle: "Modifiez directement le document",
    editorHelp:
      "Choisissez un outil, puis cliquez-glissez sur le PDF. ReDOCX convertit vos modifications visuelles en opérations PDF sécurisées lors de l’envoi.",
    loadingPdf: "Préparation du PDF…",
    renderFailed: "Le PDF ne peut pas être affiché. Vérifiez qu’il est valide et non chiffré.",
    select: "Sélectionner",
    correctText: "Corriger le texte",
    addText: "Ajouter du texte",
    highlight: "Surligner",
    whiteout: "Effacer",
    draw: "Dessiner",
    image: "Image",
    signature: "Signature",
    dragInstruction: "Faites glisser sur la page pour placer cette modification.",
    drawInstruction: "Dessinez directement sur la page.",
    imageInstruction: "Choisissez une image, puis faites glisser pour la placer.",
    selectInstruction: "Sélectionnez une modification pour la déplacer, la redimensionner ou la supprimer.",
    currentPage: "Page",
    previousPage: "Page précédente",
    nextPage: "Page suivante",
    zoomOut: "Réduire",
    zoomIn: "Agrandir",
    undo: "Annuler",
    redo: "Rétablir",
    clearAll: "Effacer toutes les modifications",
    inspector: "Propriétés",
    noSelection: "Sélectionnez une modification sur le document pour la mettre à jour.",
    replacementText: "Texte de remplacement",
    insertedText: "Texte",
    fontSize: "Taille de police",
    color: "Couleur",
    opacity: "Opacité",
    strokeWidth: "Épaisseur du trait",
    replaceImage: "Remplacer l’image",
    signatureType: "Type de signature",
    typed: "Saisie",
    drawn: "Dessinée",
    uploaded: "Image importée",
    typedName: "Nom affiché comme signature",
    signaturePad: "Dessiner la signature",
    clearSignature: "Effacer la signature",
    signatureConsent: "Je confirme être autorisé à apposer cette signature au document.",
    deleteEdit: "Supprimer la modification",
    duplicateEdit: "Dupliquer la modification",
    edits: "Modifications du document",
    noEdits: "Aucune modification n’a encore été ajoutée.",
    correction: "Correction de texte",
    textAddition: "Texte ajouté",
    imageAddition: "Image ajoutée",
    signatureAddition: "Signature",
    drawing: "Dessin",
    editCount: "modifications",
    chooseImage: "Choisir une image",
    chooseSignatureImage: "Choisir une image de signature",
    noText: "Chaque correction ou ajout de texte doit contenir du texte.",
    noDrawing: "Chaque dessin doit contenir au moins un trait.",
    noImage: "Chaque modification d’image doit inclure une image.",
    noSignature: "Chaque signature doit inclure un nom, un dessin ou une image.",
    noConsent: "L’autorisation de signature doit être confirmée avant le traitement.",
    tooManyOperations: "Ce document dépasse la limite de 500 opérations.",
    tooManyAssets: "Une demande peut contenir au maximum 20 images ou signatures.",
    badPlacement: "Créez une zone de modification plus grande dans la page.",
    removeFileConfirm: "Retirer ce PDF et supprimer toutes les modifications ?",
    clearConfirm: "Supprimer toutes les modifications de ce PDF ?",
    visualWorkflow: "Espace de modification visuelle",
    hiddenCoordinates:
      "Le placement est enregistré automatiquement. Aucune coordonnée de page n’est demandée.",
    selectedFile: "PDF sélectionné",
    changePdf: "Changer de PDF",
    readyToProcess: "Vérifiez les modifications visuelles, puis envoyez le document.",
    processingNote:
      "Les corrections de texte suppriment définitivement le contenu sélectionné avant d’insérer le nouveau texte.",
  },
};

const TOOL_DEFINITIONS = [
  { id: "select", label: "select", icon: MousePointer2 },
  { id: "replace_text", label: "correctText", icon: Replace },
  { id: "add_text", label: "addText", icon: Type },
  { id: "highlight", label: "highlight", icon: Highlighter },
  { id: "whiteout", label: "whiteout", icon: Eraser },
  { id: "draw", label: "draw", icon: PenLine },
  { id: "add_image", label: "image", icon: ImageIcon },
  { id: "add_signature", label: "signature", icon: FilePenLine },
];

function systemLanguageFor(language) {
  return language === "fr" ? "french" : "english";
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

function uid(prefix = "op") {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function assetFilename(operationId, file) {
  return `${operationId}${getFileExtension(file?.name) || ".png"}`;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function normalizedPoint(event, element) {
  const bounds = element.getBoundingClientRect();
  return {
    x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
    y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1),
  };
}

function rectangleFromPoints(start, end) {
  return {
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  };
}

function validRectangle(rectangle) {
  return (
    rectangle &&
    rectangle.width >= MIN_RECT_SIZE &&
    rectangle.height >= MIN_RECT_SIZE &&
    rectangle.x >= 0 &&
    rectangle.y >= 0 &&
    rectangle.x + rectangle.width <= 1.000001 &&
    rectangle.y + rectangle.height <= 1.000001
  );
}

function fitRectangle(rectangle) {
  const width = clamp(rectangle.width, MIN_RECT_SIZE, 1);
  const height = clamp(rectangle.height, MIN_RECT_SIZE, 1);
  return {
    x: clamp(rectangle.x, 0, 1 - width),
    y: clamp(rectangle.y, 0, 1 - height),
    width,
    height,
  };
}

function strokesToSvgPath(strokes) {
  return (strokes || [])
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

function drawingFromPagePoints(pagePoints) {
  if (!Array.isArray(pagePoints) || pagePoints.length < 2) return null;
  const padding = 0.006;
  const minX = clamp(Math.min(...pagePoints.map((point) => point.x)) - padding, 0, 1);
  const minY = clamp(Math.min(...pagePoints.map((point) => point.y)) - padding, 0, 1);
  const maxX = clamp(Math.max(...pagePoints.map((point) => point.x)) + padding, 0, 1);
  const maxY = clamp(Math.max(...pagePoints.map((point) => point.y)) + padding, 0, 1);
  const rectangle = {
    x: minX,
    y: minY,
    width: Math.max(MIN_RECT_SIZE, maxX - minX),
    height: Math.max(MIN_RECT_SIZE, maxY - minY),
  };
  const strokes = [
    pagePoints.map((point) => ({
      x: clamp((point.x - rectangle.x) / rectangle.width, 0, 1),
      y: clamp((point.y - rectangle.y) / rectangle.height, 0, 1),
    })),
  ];
  return { rectangle: fitRectangle(rectangle), strokes };
}

function strokesToPngFile(strokes, filename) {
  return new Promise((resolve, reject) => {
    const canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 360;
    const context = canvas.getContext("2d");
    if (!context) {
      reject(new Error("Could not prepare the signature image."));
      return;
    }
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = "#111111";
    context.lineWidth = 7;
    context.lineCap = "round";
    context.lineJoin = "round";
    for (const stroke of strokes || []) {
      if (stroke.length < 2) continue;
      context.beginPath();
      context.moveTo(stroke[0].x * canvas.width, stroke[0].y * canvas.height);
      for (const point of stroke.slice(1)) {
        context.lineTo(point.x * canvas.width, point.y * canvas.height);
      }
      context.stroke();
    }
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("Could not prepare the signature image."));
        return;
      }
      resolve(new File([blob], filename, { type: "image/png" }));
    }, "image/png");
  });
}

function itemLabel(item, vt) {
  const labels = {
    replace_text: vt.correction,
    add_text: vt.textAddition,
    highlight: vt.highlight,
    whiteout: vt.whiteout,
    draw: vt.drawing,
    add_image: vt.imageAddition,
    add_signature: vt.signatureAddition,
  };
  return labels[item.kind] || item.kind;
}

function toolInstruction(tool, vt) {
  if (tool === "select") return vt.selectInstruction;
  if (tool === "draw") return vt.drawInstruction;
  if (tool === "add_image") return vt.imageInstruction;
  return vt.dragInstruction;
}

function makeItem(kind, pageNumber, rectangle, assetFile = null) {
  const common = {
    id: uid("op"),
    kind,
    page_number: pageNumber,
    rectangle: fitRectangle(rectangle),
  };
  if (kind === "replace_text") {
    return { ...common, text: "", fontSize: 12, colorHex: "#111111" };
  }
  if (kind === "add_text") {
    return { ...common, text: "", fontSize: 12, colorHex: "#111111" };
  }
  if (kind === "highlight") {
    return { ...common, colorHex: "#fff176", opacity: 0.35 };
  }
  if (kind === "whiteout") return common;
  if (kind === "add_image") return { ...common, assetFile };
  if (kind === "add_signature") {
    return {
      ...common,
      signatureType: "typed",
      typedName: "",
      signatureStrokes: [],
      signatureImageFile: null,
      consentAccepted: false,
    };
  }
  return common;
}

function AssetPreview({ file, alt, className = "" }) {
  const previewRef = useRef(null);

  useEffect(() => {
    const preview = previewRef.current;
    if (!preview || !file) return undefined;

    const url = URL.createObjectURL(file);
    preview.style.backgroundImage = `url("${url}")`;

    return () => {
      preview.style.backgroundImage = "";
      URL.revokeObjectURL(url);
    };
  }, [file]);

  if (!file) return null;

  return (
    <div
      ref={previewRef}
      role="img"
      aria-label={alt}
      className={className}
      style={{
        backgroundPosition: "center",
        backgroundRepeat: "no-repeat",
        backgroundSize: "contain",
      }}
    />
  );
}

function SignaturePad({ strokes, onChange, vt }) {
  const canvasRef = useRef(null);
  const activeStrokes = useRef(strokes || []);
  const drawing = useRef(false);

  function redraw(nextStrokes = activeStrokes.current) {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = "#111111";
    context.lineWidth = 5;
    context.lineCap = "round";
    context.lineJoin = "round";
    for (const stroke of nextStrokes || []) {
      if (stroke.length < 2) continue;
      context.beginPath();
      context.moveTo(stroke[0].x * canvas.width, stroke[0].y * canvas.height);
      for (const point of stroke.slice(1)) {
        context.lineTo(point.x * canvas.width, point.y * canvas.height);
      }
      context.stroke();
    }
  }

  useEffect(() => {
    activeStrokes.current = strokes || [];
    redraw(strokes || []);
  }, [strokes]);

  function pointFor(event) {
    return normalizedPoint(event, canvasRef.current);
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
    const next = [...activeStrokes.current];
    next[next.length - 1] = [...next[next.length - 1], pointFor(event)];
    activeStrokes.current = next;
    redraw(next);
  }

  function finish(event) {
    if (!drawing.current) return;
    drawing.current = false;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onChange(activeStrokes.current.filter((stroke) => stroke.length >= 2));
  }

  return (
    <div>
      <p className="text-sm font-medium app-text">{vt.signaturePad}</p>
      <canvas
        ref={canvasRef}
        width="900"
        height="240"
        className="mt-2 h-40 w-full touch-none rounded-2xl border bg-white cursor-crosshair"
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={finish}
        onPointerCancel={finish}
        aria-label={vt.signaturePad}
      />
      <button
        type="button"
        onClick={() => onChange([])}
        className="mt-2 rounded-xl border app-surface px-3 py-2 text-xs font-semibold app-text"
      >
        {vt.clearSignature}
      </button>
    </div>
  );
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

function OverlayItem({
  item,
  selected,
  interactive,
  pageMetrics,
  onSelect,
  onMoveStart,
  onResizeStart,
  vt,
}) {
  const style = {
    left: `${item.rectangle.x * 100}%`,
    top: `${item.rectangle.y * 100}%`,
    width: `${item.rectangle.width * 100}%`,
    height: `${item.rectangle.height * 100}%`,
  };
  const pagePointScale = pageMetrics?.pdfWidth
    ? pageMetrics.width / pageMetrics.pdfWidth
    : 1;
  const textSize = Math.max(7, (item.fontSize || 12) * pagePointScale);

  return (
    <div
      role="button"
      tabIndex={interactive ? 0 : -1}
      aria-label={itemLabel(item, vt)}
      className={`absolute overflow-hidden select-none ${
        interactive ? "cursor-move" : "pointer-events-none"
      } ${selected ? "ring-2 ring-blue-500 ring-offset-1" : ""}`}
      style={style}
      onPointerDown={(event) => {
        if (!interactive) return;
        onSelect(item.id);
        onMoveStart(event, item);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onSelect(item.id);
      }}
    >
      {item.kind === "replace_text" ? (
        <div
          className="h-full w-full overflow-hidden bg-white px-1 text-left leading-tight"
          style={{ color: item.colorHex, fontSize: `${textSize}px` }}
        >
          {item.text || vt.replacementText}
        </div>
      ) : null}
      {item.kind === "add_text" ? (
        <div
          className="h-full w-full overflow-hidden px-1 text-left leading-tight"
          style={{ color: item.colorHex, fontSize: `${textSize}px` }}
        >
          {item.text || vt.insertedText}
        </div>
      ) : null}
      {item.kind === "highlight" ? (
        <div
          className="h-full w-full"
          style={{ backgroundColor: item.colorHex, opacity: item.opacity }}
        />
      ) : null}
      {item.kind === "whiteout" ? (
        <div className="h-full w-full border border-slate-300 bg-white/95" />
      ) : null}
      {item.kind === "draw" ? (
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="h-full w-full overflow-visible">
          {(item.strokes || []).map((stroke, index) => (
            <polyline
              key={`${item.id}-stroke-${index}`}
              points={stroke.map((point) => `${point.x},${point.y}`).join(" ")}
              fill="none"
              stroke={item.colorHex || "#111111"}
              strokeWidth={(item.strokeWidth || 2) / 500}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}
        </svg>
      ) : null}
      {item.kind === "add_image" ? (
        <AssetPreview
          file={item.assetFile}
          alt={vt.imageAddition}
          className="h-full w-full object-contain"
        />
      ) : null}
      {item.kind === "add_signature" && item.signatureType === "typed" ? (
        <div
          className="flex h-full w-full items-center justify-center overflow-hidden px-1 text-center italic text-slate-900"
          style={{ fontFamily: "cursive", fontSize: `${Math.max(9, textSize * 1.2)}px` }}
        >
          {item.typedName || vt.signatureAddition}
        </div>
      ) : null}
      {item.kind === "add_signature" && item.signatureType === "drawn" ? (
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="h-full w-full overflow-visible">
          {(item.signatureStrokes || []).map((stroke, index) => (
            <polyline
              key={`${item.id}-signature-${index}`}
              points={stroke.map((point) => `${point.x},${point.y}`).join(" ")}
              fill="none"
              stroke="#111111"
              strokeWidth="0.012"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}
        </svg>
      ) : null}
      {item.kind === "add_signature" && item.signatureType === "uploaded_image" ? (
        <AssetPreview
          file={item.signatureImageFile}
          alt={vt.signatureAddition}
          className="h-full w-full object-contain"
        />
      ) : null}
      {selected && interactive ? (
        <button
          type="button"
          aria-label="Resize"
          className="absolute bottom-0 right-0 h-5 w-5 cursor-se-resize rounded-tl bg-blue-600 shadow"
          onPointerDown={(event) => {
            event.stopPropagation();
            onResizeStart(event, item);
          }}
        />
      ) : null}
    </div>
  );
}

function EditInspector({
  item,
  vt,
  onUpdate,
  onDelete,
  onDuplicate,
  onPickImage,
  onPickSignatureImage,
}) {
  if (!item) {
    return (
      <section className="rounded-3xl border app-surface-strong p-5">
        <h2 className="text-lg font-semibold app-text">{vt.inspector}</h2>
        <p className="mt-3 rounded-2xl border border-dashed app-surface p-5 text-sm app-text-muted">
          {vt.noSelection}
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-3xl border app-surface-strong p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] app-text-soft">
            {vt.inspector}
          </p>
          <h2 className="mt-1 text-lg font-semibold app-text">{itemLabel(item, vt)}</h2>
        </div>
        <span className="rounded-full border app-surface px-3 py-1 text-xs app-text-muted">
          {vt.currentPage} {item.page_number}
        </span>
      </div>

      {(item.kind === "replace_text" || item.kind === "add_text") && (
        <div className="mt-4 space-y-4">
          <label className="block text-sm font-medium app-text">
            {item.kind === "replace_text" ? vt.replacementText : vt.insertedText}
            <textarea
              value={item.text}
              rows={4}
              autoFocus
              onChange={(event) => onUpdate({ text: event.target.value })}
              className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm font-medium app-text">
              {vt.fontSize}
              <input
                type="number"
                min="4"
                max="96"
                step="1"
                value={item.fontSize}
                onChange={(event) =>
                  onUpdate({ fontSize: clamp(Number(event.target.value) || 12, 4, 96) })
                }
                className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
              />
            </label>
            <label className="text-sm font-medium app-text">
              {vt.color}
              <input
                type="color"
                value={item.colorHex}
                onChange={(event) => onUpdate({ colorHex: event.target.value })}
                className="mt-2 h-12 w-full rounded-2xl border app-surface p-1"
              />
            </label>
          </div>
        </div>
      )}

      {item.kind === "highlight" ? (
        <div className="mt-4 grid grid-cols-2 gap-3">
          <label className="text-sm font-medium app-text">
            {vt.color}
            <input
              type="color"
              value={item.colorHex}
              onChange={(event) => onUpdate({ colorHex: event.target.value })}
              className="mt-2 h-12 w-full rounded-2xl border app-surface p-1"
            />
          </label>
          <label className="text-sm font-medium app-text">
            {vt.opacity}: {Math.round(item.opacity * 100)}%
            <input
              type="range"
              min="5"
              max="100"
              step="5"
              value={Math.round(item.opacity * 100)}
              onChange={(event) => onUpdate({ opacity: Number(event.target.value) / 100 })}
              className="mt-4 w-full"
            />
          </label>
        </div>
      ) : null}

      {item.kind === "draw" ? (
        <div className="mt-4 grid grid-cols-2 gap-3">
          <label className="text-sm font-medium app-text">
            {vt.strokeWidth}
            <input
              type="number"
              min="0.25"
              max="25"
              step="0.25"
              value={item.strokeWidth}
              onChange={(event) =>
                onUpdate({ strokeWidth: clamp(Number(event.target.value) || 2, 0.25, 25) })
              }
              className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
            />
          </label>
          <label className="text-sm font-medium app-text">
            {vt.color}
            <input
              type="color"
              value={item.colorHex}
              onChange={(event) => onUpdate({ colorHex: event.target.value })}
              className="mt-2 h-12 w-full rounded-2xl border app-surface p-1"
            />
          </label>
        </div>
      ) : null}

      {item.kind === "add_image" ? (
        <div className="mt-4">
          <button
            type="button"
            onClick={onPickImage}
            className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border app-surface px-4 py-3 text-sm font-semibold app-text"
          >
            <FileImage className="h-4 w-4" />
            {vt.replaceImage}
          </button>
          {item.assetFile ? (
            <p className="mt-2 truncate text-xs app-text-muted">{item.assetFile.name}</p>
          ) : null}
        </div>
      ) : null}

      {item.kind === "add_signature" ? (
        <div className="mt-4 space-y-4">
          <label className="block text-sm font-medium app-text">
            {vt.signatureType}
            <select
              value={item.signatureType}
              onChange={(event) => onUpdate({ signatureType: event.target.value })}
              className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
            >
              <option value="typed">{vt.typed}</option>
              <option value="drawn">{vt.drawn}</option>
              <option value="uploaded_image">{vt.uploaded}</option>
            </select>
          </label>
          {item.signatureType === "typed" ? (
            <label className="block text-sm font-medium app-text">
              {vt.typedName}
              <input
                value={item.typedName}
                onChange={(event) => onUpdate({ typedName: event.target.value })}
                className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
              />
            </label>
          ) : null}
          {item.signatureType === "drawn" ? (
            <SignaturePad
              strokes={item.signatureStrokes}
              onChange={(signatureStrokes) => onUpdate({ signatureStrokes })}
              vt={vt}
            />
          ) : null}
          {item.signatureType === "uploaded_image" ? (
            <div>
              <button
                type="button"
                onClick={onPickSignatureImage}
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border app-surface px-4 py-3 text-sm font-semibold app-text"
              >
                <FileImage className="h-4 w-4" />
                {vt.chooseSignatureImage}
              </button>
              {item.signatureImageFile ? (
                <p className="mt-2 truncate text-xs app-text-muted">
                  {item.signatureImageFile.name}
                </p>
              ) : null}
            </div>
          ) : null}
          <label className="flex items-start gap-3 rounded-2xl border app-surface p-3 text-sm app-text">
            <input
              type="checkbox"
              checked={item.consentAccepted}
              onChange={(event) => onUpdate({ consentAccepted: event.target.checked })}
              className="mt-1"
            />
            <span>{vt.signatureConsent}</span>
          </label>
        </div>
      ) : null}

      <div className="mt-5 grid grid-cols-2 gap-3">
        <button
          type="button"
          onClick={onDuplicate}
          className="inline-flex items-center justify-center gap-2 rounded-2xl border app-surface px-3 py-3 text-sm font-semibold app-text"
        >
          <Plus className="h-4 w-4" />
          {vt.duplicateEdit}
        </button>
        <button
          type="button"
          onClick={onDelete}
          className="inline-flex items-center justify-center gap-2 rounded-2xl border border-red-400/30 bg-red-400/10 px-3 py-3 text-sm font-semibold text-red-200"
        >
          <Trash2 className="h-4 w-4" />
          {vt.deleteEdit}
        </button>
      </div>
    </section>
  );
}

async function serializeItems(items, vt) {
  const operations = [];
  for (const item of items) {
    const base = {
      page_number: item.page_number,
      rectangle: item.rectangle,
    };
    if (!validRectangle(item.rectangle)) throw new Error(vt.badPlacement);

    if (item.kind === "replace_text") {
      if (!item.text.trim()) throw new Error(vt.noText);
      operations.push({
        ...base,
        operation_id: `${item.id}_erase`,
        operation: "whiteout",
      });
      operations.push({
        ...base,
        operation_id: `${item.id}_text`,
        operation: "add_text",
        text: item.text.trim(),
        font_size: item.fontSize,
        font_family: "Helvetica",
        color_hex: item.colorHex,
      });
      continue;
    }

    if (item.kind === "add_text") {
      if (!item.text.trim()) throw new Error(vt.noText);
      operations.push({
        ...base,
        operation_id: item.id,
        operation: "add_text",
        text: item.text.trim(),
        font_size: item.fontSize,
        font_family: "Helvetica",
        color_hex: item.colorHex,
      });
      continue;
    }

    if (item.kind === "highlight") {
      operations.push({
        ...base,
        operation_id: item.id,
        operation: "highlight",
        color_hex: item.colorHex,
        opacity: item.opacity,
      });
      continue;
    }

    if (item.kind === "whiteout") {
      operations.push({ ...base, operation_id: item.id, operation: "whiteout" });
      continue;
    }

    if (item.kind === "draw") {
      const path = strokesToSvgPath(item.strokes);
      if (!path) throw new Error(vt.noDrawing);
      operations.push({
        ...base,
        operation_id: item.id,
        operation: "draw",
        path_svg: path,
        stroke_width: item.strokeWidth,
        stroke_color_hex: item.colorHex,
      });
      continue;
    }

    if (item.kind === "add_image") {
      if (!item.assetFile) throw new Error(vt.noImage);
      operations.push({
        ...base,
        operation_id: item.id,
        operation: "add_image",
        image_storage_key: `asset:${item.id}`,
        image_mime_type: item.assetFile.type || "image/png",
        _assetFile: item.assetFile,
      });
      continue;
    }

    if (item.kind === "add_signature") {
      if (!item.consentAccepted) throw new Error(vt.noConsent);
      if (item.signatureType === "typed") {
        if (!item.typedName.trim()) throw new Error(vt.noSignature);
        operations.push({
          ...base,
          operation_id: item.id,
          operation: "add_signature",
          signature_type: "typed",
          typed_name: item.typedName.trim(),
          consent_accepted: true,
        });
        continue;
      }
      if (item.signatureType === "drawn") {
        if (!strokesToSvgPath(item.signatureStrokes)) throw new Error(vt.noSignature);
        const file = await strokesToPngFile(item.signatureStrokes, `${item.id}.png`);
        operations.push({
          ...base,
          operation_id: item.id,
          operation: "add_signature",
          signature_type: "drawn",
          signature_svg_storage_key: `asset:${item.id}`,
          consent_accepted: true,
          _assetFile: file,
        });
        continue;
      }
      if (!item.signatureImageFile) throw new Error(vt.noSignature);
      operations.push({
        ...base,
        operation_id: item.id,
        operation: "add_signature",
        signature_type: "uploaded_image",
        signature_image_storage_key: `asset:${item.id}`,
        consent_accepted: true,
        _assetFile: item.signatureImageFile,
      });
    }
  }
  if (operations.length > 500) throw new Error(vt.tooManyOperations);
  if (operations.filter((operation) => operation._assetFile).length > 20) {
    throw new Error(vt.tooManyAssets);
  }
  return operations;
}

export default function EditPdfPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked } = useAccount();
  const t = useMemo(() => copy[language] || copy.en, [language]);
  const vt = useMemo(() => VISUAL_COPY[language] || VISUAL_COPY.en, [language]);

  const fileInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const replaceImageInputRef = useRef(null);
  const signatureImageInputRef = useRef(null);
  const canvasRef = useRef(null);
  const overlayRef = useRef(null);
  const loadingTaskRef = useRef(null);
  const renderTaskRef = useRef(null);
  const interactionRef = useRef(null);
  const pendingImageRef = useRef(null);

  const [file, setFile] = useState(null);
  const [pdfDocument, setPdfDocument] = useState(null);
  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [pageMetrics, setPageMetrics] = useState(null);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState("");

  const [tool, setTool] = useState("select");
  const [items, setItems] = useState([]);
  const itemsRef = useRef([]);
  const [past, setPast] = useState([]);
  const [future, setFuture] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [draftRectangle, setDraftRectangle] = useState(null);
  const [draftStroke, setDraftStroke] = useState([]);

  const [outputFilename, setOutputFilename] = useState("edited-document.pdf");
  const [generatePreview, setGeneratePreview] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);

  const selectedItem = useMemo(
    () => items.find((item) => item.id === selectedId) || null,
    [items, selectedId],
  );
  const currentPageItems = useMemo(
    () => items.filter((item) => item.page_number === currentPage),
    [items, currentPage],
  );

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  const pushHistory = useCallback((snapshot) => {
    setPast((current) => [...current, snapshot].slice(-MAX_HISTORY));
    setFuture([]);
  }, []);

  function setItemsWithoutHistory(updater) {
    setItems((current) => {
      const next = typeof updater === "function" ? updater(current) : updater;
      itemsRef.current = next;
      return next;
    });
  }

  const commitItems = useCallback(
    (updater) => {
      const current = itemsRef.current;
      const next = typeof updater === "function" ? updater(current) : updater;
      if (next === current) return;
      pushHistory(current);
      itemsRef.current = next;
      setItems(next);
    },
    [pushHistory],
  );

  function updateSelected(patch) {
    if (!selectedId) return;
    commitItems((current) =>
      current.map((item) => (item.id === selectedId ? { ...item, ...patch } : item)),
    );
  }

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    commitItems((current) => current.filter((item) => item.id !== selectedId));
    setSelectedId(null);
  }, [commitItems, selectedId]);

  function duplicateSelected() {
    if (!selectedItem) return;
    const duplicate = {
      ...selectedItem,
      id: uid("op"),
      rectangle: fitRectangle({
        ...selectedItem.rectangle,
        x: selectedItem.rectangle.x + 0.02,
        y: selectedItem.rectangle.y + 0.02,
      }),
    };
    commitItems((current) => [...current, duplicate]);
    setSelectedId(duplicate.id);
  }

  const undo = useCallback(() => {
    if (!past.length) return;
    const previous = past[past.length - 1];
    setPast(past.slice(0, -1));
    setFuture([itemsRef.current, ...future].slice(0, MAX_HISTORY));
    itemsRef.current = previous;
    setItems(previous);
    setSelectedId(null);
  }, [future, past]);

  const redo = useCallback(() => {
    if (!future.length) return;
    const next = future[0];
    setPast([...past, itemsRef.current].slice(-MAX_HISTORY));
    setFuture(future.slice(1));
    itemsRef.current = next;
    setItems(next);
    setSelectedId(null);
  }, [future, past]);

  useEffect(() => {
    function onKeyDown(event) {
      const target = event.target;
      const editingField =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        target?.isContentEditable;
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (!editingField && (event.key === "Delete" || event.key === "Backspace")) {
        if (selectedId) {
          event.preventDefault();
          deleteSelected();
        }
      }
      if (event.key === "Escape") {
        setTool("select");
        setDraftRectangle(null);
        setDraftStroke([]);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteSelected, redo, selectedId, undo]);

  useEffect(() => {
    let cancelled = false;
    async function loadPdf() {
      if (!file) {
        setPdfDocument(null);
        setPageCount(0);
        setPageMetrics(null);
        return;
      }
      setPdfLoading(true);
      setPdfError("");
      try {
        const pdfjs = await import("pdfjs-dist/webpack.mjs");
        const bytes = new Uint8Array(await file.arrayBuffer());
        const loadingTask = pdfjs.getDocument({ data: bytes });
        loadingTaskRef.current = loadingTask;
        const documentProxy = await loadingTask.promise;
        if (cancelled) {
          await documentProxy.destroy();
          return;
        }
        setPdfDocument(documentProxy);
        setPageCount(documentProxy.numPages);
        setCurrentPage(1);
      } catch (caught) {
        if (!cancelled) {
          setPdfError(caught?.message || vt.renderFailed);
          setPdfDocument(null);
          setPageCount(0);
        }
      } finally {
        if (!cancelled) setPdfLoading(false);
      }
    }
    loadPdf();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel?.();
      loadingTaskRef.current?.destroy?.();
    };
  }, [file, vt.renderFailed]);

  useEffect(() => {
    let cancelled = false;
    async function renderPage() {
      if (!pdfDocument || !canvasRef.current) return;
      renderTaskRef.current?.cancel?.();
      try {
        const page = await pdfDocument.getPage(currentPage);
        const unitViewport = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: BASE_RENDER_SCALE * zoom });
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d", { alpha: false });
        const outputScale = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        const renderTask = page.render({
          canvasContext: context,
          viewport,
          transform:
            outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
        });
        renderTaskRef.current = renderTask;
        await renderTask.promise;
        if (!cancelled) {
          setPageMetrics({
            width: viewport.width,
            height: viewport.height,
            pdfWidth: unitViewport.width,
            pdfHeight: unitViewport.height,
          });
        }
      } catch (caught) {
        if (!cancelled && caught?.name !== "RenderingCancelledException") {
          setPdfError(caught?.message || vt.renderFailed);
        }
      }
    }
    renderPage();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel?.();
    };
  }, [pdfDocument, currentPage, zoom, pdfLoading, vt.renderFailed]);

  async function validateImage(nextFile) {
    if (!nextFile) return null;
    const securityError = await validateBrowserUpload(
      nextFile,
      FILE_SECURITY_POLICY.pdfEditImage,
    );
    if (securityError) {
      setError(securityError);
      return null;
    }
    setError("");
    return nextFile;
  }

  async function handlePickedPdfFile(nextFile) {
    if (!nextFile) return;
    const securityError = await validateBrowserUpload(
      nextFile,
      FILE_SECURITY_POLICY.pdfTool,
    );
    if (securityError) {
      setError(securityError);
      return;
    }
    setError("");
    setResponse(null);
    itemsRef.current = [];
    setItems([]);
    setPast([]);
    setFuture([]);
    setSelectedId(null);
    setFile(nextFile);
    setOutputFilename(
      normalizePdfFilename(`${nextFile.name.replace(/\.pdf$/i, "")}-edited`, "edited-document.pdf"),
    );
  }

  function removePdf() {
    if (items.length && !window.confirm(vt.removeFileConfirm)) return;
    setFile(null);
    setPdfDocument(null);
    setPageCount(0);
    itemsRef.current = [];
    setItems([]);
    setPast([]);
    setFuture([]);
    setSelectedId(null);
    setResponse(null);
    setPdfError("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function chooseTool(nextTool) {
    setError("");
    setSelectedId(null);
    if (nextTool !== "add_image") pendingImageRef.current = null;
    if (nextTool === "add_image") {
      imageInputRef.current?.click();
      return;
    }
    setTool(nextTool);
  }

  async function handlePendingImage(nextFile) {
    const validated = await validateImage(nextFile);
    if (imageInputRef.current) imageInputRef.current.value = "";
    if (!validated) return;
    pendingImageRef.current = validated;
    setTool("add_image");
  }

  function beginPageInteraction(event) {
    if (!overlayRef.current || event.target !== overlayRef.current || tool === "select") return;
    event.preventDefault();
    overlayRef.current.setPointerCapture(event.pointerId);
    const point = normalizedPoint(event, overlayRef.current);
    interactionRef.current = {
      type: tool === "draw" ? "draw-new" : "rect-new",
      start: point,
      last: point,
    };
    if (tool === "draw") setDraftStroke([point]);
    else setDraftRectangle({ x: point.x, y: point.y, width: 0, height: 0 });
  }

  function beginItemInteraction(event, item, mode) {
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const point = normalizedPoint(event, overlayRef.current);
    interactionRef.current = {
      type: mode,
      itemId: item.id,
      start: point,
      original: item.rectangle,
      snapshot: items,
    };
  }

  function movePageInteraction(event) {
    const interaction = interactionRef.current;
    if (!interaction || !overlayRef.current) return;
    event.preventDefault();
    const point = normalizedPoint(event, overlayRef.current);

    if (interaction.type === "rect-new") {
      setDraftRectangle(rectangleFromPoints(interaction.start, point));
      return;
    }
    if (interaction.type === "draw-new") {
      setDraftStroke((current) => [...current, point]);
      return;
    }
    if (interaction.type === "move") {
      const deltaX = point.x - interaction.start.x;
      const deltaY = point.y - interaction.start.y;
      const nextRectangle = fitRectangle({
        ...interaction.original,
        x: interaction.original.x + deltaX,
        y: interaction.original.y + deltaY,
      });
      setItemsWithoutHistory((current) =>
        current.map((item) =>
          item.id === interaction.itemId ? { ...item, rectangle: nextRectangle } : item,
        ),
      );
      return;
    }
    if (interaction.type === "resize") {
      const nextRectangle = fitRectangle({
        ...interaction.original,
        width: interaction.original.width + point.x - interaction.start.x,
        height: interaction.original.height + point.y - interaction.start.y,
      });
      setItemsWithoutHistory((current) =>
        current.map((item) =>
          item.id === interaction.itemId ? { ...item, rectangle: nextRectangle } : item,
        ),
      );
    }
  }

  function finishPageInteraction(event) {
    const interaction = interactionRef.current;
    if (!interaction) return;
    interactionRef.current = null;
    if (event.currentTarget?.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    if (interaction.type === "rect-new") {
      const endPoint = overlayRef.current
        ? normalizedPoint(event, overlayRef.current)
        : interaction.start;
      const rectangle = rectangleFromPoints(interaction.start, endPoint);
      setDraftRectangle(null);
      if (!validRectangle(rectangle)) {
        setError(vt.badPlacement);
        return;
      }
      const assetFile = tool === "add_image" ? pendingImageRef.current : null;
      const item = makeItem(tool, currentPage, rectangle, assetFile);
      pendingImageRef.current = null;
      commitItems((current) => [...current, item]);
      setSelectedId(item.id);
      setTool("select");
      return;
    }

    if (interaction.type === "draw-new") {
      const endPoint = overlayRef.current
        ? normalizedPoint(event, overlayRef.current)
        : interaction.start;
      const drawing = drawingFromPagePoints([...draftStroke, endPoint]);
      setDraftStroke([]);
      if (!drawing) {
        setError(vt.noDrawing);
        return;
      }
      const item = {
        ...makeItem("draw", currentPage, drawing.rectangle),
        strokes: drawing.strokes,
        strokeWidth: 2,
        colorHex: "#111111",
      };
      commitItems((current) => [...current, item]);
      setSelectedId(item.id);
      setTool("select");
      return;
    }

    if (interaction.snapshot) pushHistory(interaction.snapshot);
  }

  function clearAllEdits() {
    if (!items.length) return;
    if (!window.confirm(vt.clearConfirm)) return;
    commitItems([]);
    setSelectedId(null);
  }

  function goToPage(nextPage) {
    setCurrentPage(clamp(nextPage, 1, pageCount || 1));
    setSelectedId(null);
    setDraftRectangle(null);
    setDraftStroke([]);
  }

  async function replaceSelectedImage(nextFile) {
    const validated = await validateImage(nextFile);
    if (validated) updateSelected({ assetFile: validated });
    if (replaceImageInputRef.current) replaceImageInputRef.current.value = "";
  }

  async function replaceSignatureImage(nextFile) {
    const validated = await validateImage(nextFile);
    if (validated) updateSelected({ signatureImageFile: validated });
    if (signatureImageInputRef.current) signatureImageInputRef.current.value = "";
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResponse(null);
    if (!file) {
      setError(t.noFile);
      return;
    }
    if (!items.length) {
      setError(t.noOperations);
      return;
    }

    setBusy(true);
    try {
      const operations = await serializeItems(items, vt);
      const formData = new FormData();
      formData.append("file", file);
      formData.append(
        "operations_json",
        JSON.stringify(operations.map(({ _assetFile, ...operation }) => operation)),
      );
      for (const operation of operations) {
        if (operation._assetFile) {
          formData.append(
            "edit_assets",
            operation._assetFile,
            assetFilename(operation.operation_id, operation._assetFile),
          );
        }
      }
      formData.append(
        "output_filename",
        normalizePdfFilename(outputFilename, "edited-document.pdf"),
      );
      formData.append("generate_preview", String(generatePreview));
      formData.append("system_language", systemLanguageFor(language));
      setResponse(await postAnalyzerFeature(FEATURE_PATH, formData, true));
    } catch (caught) {
      setError(caught?.message || t.failed);
    } finally {
      setBusy(false);
    }
  }

  const result = response?.result || null;
  const outputUrl = normalizeArtifactUrl(
    result?.download_url || result?.pdf_artifact?.download_url || result?.file?.download_url,
  );
  const previewUrl = normalizeArtifactUrl(
    result?.preview?.download_url || result?.preview_pdf?.download_url,
  );

  if (!authChecked) {
    return (
      <AppSidebarLayout>
        <main className="app-page min-h-screen p-6 app-text">{t.loading}</main>
      </AppSidebarLayout>
    );
  }

  if (!user) {
    return (
      <AppSidebarLayout>
        <main className="app-page min-h-screen p-6">
          <AuthRequired t={t} />
        </main>
      </AppSidebarLayout>
    );
  }

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

        <section className="mb-6 rounded-3xl border app-surface-strong p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">
            {t.badge}
          </p>
          <h1 className="mt-3 text-3xl font-semibold app-text md:text-4xl">{t.title}</h1>
          <p className="mt-3 max-w-3xl app-text-muted">{vt.editorHelp}</p>
          <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3 py-1 text-xs text-emerald-200">
            <CheckCircle2 className="h-4 w-4" />
            {vt.hiddenCoordinates}
          </div>
        </section>

        {!file ? (
          <section className="mx-auto max-w-3xl rounded-3xl border app-surface-strong p-6">
            <h2 className="text-xl font-semibold app-text">1. {t.uploadTitle}</h2>
            <p className="mt-2 text-sm app-text-muted">{t.uploadHelp}</p>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="mt-5 flex w-full flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-12 text-center transition hover:border-blue-400/60"
            >
              <UploadCloud className="h-12 w-12 app-text-muted" />
              <span className="mt-4 text-base font-semibold app-text">{t.chooseFile}</span>
              <span className="mt-2 text-xs app-text-muted">PDF · 50 MB maximum</span>
            </button>
          </section>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-6">
            <section className="rounded-3xl border app-surface-strong p-4 md:p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] app-text-soft">
                    {vt.selectedFile}
                  </p>
                  <p className="mt-1 truncate text-sm font-semibold app-text">{file.name}</p>
                </div>
                <button
                  type="button"
                  onClick={removePdf}
                  className="inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-2 text-xs font-semibold app-text"
                >
                  <RotateCcw className="h-4 w-4" />
                  {vt.changePdf}
                </button>
              </div>
            </section>

            <section className="overflow-hidden rounded-3xl border app-surface-strong">
              <div className="border-b p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] app-text-soft">
                      2. {vt.visualWorkflow}
                    </p>
                    <h2 className="mt-1 text-xl font-semibold app-text">{vt.editorTitle}</h2>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={undo}
                      disabled={!past.length}
                      title={vt.undo}
                      className="rounded-xl border app-surface p-2.5 app-text disabled:opacity-35"
                    >
                      <Undo2 className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={redo}
                      disabled={!future.length}
                      title={vt.redo}
                      className="rounded-xl border app-surface p-2.5 app-text disabled:opacity-35"
                    >
                      <Redo2 className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={clearAllEdits}
                      disabled={!items.length}
                      title={vt.clearAll}
                      className="rounded-xl border border-red-400/20 bg-red-400/5 p-2.5 text-red-200 disabled:opacity-35"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap gap-2">
                  {TOOL_DEFINITIONS.map(({ id, label, icon: Icon }) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => chooseTool(id)}
                      className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-sm font-semibold transition ${
                        tool === id
                          ? "border-blue-400 bg-blue-500/15 text-blue-100"
                          : "app-surface app-text"
                      }`}
                    >
                      <Icon className="h-4 w-4" />
                      {vt[label]}
                    </button>
                  ))}
                </div>
                <p className="mt-3 text-sm app-text-muted">{toolInstruction(tool, vt)}</p>
              </div>

              <div className="grid min-h-[720px] lg:grid-cols-[minmax(0,1fr)_340px]">
                <div className="min-w-0 border-b lg:border-b-0 lg:border-r">
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b p-3">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => goToPage(currentPage - 1)}
                        disabled={currentPage <= 1}
                        title={vt.previousPage}
                        className="rounded-xl border app-surface p-2 app-text disabled:opacity-35"
                      >
                        <ChevronLeft className="h-4 w-4" />
                      </button>
                      <span className="min-w-28 text-center text-sm font-semibold app-text">
                        {vt.currentPage} {currentPage} / {pageCount || "—"}
                      </span>
                      <button
                        type="button"
                        onClick={() => goToPage(currentPage + 1)}
                        disabled={!pageCount || currentPage >= pageCount}
                        title={vt.nextPage}
                        className="rounded-xl border app-surface p-2 app-text disabled:opacity-35"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setZoom((value) => clamp(value - 0.1, 0.6, 2.2))}
                        title={vt.zoomOut}
                        className="rounded-xl border app-surface p-2 app-text"
                      >
                        <ZoomOut className="h-4 w-4" />
                      </button>
                      <span className="min-w-14 text-center text-xs font-semibold app-text-muted">
                        {Math.round(zoom * 100)}%
                      </span>
                      <button
                        type="button"
                        onClick={() => setZoom((value) => clamp(value + 0.1, 0.6, 2.2))}
                        title={vt.zoomIn}
                        className="rounded-xl border app-surface p-2 app-text"
                      >
                        <ZoomIn className="h-4 w-4" />
                      </button>
                    </div>
                  </div>

                  <div className="h-[650px] overflow-auto bg-slate-950/25 p-5">
                    {pdfLoading ? (
                      <div className="flex h-full items-center justify-center gap-3 text-sm app-text-muted">
                        <Loader2 className="h-5 w-5 animate-spin" />
                        {vt.loadingPdf}
                      </div>
                    ) : pdfError ? (
                      <div className="mx-auto mt-16 max-w-xl rounded-2xl border border-red-400/30 bg-red-400/10 p-5 text-sm text-red-200">
                        <AlertTriangle className="mr-2 inline h-4 w-4" />
                        {pdfError}
                      </div>
                    ) : (
                      <div
                        className="relative mx-auto bg-white shadow-2xl"
                        style={{
                          width: pageMetrics ? `${pageMetrics.width}px` : undefined,
                          height: pageMetrics ? `${pageMetrics.height}px` : undefined,
                        }}
                      >
                        <canvas ref={canvasRef} className="block" aria-label={`PDF page ${currentPage}`} />
                        {pageMetrics ? (
                          <div
                            ref={overlayRef}
                            className={`absolute inset-0 touch-none ${
                              tool === "select" ? "cursor-default" : "cursor-crosshair"
                            }`}
                            onPointerDown={beginPageInteraction}
                            onPointerMove={movePageInteraction}
                            onPointerUp={finishPageInteraction}
                            onPointerCancel={finishPageInteraction}
                          >
                            {currentPageItems.map((item) => (
                              <OverlayItem
                                key={item.id}
                                item={item}
                                selected={item.id === selectedId}
                                interactive={tool === "select"}
                                pageMetrics={pageMetrics}
                                onSelect={setSelectedId}
                                onMoveStart={(event, selected) =>
                                  beginItemInteraction(event, selected, "move")
                                }
                                onResizeStart={(event, selected) =>
                                  beginItemInteraction(event, selected, "resize")
                                }
                                vt={vt}
                              />
                            ))}
                            {draftRectangle ? (
                              <div
                                className="pointer-events-none absolute border-2 border-dashed border-blue-500 bg-blue-500/10"
                                style={{
                                  left: `${draftRectangle.x * 100}%`,
                                  top: `${draftRectangle.y * 100}%`,
                                  width: `${draftRectangle.width * 100}%`,
                                  height: `${draftRectangle.height * 100}%`,
                                }}
                              />
                            ) : null}
                            {draftStroke.length > 1 ? (
                              <svg
                                viewBox="0 0 1 1"
                                preserveAspectRatio="none"
                                className="pointer-events-none absolute inset-0 h-full w-full"
                              >
                                <polyline
                                  points={draftStroke.map((point) => `${point.x},${point.y}`).join(" ")}
                                  fill="none"
                                  stroke="#2563eb"
                                  strokeWidth="0.004"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                />
                              </svg>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    )}
                  </div>
                </div>

                <aside className="space-y-4 p-4">
                  <EditInspector
                    item={selectedItem}
                    vt={vt}
                    onUpdate={updateSelected}
                    onDelete={deleteSelected}
                    onDuplicate={duplicateSelected}
                    onPickImage={() => replaceImageInputRef.current?.click()}
                    onPickSignatureImage={() => signatureImageInputRef.current?.click()}
                  />

                  <section className="rounded-3xl border app-surface-strong p-5">
                    <div className="flex items-center justify-between gap-3">
                      <h2 className="text-lg font-semibold app-text">{vt.edits}</h2>
                      <span className="rounded-full border app-surface px-3 py-1 text-xs app-text-muted">
                        {items.length} {vt.editCount}
                      </span>
                    </div>
                    {items.length ? (
                      <div className="mt-4 max-h-72 space-y-2 overflow-auto pr-1">
                        {items.map((item, index) => (
                          <button
                            key={item.id}
                            type="button"
                            onClick={() => {
                              goToPage(item.page_number);
                              setSelectedId(item.id);
                              setTool("select");
                            }}
                            className={`flex w-full items-center justify-between gap-3 rounded-2xl border p-3 text-left ${
                              selectedId === item.id
                                ? "border-blue-400 bg-blue-500/10"
                                : "app-surface"
                            }`}
                          >
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-semibold app-text">
                                {index + 1}. {itemLabel(item, vt)}
                              </span>
                              <span className="mt-1 block text-xs app-text-muted">
                                {vt.currentPage} {item.page_number}
                              </span>
                            </span>
                            <ChevronRight className="h-4 w-4 shrink-0 app-text-muted" />
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-4 rounded-2xl border border-dashed app-surface p-4 text-sm app-text-muted">
                        {vt.noEdits}
                      </p>
                    )}
                  </section>
                </aside>
              </div>
            </section>

            <section className="grid gap-6 lg:grid-cols-[1fr_0.8fr]">
              <div className="rounded-3xl border app-surface-strong p-5">
                <h2 className="text-lg font-semibold app-text">3. {t.outputSettings}</h2>
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
              </div>

              <div className="rounded-3xl border app-surface-strong p-5">
                <h2 className="text-lg font-semibold app-text">4. {t.edit}</h2>
                <p className="mt-2 text-sm app-text-muted">{vt.readyToProcess}</p>
                <p className="mt-3 rounded-2xl border border-amber-400/25 bg-amber-400/10 p-3 text-xs text-amber-100">
                  {vt.processingNote}
                </p>
                {error ? (
                  <p className="mt-4 rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">
                    <AlertTriangle className="mr-2 inline h-4 w-4" />
                    {error}
                  </p>
                ) : null}
                <button
                  type="submit"
                  disabled={busy || pdfLoading || Boolean(pdfError)}
                  className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-60"
                >
                  {busy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <FilePenLine className="h-4 w-4" />
                  )}
                  {busy ? t.editing : t.edit}
                </button>
              </div>
            </section>

            {result ? (
              <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5">
                <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
                  <CheckCircle2 className="h-5 w-5" />
                  {t.resultTitle}
                </h2>
                <p className="mt-3 text-sm app-text-muted">
                  {t.requested}: {result.operations_requested ?? items.length} · {t.applied}:{" "}
                  {result.operations_applied ?? "—"}
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
                    className="mt-5 h-[620px] w-full rounded-2xl border bg-white"
                  />
                ) : null}
              </section>
            ) : null}
          </form>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(event) => handlePickedPdfFile(event.target.files?.[0] || null)}
        />
        <input
          ref={imageInputRef}
          type="file"
          accept="image/png,image/jpeg,.png,.jpg,.jpeg"
          className="hidden"
          onChange={(event) => handlePendingImage(event.target.files?.[0] || null)}
        />
        <input
          ref={replaceImageInputRef}
          type="file"
          accept="image/png,image/jpeg,.png,.jpg,.jpeg"
          className="hidden"
          onChange={(event) => replaceSelectedImage(event.target.files?.[0] || null)}
        />
        <input
          ref={signatureImageInputRef}
          type="file"
          accept="image/png,image/jpeg,.png,.jpg,.jpeg"
          className="hidden"
          onChange={(event) => replaceSignatureImage(event.target.files?.[0] || null)}
        />
      </main>
    </AppSidebarLayout>
  );
}
