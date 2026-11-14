import { useEffect, useRef, useState } from "react";
import { api, formatSize, HttpError, isClosed, Job, uploadJob } from "./api";
import { version } from "../package.json";
import { Machine } from "./Machine";
import { Closed, Licences, NotFound, Privacy } from "./Pages";
import {
  DEFAULT_SETTINGS,
  DONE,
  fits,
  Crop,
  Item,
  Logo,
  MAX_BYTES,
  MAX_FILES,
  options,
  pageInAddress,
  plural,
  Settings,
  SOURCE_URL,
  ToolName,
  totalSize,
  Workspace as WorkspaceState,
} from "./shared";
import { ToolScreen } from "./ToolScreen";

export default function App() {
  const [open, setOpen] = useState<boolean | null>(null);

  useEffect(() => {
    api<{ open: boolean }>("/session").then(
      (s) => setOpen(s.open),
      (err) => setOpen(!isClosed(err)),
    );
  }, []);

  return (
    <>
      <div className="ambient" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      <header className="top">
        <span className="brand">
          <Logo />
          LocalFileTools
        </span>
      </header>
      <main>{open === null ? null : open ? <Workspace onClosed={() => setOpen(false)} /> : <Closed />}</main>
      <footer>
        <span>LocalFileTools {version}</span>
        <a href={SOURCE_URL} rel="noreferrer">
          Source
        </a>
        <a href="/#privacy">Privacy and terms</a>
        <a href="/#licences">Licences</a>
      </footer>
    </>
  );
}

let nextId = 0;

/**
 * Owns the files in the hopper, the settings and the last job. The machine (no hash) and the tool screens (#image,
 * #images_to_pdf, #pdf_compress) are views of this one state, so the browser's back key moves between them freely.
 */
function Workspace({ onClosed }: { onClosed: () => void }) {
  const [page, setPage] = useState(pageInAddress);
  const [items, setItems] = useState<Item[]>([]);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [job, setJob] = useState<Job | null>(null);
  const [jobTool, setJobTool] = useState<ToolName | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [failure, setFailure] = useState<WorkspaceState["failure"]>(null);
  const [error, setError] = useState("");
  const [deleted, setDeleted] = useState(false); // "Your files have been deleted." stays on the home screen until the next add or job
  // The one polite live region says the latest thing that happened: a job step, a failure, a page moved.
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const sync = () => setPage(pageInAddress());
    addEventListener("hashchange", sync);
    return () => removeEventListener("hashchange", sync);
  }, []);

  // Move focus to the new screen's heading, so keyboard and screen reader users land at its start.
  const firstView = useRef(true);
  useEffect(() => {
    if (firstView.current) firstView.current = false;
    else document.querySelector<HTMLElement>("main h1")?.focus();
  }, [page]);

  // Revoke thumbnail URLs when the workspace goes away (the service closed).
  const itemsRef = useRef(items);
  itemsRef.current = items;
  useEffect(() => () => itemsRef.current.forEach((i) => URL.revokeObjectURL(i.url)), []);

  function fail(err: unknown) {
    if (isClosed(err)) onClosed();
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
    setDeleted(false);
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
    setDeleted(false);
    // Checked here too: a server rejection mid-upload can reach the browser through Cloudflare as a bare network error.
    const total = totalSize(items.map((i) => i.file));
    if (!items.length) return setError(tool === "pdf_compress" ? "Add a PDF." : "Add at least one image.");
    if (!fits(tool, items))
      return setError(
        tool === "pdf_compress" ? "This tool takes one PDF." : "This tool takes images only: JPEG, PNG, WebP or AVIF.",
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
        // The tray holds one job, so the previous result goes now rather than after 15 minutes, but only once the new
        // job exists: if the upload fails, the previous result stays downloadable.
        if (previous) api(`/jobs/${previous.id}`, { method: "DELETE" }).catch(() => {});
        return api<Job>(`/jobs/${id}`);
      })
      .then(setJob)
      .catch((err) => {
        if (isClosed(err)) onClosed();
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

  /** Delete every job and file of this browser, forget the current job and go home. */
  async function deleteAll() {
    setError("");
    try {
      await api("/jobs", { method: "DELETE" });
    } catch (err) {
      return fail(err);
    }
    setJob(null);
    setJobTool(null);
    setFailure(null);
    if (location.hash) location.hash = "";
    setDeleted(true);
    setNotice("Your files have been deleted.");
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
    deleted,
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
    deleteAll,
  };

  return (
    <>
      <p className="sr-only" aria-live="polite">
        {notice}
      </p>
      {page === "privacy" ? (
        <Privacy />
      ) : page === "licences" ? (
        <Licences />
      ) : page === "notfound" ? (
        <NotFound />
      ) : page ? (
        <ToolScreen key={page} tool={page} ws={ws} />
      ) : (
        <Machine ws={ws} />
      )}
    </>
  );
}
