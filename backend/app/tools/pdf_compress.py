"""PDF compressor: one PDF in, the smallest result that beats it out (best effort, never an exact size).

Options: {"strength": "low"|"medium"|"high"} or {"target_kb": int}. Attempt 1 is a pikepdf structural save;
then Ghostscript pdfwrite presets, one rung per strength, or walked in order until one is ≤ target.
Every smaller result goes to out/0 at once and is reported, so a timeout completes with the best so far.
"""

import os
import shutil
import subprocess
from pathlib import PurePath

import pikepdf

from .. import config, jobs
from . import ToolError
from .image import _int

LADDER = {"low": "/printer", "medium": "/ebook", "high": "/screen"}


def _inspect(path, name):
    """Open the untrusted upload; reject what the plan rules out (§5)."""
    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        raise ToolError(f"{name} is password-protected: encrypted PDFs are not supported.")
    except (pikepdf.PdfError, OSError, ValueError):
        raise ToolError(f"{name} is not a PDF or could not be read.")
    with pdf:
        if pdf.is_encrypted:
            raise ToolError(f"{name} is encrypted: encrypted PDFs are not supported.")
        fields = pdf.Root.get("/AcroForm", {}).get("/Fields", [])
        if any(f.get("/FT") == "/Sig" for f in fields) or "/Perms" in pdf.Root:
            raise ToolError(f"{name} is digitally signed: compressing it would break the signature.")
        if len(fields):
            raise ToolError(f"{name} has fillable form fields: these PDFs are not supported.")
        if len(pdf.pages) > config.MAX_PDF_PAGES:
            raise ToolError(f"{name} has {len(pdf.pages)} pages: at most {config.MAX_PDF_PAGES} are supported.")


def _structural(src, dst):
    with pikepdf.open(src) as pdf:
        pdf.save(dst, object_stream_mode=pikepdf.ObjectStreamMode.generate, recompress_flate=True)


def _ghostscript(src, dst, preset):
    # Only paths built from the job dir and fixed strings reach gs; no shell. Killed with the job's process group.
    # PDFSTOPONERROR: without it gs "repairs or ignores" damaged content and still exits 0, silently losing it.
    cmd = ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET", "-dPDFSTOPONERROR", "-sDEVICE=pdfwrite",
           f"-dPDFSETTINGS={preset}", f"-sOutputFile={dst}", str(src)]
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True).returncode == 0


def run(job_dir, inputs, options, report):
    if len(inputs) != 1:
        raise ToolError("Upload one PDF at a time.")
    strength, target_kb = options.get("strength"), _int(options, "target_kb", 1, 1_000_000)
    if (strength is None) == (target_kb is None) or (strength is not None and strength not in LADDER):
        raise ToolError("Choose a strength (low, medium or high) or a target size in KB.")
    name = inputs[0]["name"]
    src = job_dir / "in" / "0"
    report(stage="Checking the PDF")
    _inspect(src, name)

    out_dir = job_dir / "out"
    out_dir.mkdir(exist_ok=True)
    best = original = os.path.getsize(src)
    output = {"name": (PurePath(name).stem or "document") + ".pdf", "type": "application/pdf"}
    target = target_kb * 1024 if target_kb else None
    attempts = [("Optimising structure", None)]
    attempts += [(f"Compressing ({s})", p) for s, p in LADDER.items() if target or s == strength]
    stopped, gs_failed = None, False
    for stage, preset in attempts:
        if jobs.disk_usage() > config.DISK_BUDGET_BYTES:
            stopped = "The server's temporary storage is full, so compression stopped early."
            break
        report(stage=stage)
        tmp = out_dir / "attempt.tmp"
        try:
            if preset:
                ok = _ghostscript(src, tmp, preset)
                gs_failed = gs_failed or not ok
            else:
                _structural(src, tmp)
                ok = True
            if ok and os.path.getsize(tmp) < best:
                best = os.path.getsize(tmp)
                os.replace(tmp, out_dir / "0")
                report(outputs=[{**output, "size": best}])
        except (pikepdf.PdfError, OSError):
            pass  # this attempt produced nothing usable; the next one may still
        finally:
            tmp.unlink(missing_ok=True)
        if target and best <= target:
            break

    if best == original:
        shutil.copyfile(src, out_dir / "0.tmp")
        os.replace(out_dir / "0.tmp", out_dir / "0")
        report(outputs=[{**output, "size": original}])
        why = (
            "Not compressed: this PDF has errors, and repairing them could lose content, so this is the original file."
            if gs_failed
            else "No improvement: the PDF is already compact, so this is the original file."
        )
        return " ".join(filter(None, [stopped, why]))
    if target and best > target:
        return " ".join(filter(None, [stopped, f"Target not reached: this is the smallest result, {best / 1024:,.0f} KB."]))
    return stopped
