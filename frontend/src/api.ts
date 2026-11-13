// Every request carries the custom header the backend's CSRF check requires.
const CSRF = { "X-LFT": "1" };

export type FileInfo = { name: string; size: number };
export type Job = {
  id: string;
  status: "queued" | "processing" | "completed" | "failed" | "cancelled";
  stage: string | null;
  message: string | null;
  inputs: FileInfo[];
  outputs: FileInfo[];
};

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function errorFrom(status: number, body: string): Promise<HttpError> {
  try {
    const detail = JSON.parse(body).detail;
    if (typeof detail === "string") return new HttpError(status, detail);
  } catch {}
  return new HttpError(status, `Request failed (${status})`);
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`/api${path}`, { ...init, headers: { ...CSRF, ...init.headers } });
  if (!r.ok) throw await errorFrom(r.status, await r.text());
  return (r.status === 204 ? undefined : await r.json()) as T;
}

/** POST /api/jobs via XMLHttpRequest, which (unlike fetch) reports upload progress. */
export function uploadJob(tool: string, options: object, files: File[], onProgress: (fraction: number) => void) {
  return new Promise<string>((resolve, reject) => {
    const form = new FormData();
    form.append("tool", tool);
    form.append("options", JSON.stringify(options));
    for (const f of files) form.append("files", f);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.setRequestHeader("X-LFT", "1");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = async () =>
      xhr.status === 201 ? resolve(JSON.parse(xhr.responseText).id) : reject(await errorFrom(xhr.status, xhr.responseText));
    xhr.onerror = () => reject(new HttpError(0, "Upload failed: network error"));
    xhr.send(form);
  });
}

export function formatSize(bytes: number) {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
