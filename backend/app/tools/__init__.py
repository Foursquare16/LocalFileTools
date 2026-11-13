"""Tools run in the worker's child process, one module per tool name in NAMES.

Each module has `run(job_dir, inputs, options, report) -> message | None`:
- inputs: [{name, size}]; input n is job_dir/"in"/str(n)
- writes output n to job_dir/"out"/str(n) (write a temp file, then os.replace)
- calls report(stage=...) as it goes
- calls report(outputs=[{name, size, type}]) only with a complete, usable result (every input handled),
  and again whenever it has a better one: a timeout returns the last reported outputs as "completed"
- raises ToolError with a message for the user
"""

NAMES = {"echo"}


class ToolError(Exception):
    """A failure whose message is shown to the user."""
