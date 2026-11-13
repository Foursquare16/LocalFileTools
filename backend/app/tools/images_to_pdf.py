"""Images → PDF: one page per image, in upload order (the order arranged in the UI).

Options: {"page": "a4"|"image", "margin_mm"?: 0-50, "rotations"?: [0|90|180|270 per input, clockwise]}.
"""

import os
import shutil

import img2pdf

from . import ToolError
from .image import load

A4 = (img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297))


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

    # Intermediates: PNG stays PNG (lossless for screenshots and text), the rest JPEG q90. Alpha always
    # flattened onto white: img2pdf rejects alpha channels.
    work = job_dir / "pages"
    work.mkdir(exist_ok=True)
    pages = []
    for n, item in enumerate(inputs):
        report(stage=f"Preparing image {n + 1} of {len(inputs)}")
        im, source_format = load(job_dir / "in" / str(n), item["name"], flatten=True, rotate=rotations[n])
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
    report(outputs=[{"name": "images.pdf", "size": os.path.getsize(out_dir / "0"), "type": "application/pdf"}])
    return None
