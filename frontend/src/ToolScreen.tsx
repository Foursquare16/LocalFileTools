import { InputHTMLAttributes, ReactNode, useState } from "react";
import { formatSize, Job } from "./api";
import { PageEditor } from "./PageEditor";
import {
  ACCEPT_IMAGES,
  ACCEPT_PDF,
  FilePick,
  fits,
  Icon,
  isPdf,
  Item,
  MAX_FILES,
  plural,
  Saved,
  sizeChange,
  statusText,
  Steps,
  ToolName,
  TOOLS,
  totalSize,
  Workspace,
} from "./shared";

const FAILED_TITLE: Record<ToolName, string> = {
  image: "These Images Could Not Be Converted",
  images_to_pdf: "The PDF Could Not Be Made",
  pdf_compress: "This PDF Could Not Be Compressed",
};

const STRENGTHS = [
  ["low", "Low", "Print quality, 300 dpi images"],
  ["medium", "Medium", "Ebook quality, 150 dpi images"],
  ["high", "High", "Screen quality, 72 dpi images"],
];

/** One tool's page (#image, #images_to_pdf, #pdf_compress): settings, then results or a failure. */
export function ToolScreen({ tool, ws }: { tool: ToolName; ws: Workspace }) {
  const title = TOOLS.find((t) => t.name === tool)!.title;
  const { items, job } = ws;
  const done = !ws.busy && job?.status === "completed" && ws.jobTool === tool && !items.length ? job : null;

  const head = (sub: string, step: number, failed = false) => (
    <div className="tool-head">
      <a className="back" href="#">
        <Icon name="back" />
        All Tools
      </a>
      <div className="title-row">
        <div>
          <h1 tabIndex={-1}>{title}</h1>
          <p className="muted">{sub}</p>
        </div>
        <Steps at={step} failed={failed} />
      </div>
    </div>
  );

  if (ws.busy)
    return (
      <div className="tool-page">
        <div className="tool-main">
          {head("Your files are in the machine", 2)}
          <div className="card status-card">
            <p className="status-line">
              {statusText(ws)}
              {ws.progress !== null && ` ${Math.round(ws.progress * 100)}%`}
            </p>
            {ws.progress !== null ? (
              <progress value={ws.progress} max={1} aria-label="Upload progress" />
            ) : (
              <progress aria-label="Processing" />
            )}
            <div className="actions">
              <a className="btn secondary" href="#">
                Watch the Machine
              </a>
              {ws.progress === null && (
                <button className="btn ghost" onClick={() => ws.discard(false)}>
                  Cancel
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    );

  if (done) return <Results done={done} head={head(`${plural(done.outputs.length, "file")} ready to download`, 3)} ws={ws} />;

  const s = ws.settings;
  const compress = tool === "pdf_compress";
  const pdf = tool === "images_to_pdf";
  const failure = ws.jobTool === tool ? ws.failure : null;
  const n = items.length;
  const total = totalSize(items.map((i) => i.file));
  const sub = n && !fits(tool, items)
    ? compress
      ? "This tool takes one PDF: replace or remove these files"
      : "This tool takes images only: remove the other files"
    : compress
    ? n
      ? "1 PDF · pick a strength or a target size"
      : "Add a PDF to begin"
    : !n
      ? "Add images to begin"
      : pdf
        ? `${plural(n, "page")} · drag to reorder, select a page to turn or crop it`
        : `${plural(n, "image")} · convert, resize and compress in one go`;

  return (
    <form
      className="tool-page tool-layout"
      onSubmit={(e) => {
        e.preventDefault();
        ws.start(tool);
      }}
    >
      <div className="tool-main">
        {head(sub, failure ? 2 : 1, !!failure)}
        {failure && (
          <section className="failure" aria-labelledby="failure-title">
            <div className="failure-head">
              <span className="tile">
                <Icon name="warning" />
              </span>
              <h2 id="failure-title">{FAILED_TITLE[tool]}</h2>
            </div>
            <p>{failure.message}</p>
            {failure.stage && <p className="muted small">Stopped at: {failure.stage}</p>}
            <div className="actions">
              <span className="muted small">Change the settings and try again, or</span>
              <button type="button" className="btn ghost" onClick={() => ws.discard(true)}>
                Start Over
              </button>
            </div>
          </section>
        )}

        {compress ? (
          <>
            {n === 0 ? (
              <FilePick className="pick-row" accept={ACCEPT_PDF} multiple={false} onFiles={(f) => ws.add(f, true)}>
                <Icon name="upload" />
                <span>
                  <strong>Choose a PDF</strong>
                  <small>Drop it here or choose a file · one PDF, up to 50 MB and 100 pages</small>
                </span>
              </FilePick>
            ) : (
              <ul className="rows">
                {items.map((item) => (
                  <FileRow key={item.id} item={item} ws={ws}>
                    <FilePick
                      className="btn secondary"
                      accept={ACCEPT_PDF}
                      multiple={false}
                      name={`Replace ${item.file.name}`}
                      onFiles={(f) => ws.add(f, true)}
                    >
                      Replace
                    </FilePick>
                  </FileRow>
                ))}
              </ul>
            )}
            {s.mode === "quality" ? (
              <fieldset className="option-cards">
                <legend>Compression Strength</legend>
                {STRENGTHS.map(([value, label, text]) => (
                  <label key={value} className="option">
                    <input
                      type="radio"
                      className="sr-only"
                      name="strength"
                      checked={s.strength === value}
                      onChange={() => ws.set({ strength: value })}
                    />
                    <strong>{label}</strong>
                    <small>{text}</small>
                  </label>
                ))}
              </fieldset>
            ) : (
              <Field
                label="Target Size"
                suffix="KB"
                type="number"
                min={1}
                max={1000000}
                value={s.targetKb}
                onChange={(e) => ws.set({ targetKb: e.target.value })}
                required
              />
            )}
            <p className="hint-card">
              Best effort: you get the smallest result that beats the original, and the original back if nothing does.
              Text stays text. Encrypted, signed and fillable-form PDFs are not supported.
            </p>
          </>
        ) : pdf && n > 0 ? (
          <PageEditor ws={ws} />
        ) : (
          <>
            <FilePick className="pick-row" accept={ACCEPT_IMAGES} multiple onFiles={(f) => ws.add(f)}>
              <Icon name="upload" />
              <span>
                <strong>{n ? "Add More Images" : "Add Images"}</strong>
                <small>
                  Drop here or choose files · {n} of {MAX_FILES} added
                </small>
              </span>
            </FilePick>
            <p className="muted small">JPEG, PNG, WebP, HEIC or AVIF · up to {MAX_FILES} files, 50 MB in total</p>
            {n > 0 && (
              <ul className="rows">
                {items.map((item) => (
                  <FileRow key={item.id} item={item} ws={ws} />
                ))}
              </ul>
            )}
          </>
        )}
      </div>

      <aside className="panel" id="settings" tabIndex={-1}>
        <h2>Settings</h2>
        {compress ? (
          <>
            <Segmented
              legend="Mode"
              value={s.mode}
              options={[
                ["quality", "Strength"],
                ["target", "Target Size"],
              ]}
              onChange={(mode) => ws.set({ mode: mode as "quality" | "target" })}
            />
            <p className="split">
              <span>Original Size</span>
              <span>{n ? formatSize(total) : "–"}</span>
            </p>
          </>
        ) : pdf ? (
          <>
            <label className="field">
              <span>Page Size</span>
              <span className="input">
                <select value={s.page} onChange={(e) => ws.set({ page: e.target.value })}>
                  <option value="a4">A4, turned to fit each image</option>
                  <option value="image">Same as the image</option>
                </select>
                <Icon name="down" />
              </span>
            </label>
            <Field
              label="Margin"
              suffix="mm"
              type="number"
              min={0}
              max={50}
              step={1}
              value={s.margin}
              onChange={(e) => ws.set({ margin: e.target.value })}
              required
            />
            <Field
              label="File Name"
              suffix=".pdf"
              maxLength={100}
              value={s.name}
              onChange={(e) => ws.set({ name: e.target.value })}
              placeholder="images"
            />
          </>
        ) : (
          <>
            <Segmented
              legend="Output Format"
              value={s.format}
              options={[
                ["jpeg", "JPEG"],
                ["png", "PNG"],
                ["webp", "WebP"],
              ]}
              onChange={(format) => ws.set({ format })}
            />
            <fieldset className="group">
              <legend>Resize</legend>
              <div className="pair">
                <Field
                  label="Max Width"
                  suffix="px"
                  type="number"
                  min={1}
                  max={20000}
                  placeholder="Keep"
                  value={s.maxWidth}
                  onChange={(e) => ws.set({ maxWidth: e.target.value })}
                />
                <Field
                  label="Max Height"
                  suffix="px"
                  type="number"
                  min={1}
                  max={20000}
                  placeholder="Keep"
                  value={s.maxHeight}
                  onChange={(e) => ws.set({ maxHeight: e.target.value })}
                />
              </div>
              <p className="muted small">Keeps proportions and only ever makes images smaller.</p>
            </fieldset>
            {s.format === "png" ? (
              <p className="muted small">Quality and target size work for JPEG and WebP only: PNG has no quality setting.</p>
            ) : (
              <>
                <Segmented
                  legend="Compression"
                  value={s.mode}
                  options={[
                    ["quality", "Quality"],
                    ["target", "Target Size"],
                  ]}
                  onChange={(mode) => ws.set({ mode: mode as "quality" | "target" })}
                />
                {s.mode === "quality" ? (
                  <label className="field">
                    <span className="split">
                      <span>Quality</span>
                      <span className="value">{s.quality}</span>
                    </span>
                    <input
                      type="range"
                      min={1}
                      max={100}
                      value={s.quality}
                      onChange={(e) => ws.set({ quality: Number(e.target.value) })}
                    />
                  </label>
                ) : (
                  <>
                    <Field
                      label="Target Size per Image"
                      suffix="KB"
                      type="number"
                      min={1}
                      max={1000000}
                      value={s.targetKb}
                      onChange={(e) => ws.set({ targetKb: e.target.value })}
                      required
                    />
                    <p className="muted small">Best effort (JPEG and WebP only): if it can't be reached, you get the smallest result.</p>
                  </>
                )}
              </>
            )}
          </>
        )}
        <p className="split sum">
          <span>{compress ? plural(n, "PDF") : plural(n, pdf ? "page" : "image")}</span>
          <span>{formatSize(total)} to upload</span>
        </p>
        {ws.error && (
          <p className="error" role="alert">
            {ws.error}
          </p>
        )}
        <button className="btn primary" disabled={!n}>
          {compress ? "Compress PDF" : pdf ? "Make PDF" : n ? `Convert ${plural(n, "Image")}` : "Convert"}
        </button>
        <p className="muted small">Files are deleted from the server after about an hour.</p>
      </aside>
    </form>
  );
}

function Results({ done, head, ws }: { done: Job; head: ReactNode; ws: Workspace }) {
  const before = totalSize(done.inputs);
  const after = totalSize(done.outputs);
  const paired = done.outputs.length === done.inputs.length;
  const base = `/api/jobs/${done.id}`;
  return (
    <div className="tool-page tool-layout">
      <div className="tool-main">
        {head}
        <ul className="rows">
          {done.outputs.map((f, n) => (
            <li key={n} className="row-item">
              <span className="thumb">
                <Icon name="file" />
              </span>
              <span className="name">
                <strong>{f.name}</strong>
                <small>{paired ? sizeChange(done.inputs[n].size, f.size) : formatSize(f.size)}</small>
              </span>
              {paired && (
                <span className="tag">
                  <Saved before={done.inputs[n].size} after={f.size} />
                </span>
              )}
              <a className="btn secondary" href={`${base}/files/${n}`} download aria-label={`Download ${f.name}`}>
                <Icon name="download" />
                Download
              </a>
            </li>
          ))}
        </ul>
      </div>
      <aside className="panel">
        <h2>Summary</h2>
        <div>
          <p className="big">{sizeChange(before, after)}</p>
          <p className="muted">
            <Saved before={before} after={after} /> across {plural(done.outputs.length, "file")}
          </p>
        </div>
        {done.message && <p className="muted small">{done.message}</p>}
        <hr />
        {done.outputs.length > 1 ? (
          <a className="btn primary" href={`${base}/zip`} download>
            Download All (ZIP)
          </a>
        ) : (
          <a className="btn primary" href={`${base}/files/0`} download>
            Download
          </a>
        )}
        <button className="btn secondary" onClick={() => ws.discard(true)}>
          Delete Files and Start Over
        </button>
        <p className="muted small">Files are deleted from the server after about an hour.</p>
      </aside>
    </div>
  );
}

function FileRow({ item, ws, children }: { item: Item; ws: Workspace; children?: ReactNode }) {
  const [dims, setDims] = useState("");
  const [broken, setBroken] = useState(isPdf(item.file));
  const name = item.file.name;
  return (
    <li className="row-item">
      <span className="thumb">
        {broken ? (
          <Icon name={isPdf(item.file) ? "file" : "image"} />
        ) : (
          <img
            src={item.url}
            alt=""
            loading="lazy"
            decoding="async"
            onLoad={(e) => setDims(`${e.currentTarget.naturalWidth} × ${e.currentTarget.naturalHeight}`)}
            onError={() => setBroken(true)}
          />
        )}
      </span>
      <span className="name">
        <strong>{name}</strong>
        <small>
          {isPdf(item.file) && "PDF · "}
          {formatSize(item.file.size)}
          {dims && ` · ${dims}`}
        </small>
      </span>
      <span className="actions">
        {children}
        <button type="button" className="icon-btn" onClick={() => ws.remove(item.id)} aria-label={`Remove ${name}`}>
          <Icon name="close" />
        </button>
      </span>
    </li>
  );
}

export function Segmented(props: { legend: string; value: string; options: string[][]; onChange: (value: string) => void }) {
  return (
    <fieldset className="group">
      <legend>{props.legend}</legend>
      <div className="seg">
        {props.options.map(([value, label]) => (
          <label key={value}>
            <input
              type="radio"
              className="sr-only"
              name={props.legend}
              checked={props.value === value}
              onChange={() => props.onChange(value)}
            />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function Field({ label, suffix, ...input }: { label: string; suffix?: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="field">
      <span>{label}</span>
      <span className="input">
        <input {...input} />
        {suffix && <span className="suffix">{suffix}</span>}
      </span>
    </label>
  );
}
