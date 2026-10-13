// This worker belongs only to Edit PDF. Keep the API, reviewed worker and
// published asset in sync when reviewing a future pdfjs-dist update.
export const EDIT_PDF_PDFJS_VERSION = "6.1.200";
export const EDIT_PDF_WORKER_URL =
  "/redocx-edit-pdf/pdf.worker-6.1.200-unicode-8eb8165cad1fe12b.mjs";

const WORKER_CLEANUP_TIMEOUT_MS = 1000;

function abortedError() {
  return new DOMException("PDF preview was cancelled.", "AbortError");
}

function rejectionGate() {
  let reject;
  const promise = new Promise((_, rejectPromise) => { reject = rejectPromise; });
  return { promise, reject };
}

export function createEditPdfDocumentSession(data, { onWorkerError } = {}) {
  let loadingTask = null;
  let pdfWorker = null;
  let workerPort = null;
  let disposed = false;
  let opened = false;
  let disposal = null;
  // Create these before importing the SDK without requiring its polyfills.
  const cancellation = rejectionGate();
  const workerFailure = rejectionGate();

  function handleWorkerError(event) {
    if (disposed) return;
    event?.preventDefault?.();
    const error = new Error("The PDF preview worker could not continue.");
    workerFailure.reject(error);
    try {
      if (opened) onWorkerError?.(error);
    } finally {
      void destroy();
    }
  }

  function destroy() {
    if (disposal) return disposal;
    disposed = true;
    cancellation.reject(abortedError());
    disposal = (async () => {
      let timer = null;
      try {
        if (loadingTask) {
          // A dead worker cannot acknowledge Terminate. Bound that wait so
          // every owned native worker is eventually released, including
          // cancellation while PDF.js 6 is still setting up the document.
          await Promise.race([
            Promise.resolve().then(() => loadingTask.destroy()).catch(() => {}),
            new Promise((resolve) => {
              timer = setTimeout(resolve, WORKER_CLEANUP_TIMEOUT_MS);
            }),
          ]);
        }
      } finally {
        if (timer !== null) clearTimeout(timer);
        workerPort?.removeEventListener("error", handleWorkerError);
        workerPort?.removeEventListener("messageerror", handleWorkerError);
        try {
          pdfWorker?.destroy();
        } finally {
          // PDFWorker does not terminate a caller-supplied port.
          workerPort?.terminate();
          loadingTask = null;
          pdfWorker = null;
          workerPort = null;
        }
      }
    })();
    return disposal;
  }

  async function open() {
    const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
    if (disposed) throw abortedError();
    if (pdfjs.version !== EDIT_PDF_PDFJS_VERSION) {
      throw new Error("The PDF preview API and reviewed worker versions differ.");
    }
    workerPort = new Worker(EDIT_PDF_WORKER_URL, {
      type: "module",
      name: "redocx-edit-pdf",
    });
    workerPort.addEventListener("error", handleWorkerError);
    workerPort.addEventListener("messageerror", handleWorkerError);
    pdfWorker = pdfjs.PDFWorker.create({ port: workerPort });
    loadingTask = pdfjs.getDocument({ data, worker: pdfWorker });
    const documentProxy = await loadingTask.promise;
    if (disposed) throw abortedError();
    opened = true;
    return documentProxy;
  }

  const promise = Promise.race([
    open(), cancellation.promise, workerFailure.promise,
  ]).catch(async (error) => {
    await destroy();
    throw error;
  });

  return { promise, destroy };
}
