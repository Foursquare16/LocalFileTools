import io
import shutil
import subprocess

import img2pdf
import pikepdf
import pytest
from conftest import client, run_next_job, upload  # noqa: F401
from PIL import Image

from app import config, db, jobs
from app.tools import ToolError, pdf_compress

needs_gs = pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed")


def compress(tmp_path, data, options, name="doc.pdf"):
    """Run the tool in-process; returns (output bytes, every reported outputs list, message)."""
    job_dir = tmp_path / "job"
    (job_dir / "in").mkdir(parents=True)
    (job_dir / "in" / "0").write_bytes(data)
    reported = []
    message = pdf_compress.run(
        job_dir, [{"name": name, "size": len(data)}], options,
        lambda stage=None, outputs=None: outputs is not None and reported.append(outputs),
    )
    assert sorted(p.name for p in (job_dir / "out").iterdir()) == ["0"]  # no attempt files left behind
    out = (job_dir / "out" / "0").read_bytes()
    assert reported[-1] == [{"name": "doc.pdf", "size": len(out), "type": "application/pdf"}]
    return out, reported, message


def scanned_pdf(pages=2):
    """Pages that are barely compressed photos, like a 600 dpi scan (small pages keep the test fast)."""
    images = []
    for _ in range(pages):
        buf = io.BytesIO()
        Image.effect_noise((1240, 1754), 40).convert("RGB").save(buf, "JPEG", quality=95)
        images.append(buf.getvalue())
    return img2pdf.convert(images, layout_fun=img2pdf.get_layout_fun((img2pdf.mm_to_pt(52.5), img2pdf.mm_to_pt(74.25))))


def text_pdf(pages=1, compress_streams=True, words="Hello compressor"):
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type="/Font", Subtype="/Type1", BaseFont="/Helvetica"))
    for _ in range(pages):
        lines = b"".join(b"BT /F1 12 Tf 72 %d Td (%s) Tj ET\n" % (700 - 14 * i, words.encode()) for i in range(40))
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page.Contents = pdf.make_stream(lines + b"0 0 1 RG 72 72 m 500 500 l S\n")
    buf = io.BytesIO()
    pdf.save(buf, compress_streams=compress_streams)
    return buf.getvalue()


def with_field(field_type):
    pdf = pikepdf.open(io.BytesIO(text_pdf()))
    field = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name(field_type), T="f"))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([field]))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def extract_text(path):
    return subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=txtwrite", "-sOutputFile=-", str(path)],
                          capture_output=True, text=True, check=True).stdout


@needs_gs
def test_scanned_pdf_smaller_at_each_strength(tmp_path):
    src = scanned_pdf()
    sizes = []
    for strength in ("low", "medium", "high"):
        out, _, message = compress(tmp_path / strength, src, {"strength": strength})
        assert message is None and len(pikepdf.open(io.BytesIO(out)).pages) == 2
        sizes.append(len(out))
    assert len(src) > sizes[0] > sizes[1] > sizes[2]


@needs_gs
def test_text_pdf_keeps_text_and_is_not_rasterised(tmp_path):
    src = text_pdf(pages=3, compress_streams=False, words="Quarterly report 2026")
    out, _, _ = compress(tmp_path, src, {"strength": "high"})
    (tmp_path / "out.pdf").write_bytes(out)
    assert "Quarterly report 2026" in extract_text(tmp_path / "out.pdf")
    for page in pikepdf.open(tmp_path / "out.pdf").pages:
        assert not page.get_images() and "/Font" in page.Resources


@needs_gs
def test_already_small_pdf_reports_no_improvement_and_returns_original(tmp_path):
    src = text_pdf()
    src = compress(tmp_path / "first", src, {"strength": "low"})[0]  # the most compact this tool can make it
    out, reported, message = compress(tmp_path / "again", src, {"strength": "high"})
    assert out == src and len(reported) == 1 and "No improvement" in message


@needs_gs
def test_damaged_pdf_is_returned_untouched(tmp_path):
    """Ghostscript would 'repair or ignore' the broken page and exit 0; the tool must not pass that off as a result."""
    pdf = pikepdf.open(io.BytesIO(text_pdf(pages=3)))
    pdf.pages[1].Contents = pdf.make_stream(b"BT /F1 24 Tf (Page 2) Tj ET q 1 0 0 1 (((( garbage \x00\xff Do Q BT")
    pdf.save(tmp_path / "raw.pdf")
    pdf_compress._structural(tmp_path / "raw.pdf", tmp_path / "src.pdf")  # already compact: only gs could shrink it
    src = (tmp_path / "src.pdf").read_bytes()
    out, _, message = compress(tmp_path, src, {"strength": "high"})
    assert out == src and "this PDF has errors" in message


@needs_gs
def test_target_reached_stops_at_first_rung_that_fits(tmp_path, monkeypatch):
    calls = []
    real = pdf_compress._ghostscript
    monkeypatch.setattr(pdf_compress, "_ghostscript", lambda s, d, p: calls.append(p) or real(s, d, p))
    src = scanned_pdf()
    low = compress(tmp_path / "low", src, {"strength": "low"})[0]
    calls.clear()
    out, reported, message = compress(tmp_path / "t", src, {"target_kb": len(low) // 1024 + 1})
    assert calls == ["/printer"] and message is None and len(out) <= (len(low) // 1024 + 1) * 1024


@needs_gs
def test_target_unreachable_returns_smallest_with_real_size(tmp_path):
    out, reported, message = compress(tmp_path, scanned_pdf(), {"target_kb": 1})
    assert len(out) > 1024
    assert [r[0]["size"] for r in reported] == sorted((r[0]["size"] for r in reported), reverse=True)  # each one better
    assert f"Target not reached: this is the smallest result, {len(out) / 1024:,.0f} KB." == message


def test_disk_budget_exceeded_between_attempts_keeps_best_so_far(tmp_path, monkeypatch):
    usage = iter([0, 10**12])  # fine before attempt 1, full before attempt 2
    monkeypatch.setattr(jobs, "disk_usage", lambda: next(usage))
    monkeypatch.setattr(pdf_compress, "_ghostscript", lambda *a: pytest.fail("ran after the budget was exceeded"))
    src = text_pdf(pages=5, compress_streams=False)
    out, reported, message = compress(tmp_path, src, {"strength": "high"})
    assert len(out) < len(src) and len(reported) == 1
    assert message == "The server's temporary storage is full, so compression stopped early."


@pytest.mark.parametrize(
    "data, error",
    [
        (b"not a pdf at all", "doc.pdf is not a PDF"),
        (Image.new("RGB", (10, 10)).tobytes(), "doc.pdf is not a PDF"),
        (with_field("/Sig"), "digitally signed"),
        (with_field("/Tx"), "fillable form fields"),
    ],
)
def test_rejected_pdfs(tmp_path, data, error):
    with pytest.raises(ToolError, match=error):
        compress(tmp_path, data, {"strength": "low"})


@pytest.mark.parametrize("encryption", [pikepdf.Encryption(owner="o", user="u"), pikepdf.Encryption(owner="o", user="")])
def test_encrypted_pdf_rejected(tmp_path, encryption):
    buf = io.BytesIO()
    pikepdf.open(io.BytesIO(text_pdf())).save(buf, encryption=encryption)
    with pytest.raises(ToolError, match="encrypted"):
        compress(tmp_path, buf.getvalue(), {"strength": "low"})


def test_over_page_limit_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_PDF_PAGES", 2)
    with pytest.raises(ToolError, match="has 3 pages: at most 2"):
        compress(tmp_path, text_pdf(pages=3), {"strength": "low"})


@pytest.mark.parametrize("options", [{}, {"strength": "max"}, {"strength": "low", "target_kb": 5}, {"target_kb": 0}])
def test_bad_options_rejected(tmp_path, options):
    with pytest.raises(ToolError):
        compress(tmp_path, text_pdf(), options)


def test_timeout_mid_ladder_completes_with_best_so_far(client, tmp_path, monkeypatch):
    """Ghostscript hangs after the structural save: the timeout returns that save as the completed result."""
    fake = tmp_path / "bin" / "gs"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexec sleep 60\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    monkeypatch.setattr(config, "JOB_TIMEOUT_SECONDS", 3)
    src = text_pdf(pages=5, compress_streams=False)
    job_id = upload(client, [("doc.pdf", src)], tool="pdf_compress", options='{"strength": "high"}').json()["id"]
    run_next_job()
    with db.jobs() as c:
        row = jobs.get(c, job_id)
    assert row["status"] == "completed" and "best result so far" in row["message"]
    out = client.get(f"/api/jobs/{job_id}/files/0").content
    assert len(out) < len(src) and len(pikepdf.open(io.BytesIO(out)).pages) == 5
    assert subprocess.run(["pgrep", "-f", "sleep 60"], capture_output=True).returncode == 1  # gs stand-in killed
