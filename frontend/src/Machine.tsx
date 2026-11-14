import { PointerEvent, ReactNode } from "react";
import {
  ACCEPT_IMAGES,
  ACCEPT_PDF,
  FilePick,
  fits,
  Icon,
  isImage,
  isPdf,
  Item,
  plural,
  Saved,
  sizeChange,
  statusText,
  TOOLS,
  totalSize,
  Workspace,
} from "./shared";

const MAX_CHIPS = 3;

/** What the files in the hopper are called: "3 Photos", "1 PDF" or "2 Files". */
function batchName(items: Item[]) {
  if (items.every((i) => isImage(i.file))) return plural(items.length, "Photo");
  if (items.every((i) => isPdf(i.file))) return plural(items.length, "PDF");
  return plural(items.length, "File");
}

/** Why nothing is lit, and which files are in the way (shown first, with a remove button). */
function diagnose(items: Item[]): { hint: string; problem: boolean } {
  const unsupported = items.filter((i) => !isImage(i.file) && !isPdf(i.file));
  const pdfs = items.filter((i) => isPdf(i.file)).length;
  if (unsupported.length)
    return {
      hint: `${unsupported.length === 1 ? `“${unsupported[0].file.name}” isn't` : `${unsupported.length} files aren't`} supported: use JPEG, PNG, WebP, HEIC, AVIF or PDF`,
      problem: true,
    };
  if (pdfs && pdfs < items.length) return { hint: "Photos and a PDF can't share a chute: remove one kind", problem: true };
  if (pdfs > 1) return { hint: "The compressor takes one PDF at a time: remove the others", problem: true };
  return { hint: pdfs ? "Pick the lit chute to continue" : "Pick a lit chute, or drop in more photos", problem: false };
}

const inTheWay = (i: Item) => !isImage(i.file) && !isPdf(i.file);

/** The home page: hopper, three chutes, collection tray. */
export function Machine({ ws }: { ws: Workspace }) {
  const { items, job, jobTool, busy, failure } = ws;
  // After a failed upload the previous result is still on the server (see start() in App.tsx): failure first.
  const previous = job?.status === "completed" ? job : null;
  const done = failure ? null : previous;
  // The hopper's files decide what is lit; with an empty hopper, the last job's chute stays lit.
  const byFit = items.length > 0 && !busy;
  const byJob = !byFit && !!jobTool && (busy || !!done || !!failure);
  const lit = TOOLS.map((t) => (byFit ? fits(t.name, items) : byJob && t.name === jobTool));
  const anyLit = lit.some(Boolean);
  const landed = TOOLS.map((_, n) => !!done && byJob && lit[n]);
  const jobTitle = TOOLS.find((t) => t.name === jobTool)?.title;
  const status = statusText(ws);
  const diagnosis = diagnose(items);
  // Files in the way first, so their remove buttons are on show; past three, two chips and a count.
  const ordered = [...items.filter(inTheWay), ...items.filter((i) => !inTheWay(i))];
  const chips = ordered.slice(0, items.length > MAX_CHIPS ? MAX_CHIPS - 1 : MAX_CHIPS);

  return (
    <div className={`machine${busy ? " running" : ""}`}>
      <h1 className="sr-only" tabIndex={-1}>
        LocalFileTools
      </h1>

      {items.length === 0 ? (
        <FilePick className="hopper idle" accept={`${ACCEPT_IMAGES},${ACCEPT_PDF}`} multiple onFiles={(f) => ws.add(f)}>
          <span className="tile">
            <Icon name="upload" />
          </span>
          <strong className="hopper-title">
            <span className="wide">Drop Files Into the Machine</span>
            <span className="narrow">Drop or Choose Files</span>
          </strong>
          <span className="muted">
            <span className="wide">or choose files · </span>images or a PDF, up to 50 MB
          </span>
        </FilePick>
      ) : (
        <FilePick
          className={`hopper ${busy ? "busy" : diagnosis.problem ? "full problem" : "full"}`}
          accept={`${ACCEPT_IMAGES},${ACCEPT_PDF}`}
          multiple
          disabled={busy}
          name="Add more files"
          onFiles={(f) => ws.add(f)}
        >
          <ul className="chips" aria-label="Files in the machine">
            {chips.map((i) => (
              <li key={i.id} className={`chip${diagnosis.problem && (inTheWay(i) || !items.some(inTheWay)) ? " removable" : ""}${inTheWay(i) ? " bad" : ""}`}>
                <Icon name={isPdf(i.file) ? "file" : "image"} />
                <span>{i.file.name}</span>
                {diagnosis.problem && !busy && (inTheWay(i) || !items.some(inTheWay)) && (
                  <button
                    type="button"
                    className="chip-x"
                    aria-label={`Remove ${i.file.name}`}
                    onClick={(e) => {
                      e.preventDefault(); // the hopper is a label: no file picker for this click
                      ws.remove(i.id);
                    }}
                  >
                    <Icon name="close" />
                  </button>
                )}
              </li>
            ))}
            {items.length > chips.length && <li className="chip">+{items.length - chips.length} more</li>}
          </ul>
          <strong className="hopper-title">
            {batchName(items)} {busy ? "in the Machine" : "Ready"}
          </strong>
          <span className={diagnosis.problem && !busy ? "hopper-hint problem" : "hopper-hint"}>
            {busy ? "You can leave this page open while it works" : diagnosis.hint}
            {!busy && (
              <>
                {" · "}
                <button
                  type="button"
                  className="link-btn"
                  onClick={(e) => {
                    e.preventDefault();
                    ws.clear();
                  }}
                >
                  Clear
                </button>
              </>
            )}
          </span>
        </FilePick>
      )}

      <Manifold lit={lit} any={anyLit} flowing={busy} />

      <div className={`chutes${anyLit ? " any" : ""}`}>
        {TOOLS.map((t, n) => {
          const dim = (byFit || byJob) && !lit[n];
          const running = busy && t.name === jobTool;
          return (
            <button
              key={t.name}
              className={`tool-card${lit[n] ? " lit" : ""}${dim ? " dim" : ""}${running ? " running" : ""}`}
              disabled={busy || (byFit && !lit[n])}
              onClick={() => (location.hash = t.name)}
              onPointerMove={tilt}
              onPointerLeave={untilt}
            >
              {byFit && lit[n] && (
                <span className="badge">{items.every((i) => isPdf(i.file)) ? "Fits Your PDF" : "Fits Your Photos"}</span>
              )}
              <span className="tile">
                <Icon name={t.icon} />
              </span>
              <span className="card-text">
                <strong>{t.title}</strong>
                <span className="desc">{t.text}</span>
                <span className="short">{t.short}</span>
                <span className="meta">{running ? status : t.meta}</span>
              </span>
            </button>
          );
        })}
      </div>

      <Manifold lit={landed} any={landed.some(Boolean)} join>
        {busy && (
          <div className="pill">
            <span>{status}</span>
            {ws.progress !== null ? (
              <>
                <progress value={ws.progress} max={1} aria-label="Upload progress" />
                <span>{Math.round(ws.progress * 100)}%</span>
              </>
            ) : (
              <progress aria-label="Processing" />
            )}
          </div>
        )}
      </Manifold>

      <section className={`tray${done || failure ? " full" : ""}${done ? " on" : ""}${failure ? " failed" : ""}`} aria-label="Finished files">
        {busy ? (
          <>
            <p className="muted">Your {jobTool === "image" ? "images" : "PDF"} will land here</p>
            {ws.progress === null && (
              <button className="btn ghost" onClick={() => ws.discard(false)}>
                Cancel
              </button>
            )}
          </>
        ) : failure ? (
          <>
            <p className="tray-error">
              <Icon name="warning" />
              <span>
                <strong>{jobTitle} stopped.</strong> {failure.message}
              </span>
            </p>
            <div className="tray-actions">
              <a className="btn secondary" href={`#${jobTool}`}>
                Back to Settings
              </a>
              <button className="btn ghost" onClick={() => ws.discard(true)}>
                Start Over
              </button>
            </div>
            {previous && (
              <p className="muted small">
                Your previous result is still here:{" "}
                <a className="link-btn" href={`/api/jobs/${previous.id}/${previous.outputs.length > 1 ? "zip" : "files/0"}`} download>
                  download it
                </a>
              </p>
            )}
          </>
        ) : done ? (
          <>
            {done.outputs.length === 1 ? (
              <div className="result">
                <span className="thumb">
                  <Icon name="file" />
                </span>
                <span className="name">
                  <strong>{done.outputs[0].name}</strong>
                  <small>
                    {sizeChange(totalSize(done.inputs), done.outputs[0].size)} ·{" "}
                    <Saved before={totalSize(done.inputs)} after={done.outputs[0].size} />
                  </small>
                </span>
                <a className="btn primary" href={`/api/jobs/${done.id}/files/0`} download aria-label={`Download ${done.outputs[0].name}`}>
                  Download
                </a>
              </div>
            ) : (
              <>
                <p className="result-sum">
                  <strong>{plural(done.outputs.length, "file")}</strong> ·{" "}
                  {sizeChange(totalSize(done.inputs), totalSize(done.outputs))} ·{" "}
                  <Saved before={totalSize(done.inputs)} after={totalSize(done.outputs)} />
                </p>
                <div className="tray-actions">
                  <a className="btn primary" href={`/api/jobs/${done.id}/zip`} download>
                    Download All (ZIP)
                  </a>
                  <a className="btn secondary" href={`#${jobTool}`}>
                    See Each File
                  </a>
                </div>
              </>
            )}
            {done.message && <p className="muted small">{done.message}</p>}
            <p className="muted small">
              <button className="link-btn" onClick={() => ws.discard(true)}>
                Start Over
              </button>{" "}
              · Files are deleted after about an hour
            </p>
          </>
        ) : (
          <p className="muted">Finished Files Land Here</p>
        )}
      </section>
      {ws.error && (
        <p className="error center-text" role="alert">
          {ws.error}
        </p>
      )}
    </div>
  );
}

/**
 * The pipes between hopper and chutes (split) or chutes and tray (join), drawn with borders:
 * a centre stem in two parts and an elbow out to each side chute.
 */
function Manifold(props: { lit: boolean[]; any: boolean; join?: boolean; flowing?: boolean; children?: ReactNode }) {
  const [left, centre, right] = props.lit;
  const on = (b: boolean) => (b ? " on" : "");
  // Split: the stem above the bar carries anything lit; below it only the centre chute. Join: the reverse.
  const upper = props.join ? centre : props.any;
  const lower = props.join ? props.any : centre;
  return (
    <div className={`manifold${props.join ? " join" : ""}${props.any ? " any" : ""}`} aria-hidden={props.children ? undefined : true}>
      <i className={`pipe el${on(left)}`} />
      <i className={`pipe er${on(right)}`} />
      <i className={`pipe sa${on(upper)}`} />
      <i className={`pipe sb${on(lower)}`} />
      {props.flowing && <i className="sheet" />}
      {props.children}
    </div>
  );
}

// Cards lean toward the pointer; the CSS applies it only for a mouse and without reduced motion.
function tilt(e: PointerEvent<HTMLElement>) {
  if (e.pointerType !== "mouse") return;
  const r = e.currentTarget.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width - 0.5;
  const y = (e.clientY - r.top) / r.height - 0.5;
  e.currentTarget.style.setProperty("--ry", `${(x * 12).toFixed(2)}deg`);
  e.currentTarget.style.setProperty("--rx", `${(-y * 12).toFixed(2)}deg`);
}

function untilt(e: PointerEvent<HTMLElement>) {
  e.currentTarget.style.removeProperty("--rx");
  e.currentTarget.style.removeProperty("--ry");
}
