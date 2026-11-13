"""No-op tool: copies each input to an output. Proves the pipeline end to end."""

import os
import shutil


def run(job_dir, inputs, options, report):
    out_dir = job_dir / "out"
    out_dir.mkdir(exist_ok=True)
    outputs = []
    for n, item in enumerate(inputs):
        report(stage=f"Copying file {n + 1} of {len(inputs)}")
        tmp = out_dir / f"{n}.tmp"
        shutil.copyfile(job_dir / "in" / str(n), tmp)
        os.replace(tmp, out_dir / str(n))
        outputs.append({"name": item["name"], "size": item["size"], "type": "application/octet-stream"})
    report(outputs=outputs)  # once, at the end: a partial batch must not look like a result
    return None
