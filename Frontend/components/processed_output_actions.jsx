"use client";

/**
 * Compatibility shim.
 *
 * Print and share behavior is intentionally implemented by each feature page.
 * Keeping this component as a no-op prevents stale imports from breaking the
 * application while avoiding duplicate print/share orchestration and side
 * effects at the shared-component level.
 */
export default function ProcessedOutputActions() {
  return null;
}