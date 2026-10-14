"use client";

import { useEffect, useRef, useState } from "react";
import { normalizeAnalyzerArtifactUrl, buildAnalyzerArtifactUrl } from "@/lib/api_client";
import { createEditPdfDocumentSession } from "@/lib/edit_pdf_document";

export default function EditPdfReview({ open, result, copy, onClose, onDownload }) {
  const dialogRef = useRef(null);
  const canvasRef = useRef(null);
  const [file, setFile] = useState(null);
  const [documentProxy, setDocumentProxy] = useState(null);
  const [verifiedResult, setVerifiedResult] = useState(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [renderedPage, setRenderedPage] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    if (!open || !result) {
      setFile(null); setDocumentProxy(null); setVerifiedResult(null); setRenderedPage(0);
      return undefined;
    }
    const controller = new AbortController();
    let session = null;
    setFile(null); setDocumentProxy(null); setVerifiedResult(null); setPageNumber(1); setRenderedPage(0); setError("");
    async function load() {
      try {
        const artifact = result.preview || result;
        const artifactUrl = normalizeAnalyzerArtifactUrl(artifact.download_url) || buildAnalyzerArtifactUrl(artifact.storage_key);
        if (!artifactUrl) throw new Error("preview unavailable");
        const response = await fetch(artifactUrl, { credentials: "include", cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("preview unavailable");
        const bytes = await response.arrayBuffer();
        if (!bytes.byteLength || bytes.byteLength > 250 * 1024 * 1024 ||
          new TextDecoder().decode(bytes.slice(0, 5)) !== "%PDF-") throw new Error("invalid PDF preview");
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const hash = Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, "0")).join("");
        if (hash !== result.output_checksum_sha256) throw new Error("preview differs from output");
        if (controller.signal.aborted) return;
        // File owns its bytes before PDF.js transfers the buffer to its worker.
        const verified = new File([bytes], result.filename || "edited-document.pdf", { type: "application/pdf" });
        session = createEditPdfDocumentSession(new Uint8Array(bytes), {
          onWorkerError() {
            if (!controller.signal.aborted) { setRenderedPage(0); setError(copy.renderFailed); }
          },
        });
        const pdf = await session.promise;
        if (controller.signal.aborted) return;
        if (pdf.numPages !== result.page_count) throw new Error("preview page count differs from output");
        setFile(verified); setDocumentProxy(pdf); setVerifiedResult(result);
      } catch (caught) {
        if (!controller.signal.aborted) setError(copy.renderFailed);
      }
    }
    void load();
    return () => { controller.abort(); void session?.destroy(); };
  }, [open, result, copy.renderFailed]);

  useEffect(() => {
    if (!open || !documentProxy || verifiedResult !== result || !canvasRef.current) return undefined;
    let cancelled = false;
    let renderTask = null;
    setRenderedPage(0);
    async function render() {
      try {
        const page = await documentProxy.getPage(pageNumber);
        if (cancelled) return;
        const unit = page.getViewport({ scale: 1 });
        const scale = Math.min(1.5, 1100 / unit.width, 1600 / unit.height);
        const viewport = page.getViewport({ scale });
        const pixels = Math.min(2, window.devicePixelRatio || 1);
        const buffer = document.createElement("canvas");
        buffer.width = Math.ceil(viewport.width * pixels);
        buffer.height = Math.ceil(viewport.height * pixels);
        renderTask = page.render({ canvasContext:buffer.getContext("2d", { alpha:false }), viewport,
          transform:pixels === 1 ? null : [pixels,0,0,pixels,0,0] });
        await renderTask.promise;
        if (cancelled || !canvasRef.current) return;
        const canvas = canvasRef.current;
        canvas.width = buffer.width; canvas.height = buffer.height;
        canvas.style.width = `${viewport.width}px`;
        canvas.getContext("2d", { alpha:false }).drawImage(buffer,0,0);
        setRenderedPage(pageNumber);
      } catch (caught) {
        if (!cancelled && caught?.name !== "RenderingCancelledException") setError(copy.renderFailed);
      }
    }
    void render();
    return () => { cancelled = true; renderTask?.cancel?.(); };
  }, [open, documentProxy, verifiedResult, result, pageNumber, copy.renderFailed]);

  const ready = Boolean(file && verifiedResult === result && renderedPage === pageNumber && !error);
  return <dialog ref={dialogRef} aria-labelledby="edit-pdf-review-title"
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    className="fixed inset-0 m-auto h-[92vh] w-[min(1200px,96vw)] max-w-none rounded-2xl border app-surface-strong p-0 app-text shadow-2xl backdrop:bg-black/70">
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div><h2 id="edit-pdf-review-title" className="font-semibold">{copy.previewTitle}</h2>
          <p className="mt-1 text-xs app-text-muted">{copy.previewHelp}</p></div>
        <button type="button" autoFocus onClick={onClose} className="rounded-xl border app-surface px-3 py-2 text-sm">{copy.returnToEdit}</button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto bg-slate-900/20 p-4" aria-busy={!ready && !error}>
        {error ? <p role="alert" className="m-5 rounded-xl border border-red-400/40 p-4 text-red-300">{error}</p> : null}
        {!ready && !error ? <p role="status" className="p-6 text-sm app-text-muted">{copy.loadingPdf}</p> : null}
        <canvas ref={canvasRef} role="img" aria-label={`${copy.previewTitle} · ${copy.reviewPage} ${pageNumber}`}
          className="mx-auto block bg-white" style={{ maxWidth:"100%", height:"auto", display:ready ? "block" : "none" }} />
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t p-4">
        <div className="flex items-center gap-2">
          <button type="button" aria-label={copy.previousPage} disabled={!documentProxy || pageNumber <= 1}
            onClick={() => setPageNumber((number) => number-1)} className="rounded-xl border app-surface px-3 py-2 text-sm">←</button>
          <span className="text-sm">{copy.reviewPage} {pageNumber} / {documentProxy?.numPages || result?.page_count || "…"}</span>
          <button type="button" aria-label={copy.nextPage} disabled={!documentProxy || pageNumber >= documentProxy.numPages}
            onClick={() => setPageNumber((number) => number+1)} className="rounded-xl border app-surface px-3 py-2 text-sm">→</button>
        </div>
        <div className="flex items-center gap-3">
          <button type="button" onClick={onClose} className="rounded-xl border app-surface px-3 py-2 text-sm">{copy.returnToEdit}</button>
          <button type="button" disabled={!ready} onClick={() => onDownload(file)}
            className="rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-40">{copy.approve}</button>
        </div>
      </footer>
    </div>
  </dialog>;
}
