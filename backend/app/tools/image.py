"""Image tool: convert between JPEG/PNG/WebP, optional resize, quality or best-effort target size.

Options: {"format": "jpeg"|"png"|"webp", "max_width"?: int, "max_height"?: int, "quality"?: 1-100,
"target_kb"?: int (JPEG and WebP only)}. One output per input. `load` is shared with images_to_pdf.
"""

import io
import os
import warnings
from pathlib import PurePath

import pillow_heif
from PIL import Image, ImageOps, UnidentifiedImageError

from .. import config, jobs
from . import ToolError

pillow_heif.register_heif_opener()  # HEIC; Pillow ≥ 11.3 reads AVIF itself

# Only these decoders ever see an upload (the JPEG one also opens multi-picture "MPO" files from phones).
FORMATS = ["JPEG", "PNG", "WEBP", "HEIF", "AVIF"]
OUTPUTS = {"jpeg": ("JPEG", ".jpg", "image/jpeg"), "png": ("PNG", ".png", "image/png"), "webp": ("WEBP", ".webp", "image/webp")}
MAX_SIDE = 20_000
TARGET_ENCODES = 7
BOMB = (Image.DecompressionBombError, Image.DecompressionBombWarning)


def load(path, name: str, flatten: bool, rotate: int = 0) -> tuple[Image.Image, str]:
    """Open an untrusted upload by content, check its size before decoding, apply EXIF orientation and
    `rotate` (degrees clockwise). Returns (RGB, or RGBA when it has alpha and not `flatten`, without metadata;
    the detected format)."""
    limit = config.MAX_IMAGE_MP * 1_000_000
    too_large = f"{name} is too large: at most {config.MAX_IMAGE_MP} megapixels per image."
    Image.MAX_IMAGE_PIXELS = limit  # Pillow's own bomb check, its warning turned into an error below
    try:
        bomb_is_error = warnings.catch_warnings(action="error", category=Image.DecompressionBombWarning)
        with bomb_is_error, Image.open(path, formats=FORMATS) as im:
            if im.width * im.height > limit:
                raise ToolError(too_large)
            source_format = im.format
            icc = im.info.get("icc_profile") if im.mode in ("RGB", "RGBA") else None
            im = ImageOps.exif_transpose(im)  # decodes; returns a copy without the orientation tag
            if im.has_transparency_data:
                im = im.convert("RGBA")
                if flatten:
                    im = Image.alpha_composite(Image.new("RGBA", im.size, "white"), im).convert("RGB")
            else:
                im = im.convert("RGB")
    except UnidentifiedImageError:
        raise ToolError(f"{name} is not a supported image (JPEG, PNG, WebP, HEIC or AVIF).")
    except BOMB:
        raise ToolError(too_large)
    except (OSError, ValueError, EOFError, SyntaxError):
        raise ToolError(f"{name} could not be read: the file may be damaged.")
    im.info = {"icc_profile": icc} if icc else {}  # colour profile kept, every other piece of metadata dropped
    return (im.rotate(-rotate, expand=True) if rotate else im), source_format


def _int(options, key, low, high, default=None):
    value = options.get(key, default)
    if value is None:
        return None
    if type(value) is not int or not low <= value <= high:
        raise ToolError(f"{key.replace('_', ' ').capitalize()} must be a whole number from {low} to {high}.")
    return value


def _encode(im, fmt, quality) -> bytes:
    buf = io.BytesIO()
    kwargs = {"icc_profile": im.info["icc_profile"]} if "icc_profile" in im.info else {}
    if fmt == "JPEG":
        kwargs.update(quality=quality, optimize=True)
    elif fmt == "WEBP":
        kwargs.update(quality=quality)
    im.save(buf, fmt, **kwargs)
    return buf.getvalue()


def _to_target(im, fmt, target) -> tuple[bytes, bool]:
    """Binary search on quality for the largest encoding ≤ target. Unreachable: the smallest one, False."""
    low, high, best, smallest = 1, 100, None, None
    for _ in range(TARGET_ENCODES):
        if low > high:
            break
        quality = (low + high) // 2
        data = _encode(im, fmt, quality)
        if smallest is None or len(data) < len(smallest):
            smallest = data
        if len(data) <= target:
            best, low = data, quality + 1
        else:
            high = quality - 1
    return (best, True) if best is not None else (smallest, False)


def run(job_dir, inputs, options, report):
    fmt_key = options.get("format")
    if fmt_key not in OUTPUTS:
        raise ToolError("Choose an output format: JPEG, PNG or WebP.")
    fmt, ext, ctype = OUTPUTS[fmt_key]
    max_w = _int(options, "max_width", 1, MAX_SIDE)
    max_h = _int(options, "max_height", 1, MAX_SIDE)
    quality = _int(options, "quality", 1, 100, 85)
    target_kb = _int(options, "target_kb", 1, 1_000_000)
    if target_kb and fmt == "PNG":
        raise ToolError("Target size works for JPEG and WebP only: PNG has no quality setting.")

    out_dir = job_dir / "out"
    out_dir.mkdir(exist_ok=True)
    outputs, missed = [], []
    for n, item in enumerate(inputs):
        report(stage=f"Converting image {n + 1} of {len(inputs)}")
        im, _ = load(job_dir / "in" / str(n), item["name"], flatten=fmt == "JPEG")
        if max_w or max_h:
            box = (max_w or im.width, max_h or im.height)
            if im.width > box[0] or im.height > box[1]:  # only ever shrinks, keeping proportions
                im = ImageOps.contain(im, box, Image.Resampling.LANCZOS)
        if target_kb:
            data, hit = _to_target(im, fmt, target_kb * 1024)
            if not hit:
                missed.append(item["name"])
        else:
            data = _encode(im, fmt, quality)
        tmp = out_dir / f"{n}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, out_dir / str(n))
        if jobs.disk_usage() > config.DISK_BUDGET_BYTES:  # e.g. many small HEIC files turned into large PNGs
            raise ToolError("The server's temporary storage is full. Try fewer images or a smaller output format.")
        outputs.append({"name": (PurePath(item["name"]).stem or "image") + ext, "size": len(data), "type": ctype})
    report(outputs=outputs)  # once, at the end: a partial batch must not look like a result
    if missed:
        return f"Target size not reachable for {', '.join(missed)}: those are the smallest results (sizes shown)."
    return None
