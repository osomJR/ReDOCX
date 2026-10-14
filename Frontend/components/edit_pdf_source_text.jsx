"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { sourceFontFamily, sourceFontAppearance } from "@/lib/edit_pdf_source";

function baselineOffset(family, size, lineHeight) {
  const box = document.createElement("div");
  const marker = document.createElement("span");
  Object.assign(box.style, { position: "absolute", visibility: "hidden", left: "-10000px",
    fontFamily: family, fontSize: `${size}px`, lineHeight: `${lineHeight}px`, whiteSpace: "nowrap" });
  box.appendChild(document.createTextNode("Mg"));
  Object.assign(marker.style, { display: "inline-block", width: "0", height: "0", verticalAlign: "baseline" });
  box.appendChild(marker);
  document.body.appendChild(box);
  const result = marker.getBoundingClientRect().top - box.getBoundingClientRect().top;
  box.remove();
  return result;
}

export default function SourceTextBlock({ block, item, profile, pageMetrics, active, enabled,
  onBegin, onChange, onFinish, copy, inserted = false }) {
  const editorRef = useRef(null);
  const composingRef = useRef(false);
  const caretPointRef = useRef(null);
  const [baseline, setBaseline] = useState(0);
  const [draftHeight, setDraftHeight] = useState(0);
  const [inkBounds, setInkBounds] = useState(null);
  const value = item?.text ?? block.text;
  const rectangle = inserted ? item.rectangle : block.rectangle;
  const font = profile.fonts.find((candidate) => candidate.key === block.font_key);
  const family = sourceFontFamily(font);
  const scale = pageMetrics.width / profile.width;
  const fontSize = block.font_size * scale;
  const lineHeight = block.line_height * scale;
  const horizontalScale = Number(block.horizontal_scale) || 1;
  const modified = inserted || Boolean(item);
  const angle = Number(block.angle) || 0;
  const x = rectangle.x * pageMetrics.width;
  const y = rectangle.y * pageMetrics.height;
  const originalHeight = rectangle.height * pageMetrics.height;
  const localWidth = inserted ? rectangle.width * pageMetrics.width : block.layout_width * scale;
  const limitHeight = inserted ? rectangle.height * pageMetrics.height : block.max_height * scale;
  const height = Math.min(limitHeight, Math.max(originalHeight, draftHeight || 0));

  useLayoutEffect(() => {
    setBaseline(baselineOffset(family, fontSize, lineHeight));
  }, [family, fontSize, lineHeight]);

  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor || composingRef.current) return;
    const existing = editor.innerText.replace(/\r\n?/g, "\n");
    if (existing !== value) {
      // DOM text only. PDF content and pasted text are never interpreted as HTML.
      if (value === block.text && !inserted && block.spans?.length) {
        editor.replaceChildren(...block.spans.map((run) => {
          const span = document.createElement("span");
          span.textContent = run.text;
          const runFont = profile.fonts.find((candidate) => candidate.key === run.font_key);
          span.style.fontFamily = sourceFontFamily(runFont);
          Object.assign(span.style, sourceFontAppearance(runFont));
          span.style.fontSize = `${run.font_size * scale}px`;
          span.style.color = run.color;
          if (run.baseline_shift) { span.style.position = "relative"; span.style.top = `${run.baseline_shift * scale}px`; }
          return span;
        }));
      } else {
        editor.replaceChildren(document.createTextNode(value));
      }
    }
    setDraftHeight(editor.scrollHeight);
    const range = document.createRange();
    range.selectNodeContents(editor);
    const bounds = range.getBoundingClientRect();
    const frame = editor.closest("[data-source-editor]")?.parentElement?.getBoundingClientRect();
    if (frame && bounds.width && bounds.height) setInkBounds({
      left:bounds.left-frame.left,top:bounds.top-frame.top,right:bounds.right-frame.left,bottom:bounds.bottom-frame.top,
    });
  }, [value, active, localWidth, fontSize, lineHeight]);

  useLayoutEffect(() => {
    if (!active || !editorRef.current) return;
    editorRef.current.focus({ preventScroll: true });
    const selection = window.getSelection();
    const point = caretPointRef.current;
    caretPointRef.current = null;
    if (point && selection) {
      const caret = document.caretPositionFromPoint?.(point.x,point.y);
      const range = caret ? document.createRange() : document.caretRangeFromPoint?.(point.x,point.y);
      if (caret) { range.setStart(caret.offsetNode,caret.offset); range.collapse(true); }
      if (range && editorRef.current.contains(range.startContainer)) {
        selection.removeAllRanges(); selection.addRange(range); return;
      }
    }
    if (selection && !editorRef.current.contains(selection.anchorNode)) {
      const range = document.createRange();
      range.selectNodeContents(editorRef.current);
      range.collapse(false);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  }, [active]);

  if (!modified && !active) {
    return (
      <button type="button" disabled={!enabled || !block.editable}
        aria-label={`${copy.editText}: ${block.text.slice(0, 80)}`}
        title={block.editable ? `${block.font_name} · ${block.font_size.toFixed(2)} pt` : (copy[block.reason] || copy.readonly)}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => { event.stopPropagation(); caretPointRef.current={x:event.clientX,y:event.clientY}; onBegin(block); }}
        className="absolute z-10 cursor-text border border-transparent bg-transparent text-left hover:border-blue-400/70 hover:bg-blue-400/5 focus:border-blue-500 focus:outline-none disabled:cursor-default"
        style={{ left: `${rectangle.x * 100}%`, top: `${rectangle.y * 100}%`,
          width: `${rectangle.width * 100}%`, height: `${rectangle.height * 100}%` }} />
    );
  }

  // The backing image has only safely editable text removed. It preserves
  // colored paper, photographs, tables and vector graphics behind the text.
  const originX = inserted ? x : (block.origin?.x ?? rectangle.x) * pageMetrics.width;
  const originY = inserted ? y + (font?.ascender ?? block.baseline_offset/block.font_size)*fontSize : (block.origin?.y ?? rectangle.y) * pageMetrics.height;
  const radians = angle * Math.PI / 180;
  const corners = inkBounds ? [{x:inkBounds.left,y:inkBounds.top},{x:inkBounds.right,y:inkBounds.bottom}] :
    [[0,-baseline],[rectangle.width*pageMetrics.width,-baseline],[0,originalHeight-baseline],[rectangle.width*pageMetrics.width,originalHeight-baseline]].map(([u,v]) =>
      ({x:originX+Math.cos(radians)*u-Math.sin(radians)*v,y:originY+Math.sin(radians)*u+Math.cos(radians)*v}));
  const maskX = Math.min(x,...corners.map((point) => point.x));
  const maskY = Math.min(y,...corners.map((point) => point.y));
  const maskWidth = Math.max(x+rectangle.width*pageMetrics.width,...corners.map((point) => point.x))-maskX;
  const maskHeight = Math.max(y+originalHeight,...corners.map((point) => point.y))-maskY;
  const overflow = draftHeight > limitHeight + 1;
  const supported = new Set((profile.fonts || []).filter((candidate) => block.spans?.some((run) => run.font_key === candidate.key) || candidate.key === block.font_key).flatMap((candidate) => candidate.codepoints || []));
  const missingGlyph = supported.size > 0 && Array.from(value).some((character) => character !== "\n" && !supported.has(character.codePointAt(0)));
  return (
    <div data-source-editor="true" className="absolute z-20"
      onPointerDown={(event) => event.stopPropagation()}
      style={{ left: maskX, top: maskY, width: maskWidth, height:maskHeight, pointerEvents:enabled ? "auto" : "none",
        backgroundImage: inserted ? undefined : `url("${profile.background}")`,
        backgroundSize: `${pageMetrics.width}px ${pageMetrics.height}px`,
        backgroundPosition: `${-maskX}px ${-maskY}px`, backgroundRepeat: "no-repeat" }}>
      <div ref={editorRef} role="textbox" aria-multiline="true" aria-label={copy.directText}
        contentEditable={active && enabled ? "plaintext-only" : false} suppressContentEditableWarning
        spellCheck={false} data-placeholder={inserted ? copy.insertPlaceholder : undefined}
        className="absolute cursor-text outline-none focus:ring-1 focus:ring-blue-500/60 empty:before:content-[attr(data-placeholder)] empty:before:text-slate-400"
        style={{ left: originX-maskX, top: originY-baseline-maskY, width: localWidth/horizontalScale,
          minHeight: lineHeight, maxHeight: limitHeight, color: block.color,
          fontFamily: family, ...sourceFontAppearance(font), fontSize, lineHeight: `${lineHeight}px`,
          textAlign:block.alignment || "left", textIndent:(block.first_indent || 0)*scale/horizontalScale,
          whiteSpace: "pre-wrap", overflowWrap: "anywhere", padding: 0, margin: 0,
          transform: angle || horizontalScale !== 1 ? `rotate(${angle}deg) scaleX(${horizontalScale})` : undefined,
          transformOrigin: `0 ${baseline}px`, boxSizing: "border-box" }}
        onClick={() => { if (enabled && !active) onBegin(block, item); }}
        onCompositionStart={() => { composingRef.current = true; }}
        onCompositionEnd={(event) => {
          composingRef.current = false;
          onChange(block, event.currentTarget.innerText.replace(/\r\n?/g, "\n"), item);
        }}
        onInput={(event) => {
          setDraftHeight(event.currentTarget.scrollHeight);
          if (!composingRef.current) onChange(block, event.currentTarget.innerText.replace(/\r\n?/g, "\n"), item);
        }}
        onPaste={(event) => {
          if (event.clipboardData.getData("text/plain").length + value.length > 100_000) event.preventDefault();
        }}
        onBlur={onFinish}
        onKeyDown={(event) => {
          if (event.key === "Escape" || ((event.ctrlKey || event.metaKey) && event.key === "Enter")) {
            event.preventDefault(); event.currentTarget.blur();
          }
          event.stopPropagation();
        }} />
      {overflow || missingGlyph ? <span role="alert" className="absolute left-0 top-full mt-1 max-w-sm rounded-lg bg-red-950 px-2 py-1 text-xs text-red-100">{missingGlyph ? copy.SOURCE_FONT_MISSING_GLYPH : copy.SOURCE_TEXT_OVERFLOW}</span> : null}
    </div>
  );
}

export function SourceTextInspector({ block, copy, onRestore, changed }) {
  return <section className="rounded-2xl border app-surface-strong p-4" aria-label={copy.sourceStyle}>
    <p className="text-xs font-semibold uppercase tracking-wider app-text-soft">{copy.sourceStyle}</p>
    <p className="mt-2 text-sm font-medium app-text">{copy.automatic}</p>
    {block ? <dl className="mt-4 grid grid-cols-[1fr_auto] gap-x-3 gap-y-2 text-sm">
      <dt className="app-text-muted">{copy.matchingFont}</dt><dd className="app-text">{block.font_name}</dd>
      <dt className="app-text-muted">{copy.size}</dt><dd className="app-text">{block.font_size.toFixed(2)} pt</dd>
      <dt className="app-text-muted">{copy.spacing}</dt><dd className="app-text">{block.line_height.toFixed(2)} pt</dd>
    </dl> : null}
    <p className="mt-4 text-xs leading-5 app-text-muted">{copy.preserve}</p>
    {changed ? <button type="button" onClick={onRestore} className="mt-4 rounded-xl border app-surface px-3 py-2 text-xs font-semibold app-text">{copy.restored}</button> : null}
  </section>;
}
