import { DragEvent, ReactNode, useRef, useState } from "react";
import { formatSize, Job } from "./api";

export type ToolName = "image" | "images_to_pdf" | "pdf_compress";

export const TOOLS: { name: ToolName; title: string; text: string; short: string; meta: string; icon: IconName }[] = [
  {
    name: "image",
    title: "Image Tool",
    text: "Convert, resize and compress images",
    short: "Convert, resize, compress",
    meta: "JPEG · PNG · WebP · AVIF",
    icon: "image",
  },
  {
    name: "images_to_pdf",
    title: "Images → PDF",
    text: "Arrange, crop and rename pages",
    short: "Arrange, crop, rename",
    meta: "Up to 30 images per PDF",
    icon: "pages",
  },
  {
    name: "pdf_compress",
    title: "PDF Compressor",
    text: "Shrink a PDF to a strength or size",
    short: "Shrink a PDF",
    meta: "One PDF · up to 100 pages",
    icon: "compress",
  },
];

export const SOURCE_URL = "https://github.com/Foursquare16/LocalFileTools";

export type Page = ToolName | "privacy" | "licences" | "notfound" | null;

/** The screen the address asks for: a tool, a text page, "notfound", or null for the machine. */
export function pageInAddress(): Page {
  const hash = location.hash.slice(1);
  if (location.pathname !== "/") return "notfound";
  if (!hash) return null;
  if (hash === "privacy" || hash === "licences") return hash;
  return TOOLS.find((t) => t.name === hash)?.name ?? "notfound";
}

// shortcut: mirrors the server defaults (LFT_MAX_FILES, LFT_MAX_UPLOAD_MB); the server still enforces its own values.
export const MAX_FILES = 30;
export const MAX_BYTES = 50 * 1024 * 1024 - 64 * 1024; // leaves room for the multipart overhead in the request body
export const ACCEPT_IMAGES = "image/jpeg,image/png,image/webp,image/avif,.avif";
export const ACCEPT_PDF = "application/pdf,.pdf";
export const DONE = ["completed", "failed", "cancelled"];

/** `crop` is [left, top, right, bottom] as fractions of the image after `rotation`, or null for the whole image. */
export type Crop = [number, number, number, number];
/**
 * `url` is the full file; the list and grid show `preview`, a small JPEG made after the file is added (null while it is
 * pending, or for a PDF). `size` is the photo's real pixel size, `broken` that the browser could not decode it.
 */
export type Item = {
  id: number;
  file: File;
  url: string;
  rotation: number;
  crop: Crop | null;
  preview: string | null;
  size: [number, number] | null;
  broken: boolean;
};

export const isPdf = (f: File) => f.type === "application/pdf" || /\.pdf$/i.test(f.name);
// The type is often empty for AVIF, so the extension counts too. The worker checks the real content.
export const isImage = (f: File) => /^image\/(jpeg|png|webp|avif)$/.test(f.type) || /\.(jpe?g|png|webp|avif)$/i.test(f.name);

export function fits(tool: ToolName, items: Item[]) {
  if (!items.length) return false;
  if (tool === "pdf_compress") return items.length === 1 && isPdf(items[0].file);
  return items.every((i) => isImage(i.file));
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
export const totalSize = (files: { size: number }[]) => files.reduce((sum, f) => sum + f.size, 0);

export type Settings = {
  format: string;
  maxWidth: string;
  maxHeight: string;
  mode: "quality" | "target";
  quality: number;
  targetKb: string;
  page: string;
  margin: string;
  strength: string;
  name: string;
};

export const DEFAULT_SETTINGS: Settings = {
  format: "jpeg",
  maxWidth: "",
  maxHeight: "",
  mode: "quality",
  quality: 85,
  targetKb: "500",
  page: "a4",
  margin: "10",
  strength: "medium",
  name: "images",
};

export function options(tool: ToolName, s: Settings, items: Item[]) {
  if (tool === "pdf_compress") return s.mode === "target" ? { target_kb: Number(s.targetKb) } : { strength: s.strength };
  if (tool === "images_to_pdf")
    return {
      page: s.page,
      margin_mm: Number(s.margin),
      rotations: items.map((i) => i.rotation),
      crops: items.map((i) => i.crop),
      name: s.name,
    };
  const opts: Record<string, unknown> = { format: s.format };
  if (s.maxWidth) opts.max_width = Number(s.maxWidth);
  if (s.maxHeight) opts.max_height = Number(s.maxHeight);
  if (s.format !== "png") {
    if (s.mode === "target") opts.target_kb = Number(s.targetKb);
    else opts.quality = s.quality;
  }
  return opts;
}

/** Everything the machine and the tool screens share; owned by Workspace in App.tsx. */
export type Workspace = {
  items: Item[];
  settings: Settings;
  job: Job | null;
  jobTool: ToolName | null;
  progress: number | null;
  failure: { message: string; stage: string | null } | null;
  error: string;
  deleted: boolean;
  busy: boolean;
  set: (patch: Partial<Settings>) => void;
  add: (files: FileList | null, replace?: boolean) => void;
  remove: (id: number) => void;
  moveTo: (from: number, to: number) => void;
  rotate: (id: number) => void;
  setCrop: (id: number, crop: Crop | null) => void;
  say: (text: string) => void;
  clear: () => void;
  start: (tool: ToolName) => void;
  discard: (fresh: boolean) => void;
  deleteAll: () => void;
};

export function statusText({ progress, job }: { progress: number | null; job: Job | null }) {
  if (progress !== null) return "Uploading…";
  if (job?.status !== "queued") return job?.stage ?? "Processing…";
  return job.position ? `In queue (position ${job.position})` : "In queue…";
}

/** "87% smaller", "12% larger" or "about the same size". */
function savedText(before: number, after: number) {
  if (!before) return "";
  const pct = Math.round((1 - after / before) * 100);
  if (pct === 0) return "about the same size"; // also catches -0: a hair larger is not "0% smaller"
  return pct > 0 ? `${pct}% smaller` : `${-pct}% larger`;
}

export function Saved({ before, after }: { before: number; after: number }) {
  const text = savedText(before, after);
  return text ? <span className={text.endsWith("larger") ? "grew" : "saved"}>{text}</span> : null;
}

export const sizeChange = (before: number, after: number) => `${formatSize(before)} → ${formatSize(after)}`;

/** A button that, after a confirmation dialog, deletes every job and file of this browser. */
export function DeleteAll({ ws, className }: { ws: Workspace; className: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  return (
    <>
      <button type="button" className={className} disabled={ws.progress !== null} onClick={() => dialog.current?.showModal()}>
        Delete my files now
      </button>
      <dialog ref={dialog} className="crop-dialog confirm-dialog" aria-labelledby="delete-title" aria-describedby="delete-text">
        <h2 id="delete-title">Delete your files now?</h2>
        <p id="delete-text" className="muted">Every file and result from this browser is removed from the server. This cannot be undone.</p>
        <span className="crop-actions">
          <button type="button" className="btn secondary" onClick={() => dialog.current?.close()}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              dialog.current?.close();
              ws.deleteAll();
            }}
          >
            Delete my files
          </button>
        </span>
      </dialog>
    </>
  );
}

/** A label that opens the file picker on click or Enter/Space (via its hidden input) and takes dropped files. */
export function FilePick(props: {
  accept: string;
  multiple: boolean;
  onFiles: (files: FileList | null) => void;
  className: string;
  disabled?: boolean;
  name?: string;
  children: ReactNode;
}) {
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (!props.disabled) props.onFiles(e.dataTransfer.files);
  };
  return (
    <label
      className={`${props.className}${over ? " over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!props.disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <input
        type="file"
        className="sr-only"
        multiple={props.multiple}
        accept={props.accept}
        disabled={props.disabled}
        aria-label={props.name}
        onChange={(e) => {
          props.onFiles(e.target.files);
          e.target.value = ""; // lets the same file be picked again
        }}
      />
      {props.children}
    </label>
  );
}

const PATHS = {
  funnel: "M4 5h16l-6.5 7.5V19l-3-1.5v-5z",
  upload: "M12 15V4M7.5 8.5 12 4l4.5 4.5M5 20h14",
  download: "M12 4v11M7.5 10.5 12 15l4.5-4.5M5 20h14",
  image: "M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM10.5 9a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zM4.5 17.5l5-5 4 4 2-2 4 4",
  pages: "M10 8h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2zM16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2",
  compress: "M12 3v6M9 6.5 12 9.5l3-3M12 21v-6M9 17.5l3-3 3 3M5 12h14",
  file: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5",
  close: "M6.5 6.5l11 11M17.5 6.5l-11 11",
  back: "M19 12H5M11 6l-6 6 6 6",
  warning: "M12 4 2.8 19.5h18.4zM12 10v4.5M12 17v.5",
  rotate: "M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4",
  down: "M6 9l6 6 6-6",
  plus: "M12 5v14M5 12h14",
  forward: "M5 12h14M13 6l6 6-6 6",
  crop: "M7 3v12a2 2 0 0 0 2 2h12M3 7h12a2 2 0 0 1 2 2v12",
  grip: "M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01",
  sliders: "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
};
type IconName = keyof typeof PATHS;

export function Icon({ name }: { name: IconName }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d={PATHS[name]} />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="logo">
      <Icon name="funnel" />
    </span>
  );
}

const STEPS = ["Drop", "Edit", "Build", "Collect"];

/** Drop → Edit → Build → Collect; `at` is the index of the current step. */
export function Steps({ at, failed = false }: { at: number; failed?: boolean }) {
  return (
    <ol className="steps" aria-label="Progress">
      {STEPS.map((s, n) => (
        <li
          key={s}
          className={n < at ? "done" : n === at ? (failed ? "now failed" : "now") : ""}
          aria-current={n === at ? "step" : undefined}
        >
          {s}
        </li>
      ))}
    </ol>
  );
}
