import { FormEvent, useEffect, useState } from "react";
import { api, formatSize, HttpError, Job, uploadJob } from "./api";

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    api<{ authenticated: boolean }>("/session")
      .then((s) => setAuthed(s.authenticated))
      .catch(() => setAuthed(false));
  }, []);

  if (authed === null) return null;
  return (
    <main>
      <h1>LocalFileTools</h1>
      {authed ? <EchoScreen onLoggedOut={() => setAuthed(false)} /> : <Login onLoggedIn={() => setAuthed(true)} />}
    </main>
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

const DONE = ["completed", "failed", "cancelled"];

// Stage 1 only: proves upload → queue → worker → download with the no-op echo tool.
function EchoScreen({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");

  function fail(err: unknown) {
    if (err instanceof HttpError && err.status === 401) onLoggedOut();
    else setError((err as Error).message);
  }

  async function start() {
    setError("");
    setProgress(0);
    try {
      const id = await uploadJob("echo", {}, files, setProgress);
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

  async function remove() {
    try {
      await api(`/jobs/${jobId}`, { method: "DELETE" });
      setJob(null);
      setFiles([]);
    } catch (err) {
      fail(err);
    }
  }

  async function logout() {
    await api("/logout", { method: "POST" }).catch(() => {});
    onLoggedOut();
  }

  return (
    <section className="card">
      <h2>Echo test</h2>
      {!job && (
        <>
          <input type="file" multiple onChange={(e) => setFiles([...(e.target.files ?? [])])} />
          {progress === null ? (
            <button disabled={!files.length} onClick={start}>
              Upload
            </button>
          ) : (
            <progress value={progress} max={1} aria-label="Upload progress" />
          )}
        </>
      )}
      {job && (
        <>
          <p>
            Status: <strong>{job.status}</strong>
            {job.stage && ` — ${job.stage}`}
          </p>
          {job.message && <p>{job.message}</p>}
          {job.status === "completed" && (
            <ul>
              {job.outputs.map((f, n) => (
                <li key={n}>
                  <a href={`/api/jobs/${job.id}/files/${n}`} download>
                    {f.name}
                  </a>{" "}
                  ({formatSize(f.size)})
                </li>
              ))}
            </ul>
          )}
          <button onClick={remove}>{finished ? "Delete" : "Cancel"}</button>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="link" onClick={logout}>
        Log out
      </button>
    </section>
  );
}
