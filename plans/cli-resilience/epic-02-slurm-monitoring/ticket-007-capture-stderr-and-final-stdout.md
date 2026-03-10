# ticket-007 Capture stderr.modelops and Final stdout After Job Completion

## Context

### Background

SLURM jobs in this application write stdout to `stdout.modelops` and stderr to `stderr.modelops` (configured in `submit_job()` via `--output="stdout.modelops"` and `--error="stderr.modelops"`). After tickets 005 and 006, the monitoring loop captures `stdout.modelops` during execution and reads it after completion, plus queries `sacct` for job state. However, `stderr.modelops` is never read or logged. When a model crashes, the error details go to stderr and are lost.

This ticket ensures both `stdout.modelops` and `stderr.modelops` are fully captured after job completion, and creates a unified post-completion reporting function.

### Relation to Epic

This is the final ticket in Epic 02. It completes the SLURM monitoring overhaul by capturing all available diagnostic output.

### Current State

After tickets 005-006:

- `follow_submitted_job()` reads `stdout.modelops` after job completion (cat)
- `follow_submitted_job()` logs `sacct` completion info
- `stderr.modelops` is never read anywhere in the codebase
- `submit_job()` configures SLURM to write stderr to `stderr.modelops` (scheduler.py line 38: `'--error="stderr.modelops"'`)

Grep for `stderr.modelops` in the codebase shows it is:

- Configured in `submit_job()` SLURM flags
- Listed in `_list_output_files()` as an ignored file during compression (newave.py, decomp.py, dessem.py, gevazp.py)
- Never read or processed

## Specification

### Requirements

1. **Create a `read_job_output_files()` function** that reads both `stdout.modelops` and `stderr.modelops` after job completion and returns their content.

2. **Modify `follow_submitted_job()`** to use `read_job_output_files()` instead of the inline `cat stdout.modelops` from ticket-005. Log both stdout and stderr content.

3. **Log stderr with WARNING level** if `stderr.modelops` exists and is non-empty. Stderr content often contains critical error information (segfaults, MPI errors, library loading failures).

### New Function

```python
@dataclass
class JobOutputFiles:
    stdout_content: str | None  # Content of stdout.modelops, or None if file missing
    stderr_content: str | None  # Content of stderr.modelops, or None if file missing
    stdout_exists: bool
    stderr_exists: bool

def read_job_output_files(
    stdout_file: str = "stdout.modelops",
    stderr_file: str = "stderr.modelops",
) -> JobOutputFiles:
    """Read SLURM job output files after completion."""
```

### Integration with follow_submitted_job

Replace the inline `cat stdout.modelops` block from ticket-005 with:

```python
# Post-completion: read all output files
output_files = read_job_output_files()
if output_files.stdout_content:
    _log(f"=== Final stdout.modelops ({len(output_files.stdout_content)} chars) ===")
    # Log last 200 lines to avoid flooding
    for line in output_files.stdout_content.splitlines()[-200:]:
        _log(line)
if output_files.stderr_content:
    _log(f"WARNING: stderr.modelops contains output ({len(output_files.stderr_content)} chars):")
    for line in output_files.stderr_content.splitlines()[-50:]:
        _log(f"  STDERR: {line}")
if not output_files.stdout_exists:
    _log(f"Warning: stdout.modelops not found after job completion")
```

### Outputs/Behavior

- `read_job_output_files()` reads files using Python's built-in `open()` (not `run_in_terminal`), since the files are local and may be large
- Returns `JobOutputFiles` dataclass with content strings and existence flags
- Content is truncated to last 10000 lines to avoid memory issues with very large output files

### Error Handling

- If files do not exist: set `content=None`, `exists=False`
- If files cannot be read (permission error, encoding error): set `content=None`, log warning
- Function never raises exceptions

## Acceptance Criteria

- [ ] Given `app/utils/scheduler.py` after tickets 005-006, when ticket-007 is implemented, then a `JobOutputFiles` dataclass and `read_job_output_files()` function exist in `app/utils/scheduler.py`
- [ ] Given `stdout.modelops` contains "Model converged successfully" and `stderr.modelops` contains "Warning: deprecated config", when `read_job_output_files()` is called, then it returns `JobOutputFiles(stdout_content="Model converged successfully", stderr_content="Warning: deprecated config", stdout_exists=True, stderr_exists=True)`
- [ ] Given `stderr.modelops` does not exist after job completion, when `read_job_output_files()` is called, then it returns `JobOutputFiles` with `stderr_content=None` and `stderr_exists=False`
- [ ] Given a job completes with non-empty `stderr.modelops`, when `follow_submitted_job` runs post-completion, then the log output contains lines prefixed with `"STDERR:"` showing the stderr content
- [ ] Given `stdout.modelops` contains 50000 lines, when `read_job_output_files()` reads it, then `stdout_content` contains only the last 10000 lines (to avoid memory exhaustion)

## Implementation Guide

### Suggested Approach

1. Add `JobOutputFiles` dataclass to `app/utils/scheduler.py` (next to `JobCompletionInfo` from ticket-006)
2. Implement `read_job_output_files()`:
   - Use `pathlib.Path` to check file existence
   - Read files with `open(file, "r", encoding="utf-8", errors="replace")` to handle encoding issues
   - Truncate to last 10000 lines using `deque(f, maxlen=10000)` from `collections`
   - Wrap in try/except to never raise
3. Modify `follow_submitted_job()`: replace the inline `cat` + `os.path.exists` block with `read_job_output_files()` call and structured logging
4. Ensure the ordering in post-completion is: read output files -> log stdout -> log stderr -> sacct query -> log completion info

### Key Files to Modify

- `app/utils/scheduler.py` (add `JobOutputFiles` dataclass, add `read_job_output_files()`, modify post-completion block in `follow_submitted_job()`)

### Patterns to Follow

- Use `@dataclass` for structured return types (consistent with `JobCompletionInfo` from ticket-006)
- Use `pathlib.Path.exists()` for file checks (used in `app/utils/fs.py` line 108)
- Read files with Python built-in `open()` rather than `run_in_terminal(["cat", ...])` — avoids unnecessary subprocess overhead for local file reads

### Pitfalls to Avoid

- Do NOT read the entire file into memory at once for very large files — use line-by-line reading with a cap
- Do NOT use `run_in_terminal(["cat", file])` for reading output files — the `run_in_terminal` line dedup logic (`last_lines_diff`) would suppress repeated lines, losing diagnostic information
- Do NOT log the full content of very large stdout files — cap at the last 200 lines in the logging output (the full content is in `stdout_content` for programmatic use)
- Do NOT change `submit_job()` SLURM flags — the `--output` and `--error` flags are already correct

## Testing Requirements

### Unit Tests

- Test `read_job_output_files()` with both files existing and containing content
- Test with only stdout existing (no stderr)
- Test with neither file existing
- Test with a very large stdout file (verify truncation to 10000 lines)
- Test with files containing non-UTF-8 bytes (verify `errors="replace"` handles gracefully)

### Integration Tests

Not applicable (local file I/O only).

## Dependencies

- **Blocked By**: ticket-006-add-sacct-post-completion.md
- **Blocks**: None within Epic 02. Epic 03 tickets may reference `JobCompletionInfo` and `JobOutputFiles` for error categorization.

## Effort Estimate

**Points**: 2
**Confidence**: High
