// Source typography and document editing helpers. Used only by Edit PDF.
export const SOURCE_TEXT_KINDS = new Set(["source_text", "source_insert"]);

const COPY = {
  en: {
    editText: "Edit document text", instruction: "Click a paragraph and type directly on the page. Click empty space to insert text in the document’s style.",
    sourceStyle: "Document style", automatic: "Detected automatically", matchingFont: "Source font", size: "Size", spacing: "Line spacing",
    preserve: "Existing formatting is retained where available. Review font substitutions and recognized scan text in the generated PDF.",
    ocrNotice: "Scanned text was recognized automatically. Check the words and formatting carefully in the generated PDF; handwriting and artwork may remain images.",
    fontNotice: "Some fonts or characters use an available substitute. Review their appearance before downloading.",
    draftFontNotice: "A font could not be displayed in this draft. Check the generated PDF for its final appearance.",
    fontFallback: "A matching font will be used for new characters when the original font has no glyph.",
    reviewEditedPages: "Review edited pages", reviewProgress: "Edited pages reviewed",
    SOURCE_OCR_UNAVAILABLE: "Scanned text recognition is unavailable. Retry, or use Add text and Whiteout.",
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
    SOURCE_FONT_UNAVAILABLE: "No usable font could be loaded for this paragraph. Add an installed font or use a different font in Add text.",
    SOURCE_FONT_MISSING_GLYPH: "No available font can represent one of these characters. Install a font supporting it or choose a different character.",
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
    preserve: "La mise en forme existante est conservée si possible. Vérifiez les polices de remplacement et le texte reconnu dans le PDF généré.",
    ocrNotice: "Le texte numérisé a été reconnu automatiquement. Vérifiez les mots et la mise en forme dans le PDF généré ; l’écriture manuscrite et les illustrations peuvent rester des images.",
    fontNotice: "Certaines polices ou certains caractères utilisent une police de remplacement. Vérifiez leur apparence avant le téléchargement.",
    draftFontNotice: "Une police ne peut pas être affichée dans ce brouillon. Vérifiez son apparence finale dans le PDF généré.",
    fontFallback: "Une police adaptée sera utilisée si la police d’origine ne contient pas les nouveaux caractères.",
    reviewEditedPages: "Vérifier les pages modifiées", reviewProgress: "Pages modifiées vérifiées",
    SOURCE_OCR_UNAVAILABLE: "La reconnaissance du texte numérisé est indisponible. Réessayez ou utilisez Ajouter du texte et Effacement.",
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
    SOURCE_FONT_UNAVAILABLE: "Aucune police utilisable n’a pu être chargée. Installez une police adaptée ou choisissez-en une autre dans Ajouter du texte.",
    SOURCE_FONT_MISSING_GLYPH: "Aucune police disponible ne prend en charge l’un de ces caractères. Installez une police adaptée ou changez le caractère.",
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
  if (font?.data && font.browser_loaded !== false) return `"ReDOCXSource_${font.key}"`;
  const name = String(font?.name || "").toLowerCase();
  if (name.includes("times")) return '"Times New Roman", Times, serif';
  if (name.includes("courier")) return '"Courier New", Courier, monospace';
  return "Arial, Helvetica, sans-serif";
}

export function sourceFontAppearance(font) {
  if (font?.data && font.browser_loaded !== false) return {};
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
      try {
        await face.load();
      } catch (error) {
        if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
        // A browser's font sanitizer may reject an otherwise usable PDF
        // program. Keep the page editable and disclose the draft fallback;
        // the server still uses and verifies the original font program.
        font.browser_loaded = false;
        profile.draft_font_fallback = true;
        continue;
      }
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      document.fonts.add(face);
      font.browser_loaded = true;
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

// Match unchanged character runs, mirroring the server's style inheritance.
// This keeps bold, colour, size and font changes visible after an edit instead
// of flattening the entire paragraph to the first span's appearance.
export function sourceStyledRuns(block, value) {
  const original = Array.from(block.text || "");
  const next = Array.from(value || "");
  const fallback = block.spans?.[0] || {
    font_key: block.font_key, font_size: block.font_size, color: block.color,
    baseline_shift: 0, horizontal_scale: block.horizontal_scale || 1,
  };
  const styles = (block.spans || []).flatMap((span) => Array.from(span.text, () => span));
  const assigned = Array(next.length).fill(null);
  const indices = new Map();
  original.forEach((character, index) => {
    if (!indices.has(character)) indices.set(character, []);
    indices.get(character).push(index);
  });
  // A bounded longest-match search: browser input must remain responsive for
  // large pasted paragraphs. Unmatched characters inherit the adjacent style.
  let work = 0;
  const pending = [[0, original.length, 0, next.length]];
  while (pending.length && work < 2_000_000) {
    const [a0, a1, b0, b1] = pending.pop();
    let previous = new Map(), best = 0, startA = a0, startB = b0;
    for (let b = b0; b < b1 && work < 2_000_000; b += 1) {
      const current = new Map();
      for (const a of indices.get(next[b]) || []) {
        if (a < a0 || a >= a1) continue;
        work += 1;
        const length = (previous.get(a - 1) || 0) + 1;
        current.set(a, length);
        if (length > best) { best = length; startA = a - length + 1; startB = b - length + 1; }
      }
      previous = current;
    }
    if (!best) continue;
    for (let offset = 0; offset < best; offset += 1) assigned[startB + offset] = styles[startA + offset] || fallback;
    if (a0 < startA && b0 < startB) pending.push([a0, startA, b0, startB]);
    if (startA + best < a1 && startB + best < b1) pending.push([startA + best, a1, startB + best, b1]);
  }
  let inherited = assigned.find(Boolean) || fallback;
  const runs = [];
  next.forEach((character, index) => {
    const style = assigned[index] || inherited;
    inherited = style;
    const key = JSON.stringify([style.font_key, style.font_size, style.color, style.baseline_shift, style.horizontal_scale]);
    if (runs.length && runs[runs.length - 1]._key === key) runs[runs.length - 1].text += character;
    else runs.push({ ...style, text: character, _key: key });
  });
  return runs;
}
