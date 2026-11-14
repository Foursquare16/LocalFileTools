import { FormEvent, useEffect, useRef, useState } from "react";
import { api, formatSize, HttpError, Job, uploadJob } from "./api";
import { Machine } from "./Machine";
import {
  DEFAULT_SETTINGS,
  DONE,
  fits,
  Icon,
  Crop,
  Item,
  Logo,
  MAX_BYTES,
  MAX_FILES,
  options,
  plural,
  Settings,
  ToolName,
  toolInAddress,
  totalSize,
  Workspace as WorkspaceState,
} from "./shared";
import { ToolScreen } from "./ToolScreen";

const SOURCE_URL = "https://github.com/Foursquare16/LocalFileTools";

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    // A guest link carries its token after the #, which browsers never send to the server or to other sites.
    const invite = new URLSearchParams(location.hash.slice(1)).get("invite");
    if (invite) history.replaceState(null, "", location.pathname);
    api<{ authenticated: boolean }>("/session")
      .then(async (s) => {
        if (s.authenticated || !invite) return s.authenticated;
        const body = JSON.stringify({ token: invite });
        await api("/invite", { method: "POST", headers: { "Content-Type": "application/json" }, body }).catch((err) => {
          setNotice((err as Error).message);
          throw err;
        });
        return true;
      })
      .then(setAuthed)
      .catch(() => setAuthed(false));
  }, []);

  async function logout() {
    await api("/logout", { method: "POST" }).catch(() => {});
    setAuthed(false);
  }

  return (
    <>
      <div className="ambient" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      {authed && (
        <header className="top">
          <span className="brand">
            <Logo />
            LocalFileTools
          </span>
          <button className="btn ghost" onClick={logout}>
            Log Out
          </button>
        </header>
      )}
      <main className={authed ? "" : "center"}>
        {authed === null ? null : authed ? (
          <Workspace onLoggedOut={() => setAuthed(false)} />
        ) : (
          <Login notice={notice} onLoggedIn={() => setAuthed(true)} />
        )}
      </main>
      <footer>
        Free Software (AGPL-3.0) · <a href={SOURCE_URL}>Source Code</a>
      </footer>
    </>
  );
}

function Login({ notice, onLoggedIn }: { notice: string; onLoggedIn: () => void }) {
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
    <form className="login card" onSubmit={submit}>
      <Logo />
      <div>
        <h1>Log In to LocalFileTools</h1>
        <p className="muted">Convert and compress files on your own server.</p>
      </div>
      {notice && (
        <p className="alert" role="alert">
          <Icon name="warning" />
          <span>
            {notice}
            {notice.includes("guest link") && " Ask the owner for a new one, or log in below."}
          </span>
        </p>
      )}
      <label className="field">
        <span>Username</span>
        <span className="input">
          <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </span>
      </label>
      <label className="field">
        <span>Password</span>
        <span className="input">
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </span>
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button className="btn primary" disabled={busy}>
        {busy ? "Logging In…" : "Log In"}
      </button>
    </form>
  );
}

let nextId = 0;

/**
 * Owns the files in the hopper, the settings and the last job. The machine (no hash) and the tool screens (#image,
 * #images_to_pdf, #pdf_compress) are views of this one state, so the browser's back key moves between them freely.
 */
function Workspace({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [view, setView] = useState(toolInAddress);
  const [items, setItems] = useState<Item[]>([]);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [job, setJob] = useState<Job | null>(null);
  const [jobTool, setJobTool] = useState<ToolName | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [failure, setFailure] = useState<WorkspaceState["failure"]>(null);
  const [error, setError] = useState("");
  // The one polite live region says the latest thing that happened: a job step, a failure, a page moved.
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const sync = () => setView(toolInAddress());
    addEventListener("hashchange", sync);
    return () => removeEventListener("hashchange", sync);
  }, []);

  // Move focus to the new screen's heading, so keyboard and screen reader users land at its start.
  const firstView = useRef(true);
  useEffect(() => {
    if (firstView.current) firstView.current = false;
    else document.querySelector<HTMLElement>("main h1")?.focus();
  }, [view]);

  // Revoke thumbnail URLs when the workspace goes away (logout).
  const itemsRef = useRef(items);
  itemsRef.current = items;
  useEffect(() => () => itemsRef.current.forEach((i) => URL.revokeObjectURL(i.url)), []);

  function fail(err: unknown) {
    if (err instanceof HttpError && err.status === 401) onLoggedOut();
    else setError((err as Error).message);
  }

  function clear() {
    setItems((old) => {
      old.forEach((i) => URL.revokeObjectURL(i.url));
      return [];
    });
  }

  function add(files: FileList | null, replace = false) {
    setError("");
    const added = [...(files ?? [])].map((file) => ({ id: nextId++, file, url: URL.createObjectURL(file), rotation: 0, crop: null }));
    if (!added.length) return;
    if (replace) {
      // The compressor takes one PDF: a new pick replaces it, and of several dropped only the last is kept.
      clear();
      added.slice(0, -1).forEach((i) => URL.revokeObjectURL(i.url));
    }
    setItems((old) => (replace ? added.slice(-1) : [...old, ...added]));
  }

  function remove(id: number) {
    setItems((old) => {
      old.filter((i) => i.id === id).forEach((i) => URL.revokeObjectURL(i.url));
      return old.filter((i) => i.id !== id);
    });
  }

  function moveTo(from: number, to: number) {
    setItems((old) => {
      const next = [...old];
      next.splice(to, 0, ...next.splice(from, 1));
      return next;
    });
  }

  // A quarter turn clockwise; a crop turns with the image, so it keeps covering the same part of the picture.
  function rotate(id: number) {
    const turn = (c: Crop | null): Crop | null => c && [1 - c[3], c[0], 1 - c[1], c[2]];
    setItems((old) => old.map((i) => (i.id === id ? { ...i, rotation: (i.rotation + 90) % 360, crop: turn(i.crop) } : i)));
  }

  function setCrop(id: number, crop: Crop | null) {
    setItems((old) => old.map((i) => (i.id === id ? { ...i, crop } : i)));
  }

  function start(tool: ToolName) {
    setError("");
    // Checked here too: a server rejection mid-upload can reach the browser through Cloudflare as a bare network error.
    const total = totalSize(items.map((i) => i.file));
    if (!items.length) return setError(tool === "pdf_compress" ? "Add a PDF." : "Add at least one image.");
    if (!fits(tool, items))
      return setError(
        tool === "pdf_compress" ? "This tool takes one PDF." : "This tool takes images only: JPEG, PNG, WebP, HEIC or AVIF.",
      );
    if (items.length > MAX_FILES) return setError(`Too many files: at most ${MAX_FILES} per job.`);
    if (total > MAX_BYTES) return setError(`Too large: ${formatSize(total)} selected, at most 50 MB per job.`);
    const previous = job;
    setFailure(null);
    setNotice("Uploading…");
    setJobTool(tool);
    setProgress(0);
    if (location.hash) location.hash = ""; // watch it go through the machine
    uploadJob(tool, options(tool, settings, items), items.map((i) => i.file), setProgress)
      .then((id) => {
        // The tray holds one job, so the previous result goes now rather than in an hour, but only once the new
        // job exists: if the upload fails, the previous result stays downloadable.
        if (previous) api(`/jobs/${previous.id}`, { method: "DELETE" }).catch(() => {});
        return api<Job>(`/jobs/${id}`);
      })
      .then(setJob)
      .catch((err) => {
        if (err instanceof HttpError && err.status === 401) onLoggedOut();
        else {
          setFailure({ message: (err as Error).message, stage: "Uploading" });
          setNotice(`Failed: ${(err as Error).message}`);
        }
      })
      .finally(() => setProgress(null));
  }

  const jobId = job?.id;
  const finished = job ? DONE.includes(job.status) : true;
  useEffect(() => {
    if (!jobId || finished) return;
    const timer = setInterval(() => api<Job>(`/jobs/${jobId}`).then(setJob, fail), 1000);
    return () => clearInterval(timer);
  }, [jobId, finished]);

  // A finished job empties the hopper; a failed one keeps the files for another try and is deleted on the server.
  const status = job?.status;
  useEffect(() => {
    if (!job) return;
    if (status === "completed") {
      clear();
      setNotice(`Done: ${plural(job.outputs.length, "file")} ready to download.`);
    }
    if (status === "failed" || status === "cancelled") {
      const message = status === "cancelled" ? "Cancelled." : (job.message ?? "Processing failed.");
      setFailure({ message, stage: job.stage });
      setNotice(`Failed: ${message}`);
      setJob(null);
      api(`/jobs/${job.id}`, { method: "DELETE" }).catch(() => {});
    }
  }, [status]);

  const stage = job?.stage;
  useEffect(() => {
    if (status === "queued") setNotice("Waiting in the queue…");
    else if (status === "processing") setNotice(stage ?? "Processing…");
  }, [status, stage]);

  /** Cancel or delete the job on the server; `fresh` also empties the hopper. */
  async function discard(fresh: boolean) {
    setError("");
    if (job) {
      try {
        await api(`/jobs/${job.id}`, { method: "DELETE" });
      } catch (err) {
        if (!(err instanceof HttpError && err.status === 404)) return fail(err);
      }
    }
    if (busy) setNotice("Cancelled.");
    setJob(null);
    setFailure(null);
    if (fresh) {
      clear();
      if (location.hash) location.hash = "";
    }
  }

  const busy = progress !== null || (!!job && !DONE.includes(job.status));
  const ws: WorkspaceState = {
    items,
    settings,
    job,
    jobTool,
    progress,
    failure,
    error,
    busy,
    set: (patch) => setSettings((s) => ({ ...s, ...patch })),
    add,
    remove,
    moveTo,
    rotate,
    setCrop,
    say: setNotice,
    clear,
    start,
    discard,
  };

  return (
    <>
      <p className="sr-only" aria-live="polite">
        {notice}
      </p>
      {view ? <ToolScreen key={view} tool={view} ws={ws} /> : <Machine ws={ws} />}
    </>
  );
}
