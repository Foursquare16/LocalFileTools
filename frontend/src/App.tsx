import { DragEvent, FormEvent, useEffect, useRef, useState } from "react";
import { api, formatSize, HttpError, Job, uploadJob } from "./api";

const SOURCE_URL = "https://github.com/Foursquare16/LocalFileTools";

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    api<{ authenticated: boolean }>("/session")
      .then((s) => setAuthed(s.authenticated))
      .catch(() => setAuthed(false));
  }, []);

  async function logout() {
    await api("/logout", { method: "POST" }).catch(() => {});
    setAuthed(false);
  }

  if (authed === null) return null;
  return (
    <>
      <header>
        <h1>LocalFileTools</h1>
        {authed && (
          <button className="link" onClick={logout}>
            Log out
          </button>
        )}
      </header>
      <main>{authed ? <Tools onLoggedOut={() => setAuthed(false)} /> : <Login onLoggedIn={() => setAuthed(true)} />}</main>
      <footer>
        Free software (AGPL-3.0): <a href={SOURCE_URL}>source code</a>
      </footer>
    </>
  );
}

function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      onLoggedIn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <label>
        Username
        <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </label>
      <label>
        Password
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button disabled={busy}>Log in</button>
    </form>
  );
}

type ToolName = "image" | "images_to_pdf" | "pdf_compress";

const TOOLS: { name: ToolName; title: string; text: string }[] = [
  { name: "image", title: "Image tool", text: "Convert between JPEG, PNG and WebP, resize, and compress to a quality or a target size." },
  { name: "images_to_pdf", title: "Images → PDF", text: "Put several images into one PDF, in the order and orientation you choose." },
  { name: "pdf_compress", title: "PDF compressor", text: "Make a PDF smaller with a compression strength or a target size." },
];

function Tools({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [tool, setTool] = useState<ToolName | null>(null);
  if (tool) return <ToolScreen key={tool} tool={tool} onBack={() => setTool(null)} onLoggedOut={onLoggedOut} />;
  return (
    <div className="cards">
      {TOOLS.map((t) => (
        <button key={t.name} className="card tool" onClick={() => setTool(t.name)}>
          <strong>{t.title}</strong>
          <span>{t.text}</span>
        </button>
      ))}
    </div>
  );
}

// shortcut: mirrors the server defaults (LFT_MAX_FILES, LFT_MAX_UPLOAD_MB); the server still enforces its own values.
const MAX_FILES = 30;
const MAX_BYTES = 50 * 1024 * 1024 - 64 * 1024; // leaves room for the multipart overhead in the request body
const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,image/avif,.heic,.heif,.avif";
const ACCEPT_PDF = "application/pdf,.pdf";
const DONE = ["completed", "failed", "cancelled"];

type Item = { id: number; file: File; url: string; rotation: number };
let nextId = 0;

function ToolScreen({ tool, onBack, onLoggedOut }: { tool: ToolName; onBack: () => void; onLoggedOut: () => void }) {
  const title = TOOLS.find((t) => t.name === tool)!.title;
  const [items, setItems] = useState<Item[]>([]);
  const [format, setFormat] = useState("jpeg");
  const [maxWidth, setMaxWidth] = useState("");
  const [maxHeight, setMaxHeight] = useState("");
  const [mode, setMode] = useState<"quality" | "target">("quality");
  const [quality, setQuality] = useState(85);
  const [targetKb, setTargetKb] = useState("500");
  const [page, setPage] = useState("a4");
  const [margin, setMargin] = useState("10");
  const [strength, setStrength] = useState("medium");
  const [progress, setProgress] = useState<number | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");

  // Revoke thumbnail URLs when the screen goes away.
  const itemsRef = useRef(items);
  itemsRef.current = items;
  useEffect(() => () => itemsRef.current.forEach((i) => URL.revokeObjectURL(i.url)), []);

  function fail(err: unknown) {
    if (err instanceof HttpError && err.status === 401) onLoggedOut();
    else setError((err as Error).message);
  }

  function add(files: FileList | null) {
    setError("");
    const added = [...(files ?? [])].map((file) => ({ id: nextId++, file, url: URL.createObjectURL(file), rotation: 0 }));
    if (tool === "pdf_compress") clear(); // the compressor takes one PDF: a new pick replaces it
    setItems((old) => [...old, ...added].slice(tool === "pdf_compress" ? -1 : 0));
  }

  function remove(id: number) {
    setItems((old) => {
      old.filter((i) => i.id === id).forEach((i) => URL.revokeObjectURL(i.url));
      return old.filter((i) => i.id !== id);
    });
  }

  function move(index: number, by: number) {
    setItems((old) => {
      const next = [...old];
      [next[index], next[index + by]] = [next[index + by], next[index]];
      return next;
    });
  }

  function rotate(id: number) {
    setItems((old) => old.map((i) => (i.id === id ? { ...i, rotation: (i.rotation + 90) % 360 } : i)));
  }

  function clear() {
    items.forEach((i) => URL.revokeObjectURL(i.url));
    setItems([]);
  }

  function options() {
    if (tool === "pdf_compress") return mode === "target" ? { target_kb: Number(targetKb) } : { strength };
    if (tool === "images_to_pdf") return { page, margin_mm: Number(margin), rotations: items.map((i) => i.rotation) };
    const opts: Record<string, unknown> = { format };
    if (maxWidth) opts.max_width = Number(maxWidth);
    if (maxHeight) opts.max_height = Number(maxHeight);
    if (format !== "png") {
      if (mode === "target") opts.target_kb = Number(targetKb);
      else opts.quality = quality;
    }
    return opts;
  }

  async function start(e: FormEvent) {
    e.preventDefault();
    setError("");
    // Checked here too: a server rejection mid-upload can reach the browser through Cloudflare as a bare network error.
    const total = items.reduce((sum, i) => sum + i.file.size, 0);
    if (!items.length) return setError(compress ? "Add a PDF." : "Add at least one image.");
    if (items.length > MAX_FILES) return setError(`Too many files: at most ${MAX_FILES} per job.`);
    if (total > MAX_BYTES) return setError(`Too large: ${formatSize(total)} selected, at most 50 MB per job.`);
    setProgress(0);
    try {
      const id = await uploadJob(tool, options(), items.map((i) => i.file), setProgress);
      setJob(await api<Job>(`/jobs/${id}`));
    } catch (err) {
      fail(err);
    } finally {
      setProgress(null);
    }
  }

  const jobId = job?.id;
  const finished = job ? DONE.includes(job.status) : true;
  useEffect(() => {
    if (!jobId || finished) return;
    const timer = setInterval(() => api<Job>(`/jobs/${jobId}`).then(setJob, fail), 1000);
    return () => clearInterval(timer);
  }, [jobId, finished]);

  /** Cancel or delete the job on the server, then go back to editing (keeping the files unless `fresh`). */
  async function discard(fresh: boolean) {
    try {
      await api(`/jobs/${jobId}`, { method: "DELETE" });
    } catch (err) {
      if (!(err instanceof HttpError && err.status === 404)) return fail(err);
    }
    setJob(null);
    if (fresh) clear();
  }

  if (job) return <JobView title={title} job={job} onDiscard={discard} />;

  const pdf = tool === "images_to_pdf";
  const compress = tool === "pdf_compress";
  const uploading = progress !== null;
  return (
    <form className="card" onSubmit={start}>
      <div className="row">
        <h2>{title}</h2>
        <button type="button" className="link" onClick={onBack}>
          ← All tools
        </button>
      </div>
      <Dropzone onFiles={add} pdf={compress} />
      {items.length > 0 && (
        <ul className="files">
          {items.map((item, n) => (
            <li key={item.id}>
              {!compress && <img src={item.url} alt="" loading="lazy" decoding="async" style={{ transform: `rotate(${item.rotation}deg)` }} onError={(e) => (e.currentTarget.style.visibility = "hidden")} />}
              <span className="name">
                {pdf && `${n + 1}. `}
                {item.file.name}
                <small>{formatSize(item.file.size)}</small>
              </span>
              <span className="actions">
                {pdf && (
                  <>
                    <button type="button" disabled={n === 0} onClick={() => move(n, -1)} aria-label={`Move ${item.file.name} up`}>
                      ↑
                    </button>
                    <button type="button" disabled={n === items.length - 1} onClick={() => move(n, 1)} aria-label={`Move ${item.file.name} down`}>
                      ↓
                    </button>
                    <button type="button" onClick={() => rotate(item.id)} aria-label={`Rotate ${item.file.name} clockwise (now ${item.rotation}°)`}>
                      ↻
                    </button>
                  </>
                )}
                <button type="button" onClick={() => remove(item.id)} aria-label={`Remove ${item.file.name}`}>
                  ✕
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <fieldset disabled={uploading}>
        <legend>Settings</legend>
        {compress ? (
          <>
            <div className="radios" role="radiogroup" aria-label="Compression">
              <label className="inline">
                <input type="radio" name="mode" checked={mode === "quality"} onChange={() => setMode("quality")} /> Strength
              </label>
              <label className="inline">
                <input type="radio" name="mode" checked={mode === "target"} onChange={() => setMode("target")} /> Target size
              </label>
            </div>
            {mode === "quality" ? (
              <label>
                Strength
                <select value={strength} onChange={(e) => setStrength(e.target.value)}>
                  <option value="low">Low (print quality, 300 dpi images)</option>
                  <option value="medium">Medium (ebook, 150 dpi images)</option>
                  <option value="high">High (screen, 72 dpi images)</option>
                </select>
              </label>
            ) : (
              <label>
                Target size (KB)
                <input type="number" min={1} max={1000000} value={targetKb} onChange={(e) => setTargetKb(e.target.value)} required />
              </label>
            )}
            <p className="hint">
              Best effort: you get the smallest result that beats the original, and the original back if nothing does. Text
              stays text. Encrypted, signed and fillable-form PDFs are not supported.
            </p>
          </>
        ) : pdf ? (
          <>
            <label>
              Page size
              <select value={page} onChange={(e) => setPage(e.target.value)}>
                <option value="a4">A4 (turned to fit each image)</option>
                <option value="image">Same as the image</option>
              </select>
            </label>
            <label>
              Margin (mm)
              <input type="number" min={0} max={50} step={1} value={margin} onChange={(e) => setMargin(e.target.value)} required />
            </label>
          </>
        ) : (
          <>
            <label>
              Output format
              <select value={format} onChange={(e) => setFormat(e.target.value)}>
                <option value="jpeg">JPEG</option>
                <option value="png">PNG</option>
                <option value="webp">WebP</option>
              </select>
            </label>
            <div className="row">
              <label>
                Max width (px)
                <input type="number" min={1} max={20000} value={maxWidth} onChange={(e) => setMaxWidth(e.target.value)} placeholder="Keep" />
              </label>
              <label>
                Max height (px)
                <input type="number" min={1} max={20000} value={maxHeight} onChange={(e) => setMaxHeight(e.target.value)} placeholder="Keep" />
              </label>
            </div>
            <p className="hint">Resizing keeps proportions and only ever makes images smaller.</p>
            {format === "png" ? (
              <p className="hint">Quality and target size work for JPEG and WebP only: PNG has no quality setting.</p>
            ) : (
              <>
                <div className="radios" role="radiogroup" aria-label="Compression">
                  <label className="inline">
                    <input type="radio" name="mode" checked={mode === "quality"} onChange={() => setMode("quality")} /> Quality
                  </label>
                  <label className="inline">
                    <input type="radio" name="mode" checked={mode === "target"} onChange={() => setMode("target")} /> Target size
                  </label>
                </div>
                {mode === "quality" ? (
                  <label>
                    Quality: {quality}
                    <input type="range" min={1} max={100} value={quality} onChange={(e) => setQuality(Number(e.target.value))} />
                  </label>
                ) : (
                  <label>
                    Target size per image (KB)
                    <input type="number" min={1} max={1000000} value={targetKb} onChange={(e) => setTargetKb(e.target.value)} required />
                    <span className="hint">Best effort (JPEG and WebP only): if it can't be reached, you get the smallest result.</span>
                  </label>
                )}
              </>
            )}
          </>
        )}
      </fieldset>
      {error && <p className="error" role="alert">{error}</p>}
      {uploading ? (
        <progress value={progress} max={1} aria-label="Upload progress" />
      ) : (
        <button disabled={!items.length}>{compress ? "Compress" : pdf ? "Make PDF" : "Convert"}</button>
      )}
    </form>
  );
}

function Dropzone({ onFiles, pdf }: { onFiles: (files: FileList | null) => void; pdf: boolean }) {
  const [over, setOver] = useState(false);
  function drop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    onFiles(e.dataTransfer.files);
  }
  return (
    <label
      className={over ? "dropzone over" : "dropzone"}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <input
        type="file"
        multiple={!pdf}
        accept={pdf ? ACCEPT_PDF : ACCEPT}
        onChange={(e) => {
          onFiles(e.target.files);
          e.target.value = ""; // lets the same file be picked again
        }}
      />
      {pdf ? (
        <>
          <span>Drop a PDF here or <u>choose a file</u></span>
          <small>One PDF · up to 50 MB and 100 pages</small>
        </>
      ) : (
        <>
          <span>Drop images here or <u>choose files</u></span>
          <small>JPEG, PNG, WebP, HEIC or AVIF · up to {MAX_FILES} files, 50 MB in total</small>
        </>
      )}
    </label>
  );
}

function JobView({ title, job, onDiscard }: { title: string; job: Job; onDiscard: (fresh: boolean) => void }) {
  const before = job.inputs.reduce((sum, f) => sum + f.size, 0);
  const after = job.outputs.reduce((sum, f) => sum + f.size, 0);
  const paired = job.outputs.length === job.inputs.length;
  const base = `/api/jobs/${job.id}`;

  return (
    <section className="card" aria-live="polite">
      <h2>{title}</h2>
      {!DONE.includes(job.status) && (
        <>
          <p>{job.status === "queued" ? "Waiting in the queue…" : job.stage ?? "Processing…"}</p>
          <progress aria-label="Processing" />
          <button onClick={() => onDiscard(false)}>Cancel</button>
        </>
      )}
      {job.status === "completed" && (
        <>
          <p className="summary">
            {formatSize(before)} → <strong>{formatSize(after)}</strong> <Saved before={before} after={after} />
          </p>
          {job.message && <p className="hint">{job.message}</p>}
          <ul className="files">
            {job.outputs.map((f, n) => (
              <li key={n}>
                <span className="name">
                  {f.name}
                  <small>
                    {paired ? `${formatSize(job.inputs[n].size)} → ` : ""}
                    {formatSize(f.size)} {paired && <Saved before={job.inputs[n].size} after={f.size} />}
                  </small>
                </span>
                <a className="button" href={`${base}/files/${n}`} download aria-label={`Download ${f.name}`}>
                  Download
                </a>
              </li>
            ))}
          </ul>
          <div className="row">
            {job.outputs.length > 1 && (
              <a className="button" href={`${base}/zip`} download>
                Download all (ZIP)
              </a>
            )}
            <button onClick={() => onDiscard(true)}>Delete files and start over</button>
          </div>
          <p className="hint">Files are deleted from the server automatically within an hour.</p>
        </>
      )}
      {(job.status === "failed" || job.status === "cancelled") && (
        <>
          <p className="error" role="alert">
            {job.status === "cancelled" ? "Cancelled." : job.message ?? "Processing failed."}
          </p>
          <button onClick={() => onDiscard(false)}>Back</button>
        </>
      )}
    </section>
  );
}

function Saved({ before, after }: { before: number; after: number }) {
  if (!before) return null;
  const pct = Math.round((1 - after / before) * 100);
  return <span className={pct >= 0 ? "saved" : "grew"}>{pct >= 0 ? `${pct}% saved` : `${-pct}% larger`}</span>;
}
