"""Images → PDF: one page per image, in upload order (the order arranged in the UI).

Options: {"page": "a4"|"image", "margin_mm"?: 0-50, "rotations"?: [0|90|180|270 per input, clockwise],
"crops"?: [null or [left, top, right, bottom] per input, fractions 0-1 of the image after rotation],
"name"?: output file name without ".pdf" (sanitised here; "images" when missing or empty)}.
Each image is turned first, then cropped, so a crop is what the user saw on the turned preview.
"""

import os
import re
import shutil

import img2pdf

from . import ToolError
from .image import load

A4 = (img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297))
MIN_CROP_PX = 8  # a smaller crop is almost certainly a slip, and 0 px would be no page at all
MAX_NAME = 100


def _crops(value, count: int) -> list:
    crops = [None] * count if value is None else value
    bad = ToolError("Each crop must be empty or [left, top, right, bottom] as fractions from 0 to 1, one per image.")
    if not isinstance(crops, list) or len(crops) != count:
        raise bad
    for crop in crops:
        if crop is None:
            continue
        if not isinstance(crop, list) or len(crop) != 4 or any(type(v) not in (int, float) for v in crop):
            raise bad
        left, top, right, bottom = crop
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):  # also false for NaN
            raise bad
    return crops


def _output_name(value) -> str:
    """A file name from the browser: no path parts, control characters, header-unsafe quotes or leading dots."""
    if value is None:
        return "images.pdf"
    if not isinstance(value, str):
        raise ToolError("The file name must be text.")
    name = "".join(ch for ch in value if ch.isprintable() and ch not in '/\\:*?"<>|')
    name = re.sub(r"\.pdf$", "", name.strip(), flags=re.IGNORECASE).lstrip(". ")[:MAX_NAME].rstrip(". ")
    return f"{name or 'images'}.pdf"


def run(job_dir, inputs, options, report):
    page = options.get("page", "a4")
    if page not in ("a4", "image"):
        raise ToolError("Page size must be A4 or the image size.")
    margin = options.get("margin_mm", 0)
    if type(margin) not in (int, float) or not 0 <= margin <= 50:
        raise ToolError("Margin must be from 0 to 50 mm.")
    rotations = options.get("rotations", [0] * len(inputs))
    if not isinstance(rotations, list) or len(rotations) != len(inputs) or any(r not in (0, 90, 180, 270) for r in rotations):
        raise ToolError("Each rotation must be 0, 90, 180 or 270 degrees, one per image.")
    crops = _crops(options.get("crops"), len(inputs))
    name = _output_name(options.get("name"))

    # Intermediates: PNG stays PNG (lossless for screenshots and text), the rest JPEG q90. Alpha always
    # flattened onto white: img2pdf rejects alpha channels.
    work = job_dir / "pages"
    work.mkdir(exist_ok=True)
    pages = []
    for n, item in enumerate(inputs):
        report(stage=f"Preparing image {n + 1} of {len(inputs)}")
        im, source_format = load(job_dir / "in" / str(n), item["name"], flatten=True, rotate=rotations[n])
        if crops[n]:
            left, top, right, bottom = crops[n]
            box = (round(left * im.width), round(top * im.height), round(right * im.width), round(bottom * im.height))
            if min(box[2] - box[0], box[3] - box[1]) < MIN_CROP_PX:
                raise ToolError(f"The crop of {item['name']} is too small: at least {MIN_CROP_PX} × {MIN_CROP_PX} pixels.")
            im = im.crop(box)  # keeps im.info, so the colour profile too
        icc = im.info.get("icc_profile")
        if source_format == "PNG":
            path = work / f"{n}.png"
            im.save(path, "PNG", icc_profile=icc)
        else:
            path = work / f"{n}.jpg"
            im.save(path, "JPEG", quality=90, icc_profile=icc)
        pages.append(str(path))

    report(stage="Building the PDF")
    border = (img2pdf.mm_to_pt(margin),) * 2
    if page == "a4":
        layout = img2pdf.get_layout_fun(A4, border=border, fit=img2pdf.FitMode.into, auto_orient=True)
    else:
        layout = img2pdf.get_layout_fun(None, border=border)
    out_dir = job_dir / "out"
    out_dir.mkdir(exist_ok=True)
    tmp = out_dir / "0.tmp"
    with open(tmp, "wb") as f:
        img2pdf.convert(pages, layout_fun=layout, outputstream=f)
    os.replace(tmp, out_dir / "0")
    shutil.rmtree(work, ignore_errors=True)  # intermediates would count against the disk budget for an hour
    report(outputs=[{"name": name, "size": os.path.getsize(out_dir / "0"), "type": "application/pdf"}])
    return None
