// Source typography and document editing helpers. Used only by Edit PDF.
export const SOURCE_TEXT_KINDS = new Set(["source_text", "source_insert"]);

const COPY = {
  en: {
    editText: "Edit document text", instruction: "Click a paragraph and type directly on the page. Click empty space to insert text in the document’s style.",
    sourceStyle: "Document style", automatic: "Detected automatically", matchingFont: "Source font", size: "Size", spacing: "Line spacing",
    preserve: "Font, size, direction and margins follow the source document.",
    preparing: "Reading the document’s fonts and layout…", retry: "Retry document analysis", readonly: "This paragraph is protected",
    preview: "Preview changes", previewHelp: "Review the generated PDF before downloading. Its rendering is authoritative.",
    previewTitle: "Review edited document", returnToEdit: "Continue editing", approve: "Download reviewed PDF",
    reviewPage: "Page", previousPage: "Previous page", nextPage: "Next page",
    editedText: "Edited paragraph", insertedText: "Inserted text", restored: "Restore original", directText: "Document text",
    insertPlaceholder: "Type here", noSourceText: "This page has no safely editable text. Images and other document tools remain available.",
    liveDraft: "Editing draft", pendingPreview: "Generate a preview to verify the final layout.",
    SOURCE_INSPECTION_BUSY: "Document analysis is busy. Wait a few seconds and retry.",
    SOURCE_RESOURCE_LIMIT: "This page exceeds the safe limits for direct text editing.",
    SOURCE_COMPLEX_LAYOUT: "This paragraph uses a font or layout that cannot be rewritten faithfully. Its original content has been preserved.",
    SOURCE_FONT_UNAVAILABLE: "The document’s exact font is unavailable for this paragraph. ReDOCX will not substitute a different font.",
    SOURCE_FONT_MISSING_GLYPH: "The embedded font does not include one of the new characters. Use characters supported by this font; no replacement font was used.",
    SOURCE_TEXT_OVERFLOW: "The text no longer fits at the document’s font size and margins. Shorten it before previewing.",
    SOURCE_PROTECTED_OBJECT: "This text overlaps a link, form field, annotation or digital signature. Use an unsigned original or edit the source document.",
    SOURCE_OVERLAPPING_TEXT: "Overlapping text cannot be separated safely. The source has been preserved.",
    SOURCE_DOCUMENT_CHANGED: "The source document or selected paragraph changed. Reopen the document and try again.",
    SOURCE_TEXT_UNCHANGED: "Make a change on the document before generating a preview.",
    SOURCE_UNSAFE_TEXT: "This text contains an unsupported control character.",
  },
  fr: {
    editText: "Modifier le texte du document", instruction: "Cliquez sur un paragraphe et écrivez directement sur la page. Cliquez dans un espace vide pour insérer du texte dans le style du document.",
    sourceStyle: "Style du document", automatic: "Détecté automatiquement", matchingFont: "Police d’origine", size: "Taille", spacing: "Interligne",
    preserve: "La police, la taille, l’orientation et les marges suivent le document d’origine.",
    preparing: "Analyse des polices et de la mise en page…", retry: "Réessayer l’analyse", readonly: "Ce paragraphe est protégé",
    preview: "Prévisualiser les modifications", previewHelp: "Vérifiez le PDF généré avant de le télécharger. Son rendu fait référence.",
    previewTitle: "Vérifier le document modifié", returnToEdit: "Continuer la modification", approve: "Télécharger le PDF vérifié",
    reviewPage: "Page", previousPage: "Page précédente", nextPage: "Page suivante",
    editedText: "Paragraphe modifié", insertedText: "Texte inséré", restored: "Rétablir l’original", directText: "Texte du document",
    insertPlaceholder: "Écrivez ici", noSourceText: "Cette page ne contient pas de texte modifiable en toute sécurité. Les images et les autres outils restent disponibles.",
    liveDraft: "Brouillon en cours", pendingPreview: "Générez un aperçu pour vérifier la mise en page finale.",
    SOURCE_INSPECTION_BUSY: "L’analyse du document est occupée. Patientez quelques secondes puis réessayez.",
    SOURCE_RESOURCE_LIMIT: "Cette page dépasse les limites de sécurité pour la modification directe du texte.",
    SOURCE_COMPLEX_LAYOUT: "Ce paragraphe utilise une police ou une mise en page qui ne peut pas être reproduite fidèlement. Son contenu original est conservé.",
    SOURCE_FONT_UNAVAILABLE: "La police exacte du document est indisponible pour ce paragraphe. ReDOCX ne la remplacera pas par une autre police.",
    SOURCE_FONT_MISSING_GLYPH: "La police intégrée ne contient pas l’un des nouveaux caractères. Utilisez des caractères pris en charge ; aucune autre police n’a été utilisée.",
    SOURCE_TEXT_OVERFLOW: "Le texte ne tient plus avec la taille et les marges du document. Raccourcissez-le avant de générer l’aperçu.",
    SOURCE_PROTECTED_OBJECT: "Ce texte chevauche un lien, un champ de formulaire, une annotation ou une signature numérique. Utilisez un original non signé ou modifiez le document source.",
    SOURCE_OVERLAPPING_TEXT: "Les textes qui se chevauchent ne peuvent pas être séparés en toute sécurité. L’original est conservé.",
    SOURCE_DOCUMENT_CHANGED: "Le document source ou le paragraphe sélectionné a changé. Rouvrez le document puis réessayez.",
    SOURCE_TEXT_UNCHANGED: "Modifiez le document avant de générer un aperçu.",
    SOURCE_UNSAFE_TEXT: "Ce texte contient un caractère de contrôle non pris en charge.",
  },
};

export function sourceCopy(language = "en") { return COPY[language] || COPY.en; }

export function sourceErrorMessage(error, language, fallback) {
  const code = String(error?.payload?.detail?.error || error?.rawCode || error?.code || "").toUpperCase();
  return sourceCopy(language)[code] || fallback;
}

export function sourceFontFamily(font) {
  if (font?.data) return `"ReDOCXSource_${font.key}"`;
  const name = String(font?.name || "").toLowerCase();
  if (name.includes("times")) return '"Times New Roman", Times, serif';
  if (name.includes("courier")) return '"Courier New", Courier, monospace';
  return "Arial, Helvetica, sans-serif";
}

export function sourceFontAppearance(font) {
  if (font?.data) return {};
  const name = String(font?.name || "");
  return { fontWeight:/bold/i.test(name) ? 700 : 400,
    fontStyle:/italic|oblique/i.test(name) ? "italic" : "normal" };
}

export async function installSourceFonts(profile, signal) {
  const installed = [];
  try {
    for (const font of profile.fonts || []) {
      if (!font.data) continue;
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      if (!/^[a-f0-9]{24}$/.test(font.key) || font.data.length > 12 * 1024 * 1024) {
        throw Object.assign(new Error("SOURCE_FONT_UNAVAILABLE"), { code: "SOURCE_FONT_UNAVAILABLE" });
      }
      const bytes = Uint8Array.from(atob(font.data), (character) => character.charCodeAt(0));
      const face = new FontFace(`ReDOCXSource_${font.key}`, bytes);
      await face.load();
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      document.fonts.add(face);
      installed.push(face);
    }
    return () => installed.forEach((face) => document.fonts.delete(face));
  } catch (error) {
    installed.forEach((face) => document.fonts.delete(face));
    if (error?.name === "AbortError") throw error;
    throw Object.assign(new Error("SOURCE_FONT_UNAVAILABLE"), { code: "SOURCE_FONT_UNAVAILABLE" });
  }
}

export function sourceTextOperation(item) {
  return {
    operation_id: item.id,
    operation: item.kind === "source_insert" ? "insert_source_text" : "edit_source_text",
    page_number: item.page_number,
    rectangle: item.rectangle,
    source_block_id: item.sourceBlock.id,
    source_checksum_sha256: item.sourceChecksum,
    original_text_sha256: item.sourceBlock.text_sha256,
    text: item.text,
  };
}

export function insertionRectangle(point, profile, reference) {
  // Keep insertion in a horizontal document column. Existing rotated source
  // paragraphs can still be edited directly with their original orientation.
  if (Math.abs(Number(reference.angle) || 0) > 0.1) return null;
  // Align inserted text with the nearest paragraph’s inferred column margin.
  const width = Math.min(reference.layout_width / profile.width, 1 - reference.rectangle.x - 0.01);
  const x = reference.rectangle.x;
  const next = profile.blocks.filter((block) => block.rectangle.y > point.y &&
    block.rectangle.x < x + width && block.rectangle.x + block.rectangle.width > x);
  const bottom = Math.min(0.98, ...next.map((block) => block.rectangle.y - 0.005));
  const height = Math.min(4 * reference.line_height / profile.height, bottom - point.y);
  if (height < reference.font_size / profile.height || width <= 0) return null;
  return { x, y: point.y, width, height };
}
