import { KeyboardEvent, PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";
import { ACCEPT_IMAGES, Crop, FilePick, Icon, Item, MAX_FILES, plural, Workspace } from "./shared";
import { Segmented } from "./ToolScreen";

type Drag = { pointer: number; id: number; from: number; to: number; x: number; y: number; dx: number; dy: number; active: boolean };

/** Images → PDF pages: a grid of tiles to reorder (pointer drag or keyboard), turn, crop and remove. */
export function PageEditor({ ws }: { ws: Workspace }) {
  const { items } = ws;
  const [selected, setSelected] = useState<number | null>(null);
  const [cropping, setCropping] = useState<number | null>(null);
  const [broken, setBroken] = useState<Set<number>>(new Set());
  const [drag, setDrag] = useState<Drag | null>(null);
  const [settling, setSettling] = useState(false);
  const listRef = useRef<HTMLUListElement>(null);
  const slots = useRef<DOMRect[]>([]); // tile positions in page coordinates, measured when a drag starts
  const dragRef = useRef(drag);
  dragRef.current = drag;
  const sel = items.findIndex((i) => i.id === selected);
  const page = (id: number) => listRef.current?.querySelector<HTMLElement>(`[data-page="${id}"]`);
  // After React moves a tile in the DOM it may lose focus: put it back once the new order is painted.
  const refocus = (id: number) => requestAnimationFrame(() => page(id)?.focus());

  function moveBy(index: number, by: number) {
    const to = index + by;
    if (to < 0 || to >= items.length) return;
    ws.moveTo(index, to);
    ws.say(`${items[index].file.name} moved to position ${to + 1} of ${items.length}`);
    refocus(items[index].id);
  }

  function rotate(item: Item) {
    ws.rotate(item.id);
    ws.say(`${item.file.name} turned to ${(item.rotation + 90) % 360}°`);
  }

  function remove(index: number) {
    const item = items[index];
    const next = items[index + 1] ?? items[index - 1];
    ws.remove(item.id);
    ws.say(`${item.file.name} removed`);
    setSelected(next?.id ?? null);
    if (next) refocus(next.id);
  }

  function onKey(e: KeyboardEvent, index: number) {
    if (e.ctrlKey || e.metaKey || e.altKey) return; // leave Ctrl+R, Alt+← and friends to the browser
    const item = items[index];
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") moveBy(index, e.key === "ArrowLeft" ? -1 : 1);
    else if (e.key === "r" || e.key === "R") rotate(item);
    else if (e.key === "Delete" || e.key === "Backspace") remove(index);
    else return;
    e.preventDefault();
  }

  function onPointerDown(e: ReactPointerEvent<HTMLLIElement>, index: number) {
    const target = e.target as Element;
    const handle = target.closest(".page-handle");
    if (e.button !== 0 || target.closest(".page-tools")) return;
    if (e.pointerType !== "mouse" && !handle) return; // on touch only the handle drags, so the page still scrolls
    slots.current = [...listRef.current!.querySelectorAll(".page")].map((el) => {
      const r = el.getBoundingClientRect();
      return new DOMRect(r.x + scrollX, r.y + scrollY, r.width, r.height);
    });
    setDrag({ pointer: e.pointerId, id: items[index].id, from: index, to: index, x: e.pageX, y: e.pageY, dx: 0, dy: 0, active: !!handle });
  }

  useEffect(() => {
    if (!drag) return;
    const move = (e: PointerEvent) => {
      const d = dragRef.current!;
      if (e.pointerId !== d.pointer) return; // a second finger must not steer the drag
      const dx = e.pageX - d.x;
      const dy = e.pageY - d.y;
      if (!d.active && Math.hypot(dx, dy) < 6) return; // a mouse click, not a drag yet
      e.preventDefault();
      if (e.clientY < 80) scrollBy(0, -12);
      else if (e.clientY > innerHeight - 80) scrollBy(0, 12);
      const centre = (r: DOMRect) => Math.hypot(r.x + r.width / 2 - e.pageX, r.y + r.height / 2 - e.pageY);
      const to = slots.current.reduce((best, r, n) => (centre(r) < centre(slots.current[best]) ? n : best), 0);
      setDrag({ ...d, active: true, dx, dy, to });
    };
    const end = (commit: boolean) => (e: PointerEvent) => {
      const d = dragRef.current!;
      if (e.pointerId !== d.pointer) return;
      setDrag(null);
      if (!d.active) return;
      setSettling(true); // tiles jump to their new DOM places without animating from the old offsets
      setSelected(d.id);
      if (commit && d.to !== d.from) {
        ws.moveTo(d.from, d.to);
        ws.say(`${items[d.from].file.name} moved to position ${d.to + 1} of ${items.length}`);
      }
    };
    const up = end(true);
    const cancel = end(false);
    addEventListener("pointermove", move, { passive: false });
    addEventListener("pointerup", up);
    addEventListener("pointercancel", cancel);
    return () => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", cancel);
    };
  }, [drag?.id]);

  useEffect(() => {
    if (!settling) return;
    const frame = requestAnimationFrame(() => setSettling(false));
    return () => cancelAnimationFrame(frame);
  }, [settling]);

  /** Where tile `j` is drawn while dragging: the dragged one follows the pointer, the others make room. */
  function shift(j: number): string | undefined {
    if (!drag?.active) return undefined;
    if (j === drag.from) return `translate(${drag.dx}px, ${drag.dy}px) scale(1.04)`;
    const { from, to } = drag;
    const k = from < to && j > from && j <= to ? j - 1 : from > to && j >= to && j < from ? j + 1 : j;
    if (k === j) return undefined;
    const a = slots.current[j];
    const b = slots.current[k];
    return `translate(${b.x - a.x}px, ${b.y - a.y}px)`;
  }

  const current = sel >= 0 ? items[sel] : null;
  const tools = (item: Item, index: number) => (
    <>
      <button type="button" className="icon-btn" onClick={() => rotate(item)} aria-label={`Turn ${item.file.name} clockwise (now ${item.rotation}°)`}>
        <Icon name="rotate" />
      </button>
      <button
        type="button"
        className="icon-btn"
        disabled={broken.has(item.id)}
        onClick={() => setCropping(item.id)}
        aria-label={broken.has(item.id) ? `${item.file.name} can't be cropped here: the browser can't show it` : `Crop ${item.file.name}`}
      >
        <Icon name="crop" />
      </button>
      <button type="button" className="icon-btn" onClick={() => remove(index)} aria-label={`Remove ${item.file.name}`}>
        <Icon name="close" />
      </button>
    </>
  );
  const cropItem = items.find((i) => i.id === cropping);

  return (
    <>
      <ul ref={listRef} className={`pages${drag?.active ? " dragging" : ""}${settling ? " settling" : ""}`} aria-label="Pages, in order">
        {items.map((item, j) => {
          const label = `Page ${j + 1}: ${item.file.name}${item.rotation ? `, turned ${item.rotation}°` : ""}${item.crop ? ", cropped" : ""}`;
          return (
            <li
              key={item.id}
              className={`page${item.id === selected ? " selected" : ""}${drag?.active && drag.id === item.id ? " lifted" : ""}`}
              style={{ transform: shift(j) }}
              onPointerDown={(e) => onPointerDown(e, j)}
            >
              <button
                type="button"
                className="page-pick"
                data-page={item.id}
                aria-pressed={item.id === selected}
                aria-label={label}
                aria-describedby="page-keys"
                onFocus={() => setSelected(item.id)}
                onClick={() => setSelected(item.id)}
                onKeyDown={(e) => onKey(e, j)}
              >
                <span className="page-preview">
                  <PagePreview item={item} size={344} onBroken={() => setBroken((b) => new Set(b).add(item.id))} />
                </span>
              </button>
              <span className="page-foot">
                <span className="num">{j + 1}</span>
                <span className="page-name">{item.file.name}</span>
                <span className="page-handle" aria-hidden="true" title="Drag to reorder">
                  <Icon name="grip" />
                </span>
              </span>
              {item.id === selected && (
                <span className="page-tools" role="group" aria-label={`Page ${j + 1} actions`}>
                  {tools(item, j)}
                </span>
              )}
            </li>
          );
        })}
        {items.length < MAX_FILES && (
          <li className="page-add-cell">
            <FilePick className="page-add" accept={ACCEPT_IMAGES} multiple onFiles={(f) => ws.add(f)} name="Add more images">
              <Icon name="plus" />
              <span>Add More</span>
            </FilePick>
          </li>
        )}
      </ul>
      <p id="page-keys" className="muted small fine-only">
        Keyboard: select a page, then ← → to move it, R to rotate, Delete to remove.
      </p>
      <p className="muted small">
        JPEG, PNG, WebP or AVIF · up to {MAX_FILES} images, 50 MB in total.
      </p>

      {/* Phone: the selected page's controls and the main button stay in reach at the bottom of the screen. */}
      <div className="editor-bar">
        <div className="bar-row">
          <span className="bar-label">{current ? `Page ${sel + 1}` : "Tap a page to edit it"}</span>
          <span className="bar-tools">
            <button type="button" className="icon-btn boxed" disabled={!current || sel === 0} onClick={() => moveBy(sel, -1)} aria-label={current ? `Move ${current.file.name} left` : "Move left"}>
              <Icon name="back" />
            </button>
            <button
              type="button"
              className="icon-btn boxed"
              disabled={!current || sel === items.length - 1}
              onClick={() => moveBy(sel, 1)}
              aria-label={current ? `Move ${current.file.name} right` : "Move right"}
            >
              <Icon name="forward" />
            </button>
            {current ? (
              tools(current, sel)
            ) : (
              <>
                <button type="button" className="icon-btn" disabled aria-label="Turn">
                  <Icon name="rotate" />
                </button>
                <button type="button" className="icon-btn" disabled aria-label="Crop">
                  <Icon name="crop" />
                </button>
                <button type="button" className="icon-btn" disabled aria-label="Remove">
                  <Icon name="close" />
                </button>
              </>
            )}
          </span>
        </div>
        <div className="bar-row">
          <button
            type="button"
            className="icon-btn boxed"
            aria-label="Settings"
            onClick={() => {
              const panel = document.getElementById("settings");
              panel?.scrollIntoView({ behavior: "smooth" });
              panel?.focus({ preventScroll: true });
            }}
          >
            <Icon name="sliders" />
          </button>
          <button className="btn primary">
            Make PDF · {plural(items.length, "Page")}
          </button>
        </div>
      </div>

      {cropItem && (
        <CropDialog
          item={cropItem}
          page={items.indexOf(cropItem) + 1}
          onDone={(crop) => {
            if (crop !== undefined) {
              ws.setCrop(cropItem.id, crop);
              ws.say(crop ? `${cropItem.file.name} cropped` : `Crop removed from ${cropItem.file.name}`);
            }
            setCropping(null);
            refocus(cropItem.id);
          }}
        />
      )}
    </>
  );
}

function useImage(url: string) {
  const [img, setImg] = useState<HTMLImageElement | "broken" | null>(null);
  useEffect(() => {
    const im = new Image();
    let live = true;
    im.onload = () => live && setImg(im);
    im.onerror = () => live && setImg("broken");
    im.src = url;
    return () => {
      live = false;
    };
  }, [url]);
  return img;
}

/** Draw `img` turned clockwise by `rotation` and cut to `crop`, at most `max` px on its longer side. */
function draw(canvas: HTMLCanvasElement, img: HTMLImageElement, rotation: number, crop: Crop | null, max: number) {
  const [w, h] = [img.naturalWidth, img.naturalHeight];
  const [rw, rh] = rotation % 180 ? [h, w] : [w, h];
  const [l, t, r, b] = crop ?? [0, 0, 1, 1];
  const scale = Math.min(1, max / Math.max(rw * (r - l), rh * (b - t)));
  canvas.width = Math.max(1, Math.round(rw * (r - l) * scale));
  canvas.height = Math.max(1, Math.round(rh * (b - t) * scale));
  const ctx = canvas.getContext("2d")!;
  ctx.scale(scale, scale);
  ctx.translate(-l * rw, -t * rh); // the crop, in the turned image's space
  ctx.translate(rw / 2, rh / 2);
  ctx.rotate((rotation * Math.PI) / 180);
  ctx.drawImage(img, -w / 2, -h / 2);
}

/** The page as the PDF will show it: turned, then cropped. */
function PagePreview({ item, size, onBroken }: { item: Item; size: number; onBroken: () => void }) {
  const img = useImage(item.url);
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (img === "broken") onBroken();
    else if (img && ref.current) draw(ref.current, img, item.rotation, item.crop, size);
  }, [img, item.rotation, item.crop]);
  if (img === "broken")
    return (
      <span className="page-fallback">
        <Icon name="image" />
        <small>No preview{item.rotation ? ` · turned ${item.rotation}°` : ""}</small>
      </span>
    );
  return <canvas ref={ref} />;
}

type Handle = "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const HANDLES: [Handle, string][] = [
  ["nw", "Top left corner"],
  ["n", "Top edge"],
  ["ne", "Top right corner"],
  ["e", "Right edge"],
  ["se", "Bottom right corner"],
  ["s", "Bottom edge"],
  ["sw", "Bottom left corner"],
  ["w", "Left edge"],
];
const FULL: Crop = [0, 0, 1, 1];
const clamp = (v: number, low: number, high: number) => Math.min(high, Math.max(low, v));

/**
 * Move or resize crop `c` (fractions) by `dx`, `dy` with handle `h`. `f` locks width/height in fractions;
 * `min` is the smallest width and height. Rounded so the server's strict 0..1 check never trips on 1.0000001.
 */
function resize(c: Crop, h: Handle, dx: number, dy: number, f: number | null, min: [number, number]): Crop {
  let [l, t, r, b] = c;
  const tidy = (v: number) => clamp(Math.round(v * 1e4) / 1e4, 0, 1);
  if (h === "move") {
    const [w, hh] = [r - l, b - t];
    l = clamp(l + dx, 0, 1 - w);
    t = clamp(t + dy, 0, 1 - hh);
    return [tidy(l), tidy(t), tidy(l + w), tidy(t + hh)];
  }
  if (h.includes("w")) l = clamp(l + dx, 0, r - min[0]);
  if (h.includes("e")) r = clamp(r + dx, l + min[0], 1);
  if (h.includes("n")) t = clamp(t + dy, 0, b - min[1]);
  if (h.includes("s")) b = clamp(b + dy, t + min[1], 1);
  if (f) {
    // Top and bottom handles set the height, the others the width; the other side follows, pinned to the
    // opposite edge (or centred), and both shrink together if that would leave the image.
    let [w, hh] = [r - l, b - t];
    if (h === "n" || h === "s") w = hh * f;
    else hh = w / f;
    const ax = h.includes("w") ? r : h.includes("e") ? l : (l + r) / 2;
    const ay = h.includes("n") ? b : h.includes("s") ? t : (t + b) / 2;
    const roomX = h.includes("w") ? ax : h.includes("e") ? 1 - ax : 2 * Math.min(ax, 1 - ax);
    const roomY = h.includes("n") ? ay : h.includes("s") ? 1 - ay : 2 * Math.min(ay, 1 - ay);
    const k = Math.min(1, roomX / w, roomY / hh);
    [w, hh] = [w * k, hh * k];
    l = h.includes("w") ? ax - w : h.includes("e") ? ax : ax - w / 2;
    t = h.includes("n") ? ay - hh : h.includes("s") ? ay : ay - hh / 2;
    [r, b] = [l + w, t + hh];
  }
  return [tidy(l), tidy(t), tidy(r), tidy(b)];
}

/** The largest crop of shape `f` (width / height in fractions), centred. */
const centred = (f: number): Crop => {
  const [w, h] = f >= 1 ? [1, 1 / f] : [f, 1];
  return [(1 - w) / 2, (1 - h) / 2, (1 + w) / 2, (1 + h) / 2].map((v) => Math.round(v * 1e4) / 1e4) as Crop;
};

function CropDialog({ item, page, onDone }: { item: Item; page: number; onDone: (crop?: Crop | null) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const area = useRef<HTMLDivElement>(null);
  const result = useRef<Crop | null | undefined>(undefined);
  const img = useImage(item.url);
  const [rect, setRect] = useState<Crop>(item.crop ?? FULL);
  const [shape, setShape] = useState("free");
  const grab = useRef<{ h: Handle; x: number; y: number; start: Crop } | null>(null);

  useEffect(() => {
    if (!dialog.current?.open) dialog.current?.showModal(); // the guard keeps a second effect run (dev) harmless
  }, []);
  useEffect(() => {
    if (img && img !== "broken" && canvas.current) draw(canvas.current, img, item.rotation, null, 1400);
  }, [img]);

  const loaded = img && img !== "broken" ? img : null;
  const [rw, rh] = !loaded ? [1, 1] : item.rotation % 180 ? [loaded.naturalHeight, loaded.naturalWidth] : [loaded.naturalWidth, loaded.naturalHeight];
  // Width / height of each shape in pixels, turned into fractions of this image.
  const pixels: Record<string, number | null> = { free: null, a4: rw >= rh ? 297 / 210 : 210 / 297, square: 1, original: rw / rh };
  const f = pixels[shape] && (pixels[shape]! * rh) / rw;
  const min: [number, number] = [Math.max(0.03, 8 / rw), Math.max(0.03, 8 / rh)];

  function pick(next: string) {
    setShape(next);
    const p = pixels[next];
    if (p) setRect(centred((p * rh) / rw));
  }

  function start(e: ReactPointerEvent<HTMLElement>, h: Handle) {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    grab.current = { h, x: e.clientX, y: e.clientY, start: rect };
  }

  function move(e: ReactPointerEvent<HTMLElement>) {
    const g = grab.current;
    if (!g || !area.current) return;
    const box = area.current.getBoundingClientRect();
    setRect(resize(g.start, g.h, (e.clientX - g.x) / box.width, (e.clientY - g.y) / box.height, f, min));
  }

  function nudge(e: KeyboardEvent, h: Handle) {
    const step = e.shiftKey ? 0.1 : 0.01;
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!d) return;
    e.preventDefault();
    setRect((r) => resize(r, h, d[0], d[1], f, min));
  }

  const close = (crop?: Crop | null) => {
    result.current = crop;
    dialog.current?.close();
  };
  const full = rect.every((v, n) => v === FULL[n]);
  const handlers = (h: Handle) => ({
    onPointerDown: (e: ReactPointerEvent<HTMLElement>) => start(e, h),
    onPointerMove: move,
    onPointerUp: () => (grab.current = null),
    onPointerCancel: () => (grab.current = null),
    onKeyDown: (e: KeyboardEvent) => nudge(e, h),
  });

  return (
    <dialog ref={dialog} className="crop-dialog" aria-labelledby="crop-title" onClose={() => onDone(result.current)}>
      <div className="crop-head">
        <div>
          <h2 id="crop-title">Crop Page {page}</h2>
          <p className="muted small">
            {item.file.name}
            {loaded && ` · ${rw} × ${rh} → ${Math.round((rect[2] - rect[0]) * rw)} × ${Math.round((rect[3] - rect[1]) * rh)}`}
          </p>
        </div>
        <button type="button" className="icon-btn boxed" onClick={() => close()} aria-label="Close without cropping">
          <Icon name="close" />
        </button>
      </div>
      <div className="crop-stage">
        {img === "broken" ? (
          <p className="muted">This image can't be shown in the browser, so it can't be cropped here.</p>
        ) : (
          <div className="crop-area" ref={area}>
            <canvas ref={canvas} />
            {loaded && (
              <div
                className="crop-box"
                role="group"
                aria-label="Crop area"
                style={{
                  left: `${rect[0] * 100}%`,
                  top: `${rect[1] * 100}%`,
                  width: `${(rect[2] - rect[0]) * 100}%`,
                  height: `${(rect[3] - rect[1]) * 100}%`,
                }}
              >
                <button type="button" className="crop-move" aria-label="Move the crop area (arrow keys, Shift for bigger steps)" {...handlers("move")} />
                {HANDLES.map(([h, label]) => (
                  <button key={h} type="button" className={`crop-handle ${h}`} aria-label={`${label} (arrow keys, Shift for bigger steps)`} {...handlers(h)} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      <div className="crop-foot">
        <Segmented
          legend="Shape"
          value={shape}
          options={[
            ["free", "Free"],
            ["a4", "A4"],
            ["square", "Square"],
            ["original", "Original"],
          ]}
          onChange={pick}
        />
        <span className="crop-actions">
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              setShape("free");
              setRect(FULL);
            }}
          >
            Reset
          </button>
          <button type="button" className="btn secondary" onClick={() => close()}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={!loaded} onClick={() => close(full ? null : rect)}>
            Apply Crop
          </button>
        </span>
      </div>
    </dialog>
  );
}
