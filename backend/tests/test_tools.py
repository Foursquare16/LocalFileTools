import io
import json
import sys

import pikepdf
import pytest
from conftest import image_bytes, logged_in, run_next_job, upload  # noqa: F401
from PIL import Image

from app import config, db, jobs, worker
from app.tools import ToolError, image, images_to_pdf


def run_tool(tool, tmp_path, files, options):
    """Run a tool in-process on [(name, bytes)]; returns (outputs as bytes, reported outputs, message)."""
    job_dir = tmp_path / "job"
    (job_dir / "in").mkdir(parents=True)
    for n, (_, data) in enumerate(files):
        (job_dir / "in" / str(n)).write_bytes(data)
    reported = []
    message = tool.run(
        job_dir,
        [{"name": name, "size": len(data)} for name, data in files],
        options,
        lambda stage=None, outputs=None: outputs is not None and reported.append(outputs),
    )
    assert len(reported) == 1  # exactly one result, complete
    return [(job_dir / "out" / str(n)).read_bytes() for n in range(len(reported[0]))], reported[0], message


def opened(data):
    return Image.open(io.BytesIO(data))


def noisy(size=(600, 400)):
    """A photo-like image that does not compress to nothing."""
    return Image.effect_noise(size, 80).convert("RGB")


# --- image tool ---


def test_convert_and_resize_keeps_proportions(tmp_path):
    files = [("a.png", image_bytes((400, 200))), ("b.webp", image_bytes((100, 300), "WEBP"))]
    out, reported, _ = run_tool(image, tmp_path, files, {"format": "jpeg", "max_width": 200, "max_height": 200})
    assert [r["name"] for r in reported] == ["a.jpg", "b.jpg"]
    assert [r["type"] for r in reported] == ["image/jpeg"] * 2
    assert [opened(d).format for d in out] == ["JPEG", "JPEG"]
    assert [opened(d).size for d in out] == [(200, 100), (67, 200)]
    assert [r["size"] for r in reported] == [len(d) for d in out]


def test_resize_never_enlarges(tmp_path):
    out, _, _ = run_tool(image, tmp_path, [("a.png", image_bytes((50, 40)))], {"format": "png", "max_width": 500})
    assert opened(out[0]).size == (50, 40)


def test_exif_orientation_applied_and_metadata_dropped(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° clockwise to display
    exif[0x010F] = "SecretCam"
    src = image_bytes((40, 20), "JPEG", exif=exif)
    out, _, _ = run_tool(image, tmp_path, [("p.jpg", src)], {"format": "jpeg"})
    result = opened(out[0])
    assert result.size == (20, 40)
    assert not result.getexif() and b"SecretCam" not in out[0]


def test_alpha_flattened_onto_white_for_jpeg_kept_for_png(tmp_path):
    src = image_bytes((10, 10), mode="RGBA", color=(255, 0, 0, 0))
    jpeg, _, _ = run_tool(image, tmp_path / "j", [("t.png", src)], {"format": "jpeg"})
    assert opened(jpeg[0]).getpixel((5, 5)) == (255, 255, 255)
    png, _, _ = run_tool(image, tmp_path / "p", [("t.png", src)], {"format": "png"})
    assert opened(png[0]).mode == "RGBA" and opened(png[0]).getpixel((5, 5))[3] == 0


def test_target_size_hit(tmp_path, monkeypatch):
    encodes = []
    real = image._encode
    monkeypatch.setattr(image, "_encode", lambda *a: encodes.append(a[2]) or real(*a))
    buf = io.BytesIO()
    noisy().save(buf, "PNG")
    target_kb = 40
    out, reported, message = run_tool(image, tmp_path, [("n.png", buf.getvalue())], {"format": "jpeg", "target_kb": target_kb})
    assert len(out[0]) <= target_kb * 1024 and len(out[0]) > target_kb * 1024 * 0.5  # under, but not needlessly small
    assert opened(out[0]).size == (600, 400)  # dimensions unchanged without resize
    assert message is None
    assert len(encodes) <= image.TARGET_ENCODES


def test_target_size_unreachable_returns_smallest_with_real_size(tmp_path):
    buf = io.BytesIO()
    noisy((1000, 1000)).save(buf, "PNG")
    out, reported, message = run_tool(image, tmp_path, [("n.png", buf.getvalue())], {"format": "webp", "target_kb": 1})
    assert len(out[0]) > 1024 and reported[0]["size"] == len(out[0])
    assert "Target size not reachable for n.png" in message


def test_png_renamed_jpg_handled_by_content(tmp_path):
    src = image_bytes((30, 20), mode="RGBA", color=(0, 0, 255, 128))
    out, reported, _ = run_tool(image, tmp_path, [("shot.jpg", src)], {"format": "webp"})
    assert reported[0]["name"] == "shot.webp" and opened(out[0]).mode == "RGBA"  # read as PNG: alpha survived


def test_disk_budget_exceeded_while_converting(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DISK_BUDGET_BYTES", 10)
    (config.JOBS_DIR / "big.bin").write_bytes(b"x" * 11)
    with pytest.raises(ToolError, match="temporary storage is full"):
        run_tool(image, tmp_path, [("a.png", image_bytes())], {"format": "png"})


@pytest.mark.parametrize("size", [(1100, 1000), (3000, 1000)])  # over the limit, and over Pillow's 2× hard limit
def test_oversized_pixel_count_rejected(tmp_path, monkeypatch, size):
    monkeypatch.setattr(config, "MAX_IMAGE_MP", 1)
    with pytest.raises(ToolError, match="big.png is too large: at most 1 megapixels"):
        run_tool(image, tmp_path, [("big.png", image_bytes(size))], {"format": "jpeg"})


@pytest.mark.parametrize("data", [b"%PDF-1.7 not an image", image_bytes(fmt="GIF"), image_bytes(fmt="BMP")])
def test_non_image_or_unsupported_format_rejected(tmp_path, data):
    with pytest.raises(ToolError, match="x.jpg is not a supported image"):
        run_tool(image, tmp_path, [("x.jpg", data)], {"format": "png"})


def test_damaged_image_rejected(tmp_path):
    with pytest.raises(ToolError, match="could not be read"):
        run_tool(image, tmp_path, [("x.jpg", image_bytes(fmt="JPEG")[:200])], {"format": "png"})


@pytest.mark.parametrize("fmt", ["HEIF", "AVIF"])
def test_heic_and_avif_inputs_accepted(tmp_path, fmt):
    out, reported, _ = run_tool(image, tmp_path, [(f"phone.{fmt.lower()}", image_bytes((64, 48), fmt))], {"format": "jpeg"})
    assert opened(out[0]).size == (64, 48) and reported[0]["name"] == f"phone.jpg"


@pytest.mark.parametrize(
    "options, error",
    [
        ({}, "output format"),
        ({"format": "gif"}, "output format"),
        ({"format": "jpeg", "quality": 0}, "Quality must be"),
        ({"format": "jpeg", "quality": "high"}, "Quality must be"),
        ({"format": "jpeg", "max_width": -5}, "Max width must be"),
        ({"format": "png", "target_kb": 100}, "JPEG and WebP only"),
    ],
)
def test_bad_image_options_rejected(tmp_path, options, error):
    with pytest.raises(ToolError, match=error):
        run_tool(image, tmp_path, [("a.png", image_bytes())], options)


# --- images → PDF ---


def test_pdf_page_count_order_and_rotation(tmp_path):
    files = [
        ("red.png", image_bytes((200, 100), color="red")),
        ("green.jpg", image_bytes((100, 200), "JPEG", color="green")),
        ("blue.webp", image_bytes((300, 100), "WEBP", mode="RGBA", color=(0, 0, 255, 0))),  # alpha → white
    ]
    out, reported, _ = run_tool(images_to_pdf, tmp_path, files, {"page": "image", "rotations": [90, 0, 0]})
    assert reported == [{"name": "images.pdf", "size": len(out[0]), "type": "application/pdf"}]
    pages = pikepdf.open(io.BytesIO(out[0])).pages
    assert len(pages) == 3
    images = [next(iter(p.get_images().values())) for p in pages]
    # Order kept; the PNG stayed lossless (Flate), the rest became JPEG (DCT); rotation applied to the first.
    assert [str(i.Filter) for i in images] == ["/FlateDecode", "/DCTDecode", "/DCTDecode"]
    assert [(int(i.Width), int(i.Height)) for i in images] == [(100, 200), (100, 200), (300, 100)]
    colours = [pikepdf.PdfImage(i).as_pil_image().getpixel((5, 5)) for i in images]
    assert colours[0] == (255, 0, 0) and colours[1][1] > 100 and min(colours[2]) > 240


def test_pdf_a4_with_margin(tmp_path):
    out, _, _ = run_tool(images_to_pdf, tmp_path, [("a.png", image_bytes((100, 100)))], {"page": "a4", "margin_mm": 10})
    pdf = pikepdf.open(io.BytesIO(out[0]))
    width, height = (float(v) for v in pdf.pages[0].mediabox[2:])
    assert (round(width), round(height)) == (595, 842)


@pytest.mark.parametrize(
    "options, error",
    [
        ({"page": "letter"}, "Page size"),
        ({"margin_mm": 99}, "Margin"),
        ({"rotations": [45]}, "rotation"),
        ({"rotations": [0, 0]}, "rotation"),
    ],
)
def test_bad_pdf_options_rejected(tmp_path, options, error):
    with pytest.raises(ToolError, match=error):
        run_tool(images_to_pdf, tmp_path, [("a.png", image_bytes())], options)


def test_bad_image_in_pdf_batch_reports_nothing(tmp_path):
    with pytest.raises(ToolError, match="b.png is not a supported image"):
        run_tool(images_to_pdf, tmp_path, [("a.png", image_bytes()), ("b.png", b"junk")], {})


# --- timeout mid-batch, through the real worker and the real image tool ---

# The real child, except that its report() hangs once the tool starts on the second image: the first output
# is on disk by then, but must never be returned as a "completed" result.
HANG_ON_SECOND = """
import sys, time
from app import worker
real = worker.jobs.report
def report(c, job_id, stage=None, outputs=None):
    real(c, job_id, stage, outputs)
    if stage and stage.startswith("Converting image 2"):
        time.sleep(60)
worker.jobs.report = report
worker.child(sys.argv[1])
"""


def test_timeout_mid_batch_is_not_a_partial_result(logged_in, tmp_path, monkeypatch):  # noqa: F811
    script = tmp_path / "hang_child.py"
    script.write_text(HANG_ON_SECOND)
    monkeypatch.setattr(worker, "CHILD", [sys.executable, str(script)])
    monkeypatch.setattr(config, "JOB_TIMEOUT_SECONDS", 2)
    files = [(f"{n}.png", image_bytes()) for n in range(3)]
    job_id = upload(logged_in, files, options=json.dumps({"format": "jpeg"})).json()["id"]
    run_next_job()
    assert (config.JOBS_DIR / job_id / "out" / "0").exists()  # the first image was done...
    with db.jobs() as c:
        row = jobs.get(c, job_id)
    assert row["status"] == "failed" and "Time limit" in row["message"]  # ...yet nothing was returned
    assert logged_in.get(f"/api/jobs/{job_id}/files/0").status_code == 404
