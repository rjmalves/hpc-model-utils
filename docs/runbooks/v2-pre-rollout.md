# Runbook: v2 pre-rollout validation on the [v2] copies

**Status.** The [v2] copies this runbook validated were retired at the
ticket-067 switch: the original workflows now carry their content, and
`docs/runbooks/v2-switch.md` is the live procedure. P1-P4 below remain the
procedures the switch runbook reuses, run against the originals.

This runbook is the R133 gate (ADR-047) between applying the [v2] workflow
copies (`NEWAVE - PEM [v2]`, `DECOMP - PEM [v2]`, `Upload NEWAVE [v2]`) and
switching the original workflows to v2. The original workflows stay on their
v1 pins until every check below reads PASS.

The repository is public, so this file uses placeholders only (R104, R127).
Every real value lives in the private environment file and in the private
cluster-facts record, and is referenced here by its key or by its fact id
(F1-F16). Record evidence only in the private evidence record, never in this
file.

The matrix covers R85, R126 and R2, plus R133's three additions: v2-on-v2
chained DECOMP cut selection (a), prd log completeness (b) and FC
stage-mismatch evidence (c). It also covers R137's synthesis-failure annotation
and the AM-001/AM-001b identifier canaries.

## Preparation

### Placeholders

| Placeholder | Where its value comes from |
| --- | --- |
| `<modelops-host>`, `<api-port>` | the ModelOps API host and port (cluster facts F16) |
| `<head-node>` | the prd head node (the host of the F1-F15 probe) |
| `<user>` | the operator's SSH user |
| `<your.login>` | the operator's ModelOps login (also `MODELOPS_USER`) |
| `<env-file>` | the private environment file that `apply sync` reads |
| `<newave-queue>`, `<decomp-queue>` | env `queueNewave`, `queueDecomp` (the F6 partitions) |
| `<artifacts-bucket>` | env `outputsBucket` |
| `<versions-bucket>` | env `versionsBucket` |
| `<root-path>` | env `rootPath`, the workspace parent (F5, F15) |
| `<tools-root>` | env `toolsRoot` |
| `<uv-bin>` | env `uvBin` |
| `<mpich-path>`, `<slurm-path>` | env `mpichPath`, `slurmPath` |
| `<newave-v2-workflow-id>`, `<decomp-v2-workflow-id>`, `<upload-v2-workflow-id>` | env `workflows.newave-pem-v2`, `workflows.decomp-pem-v2`, `workflows.upload-newave-v2` |
| `<utils-tag>`, `<utils-sha40>` | the [v2] copies' `utilsAppVersion` and `utilsAppSha` parameters (the release pins) |
| `<newave-synthesis-tag>`, `<newave-synthesis-sha40>` | `NEWAVE - PEM [v2]`'s `synthesisAppVersion` and `synthesisAppSha` |
| `<decomp-synthesis-tag>`, `<decomp-synthesis-sha40>` | `DECOMP - PEM [v2]`'s `synthesisAppVersion` and `synthesisAppSha` |
| `<X.Y.Z>` | the ensure Task's `--python` interpreter (ticket-063b) |
| `<applied-sha>` | the `[deploy/modelops <sha>]` stamp at the end of the ensure Task's `observation` |
| `<v1-newave-run-uri>`, `<v1-decomp-run-uri>` | `s3://<artifacts-bucket>/artifacts/<hash>/` of a historical v1 run |
| `<execution-id>`, `<hash>` | a ModelOps ExecutionId, and the execution's ExecutionHash (its artifacts prefix) |
| `<model-id>`, `<finalize-id>`, `<finalize-job-id>` | the Slurm job ids from the `run` Task's `Submitted batch job` lines |
| `<deck-uri>`, `<decomp-deck-uri>` | the `s3://` URI of a test deck chosen for the check |
| `<model-version>`, `<cores>`, `<hours>` | the [v2] copy's `modelVersion`, `coreCount` and `jobTimeoutHours` defaults |
| `<stamp>` | the UTC stamp in a dry-run file name (P2) |
| `<evidence-dir>` | a private directory on the workstation for raw evidence files |
| `<head-scratch>` | a private scratch directory of the operator on the head node |

The workstation needs `uv`, `jq` and `curl`; the head node needs `jq` and the
AWS CLI. Every check writes its raw files under `<evidence-dir>/M<n>/` (workstation)
or `<head-scratch>/M<n>/` (head node), and records its result in the
private evidence record.

### P1. ModelOps API session (SSH tunnel)

The prd API serves plain HTTP (F16). It is reached only through an SSH tunnel,
so the bearer token never crosses the network unencrypted. The client accepts
plain `http://` only for a loopback host. Never put the token on a command line.

```bash
# terminal 1: the tunnel; local port 8080 is the operator's choice (if the API
# does not listen on the host's loopback, use <api-host>:<api-port> instead)
ssh -N -L 8080:localhost:<api-port> <user>@<modelops-host>

# terminal 2, from the repository root (a clean checkout of the applied commit)
export MODELOPS_URL=http://localhost:8080 MODELOPS_USER=<your.login>
read -rs MODELOPS_TOKEN && export MODELOPS_TOKEN
EV=<evidence-dir>
# ... the checks ...
unset MODELOPS_TOKEN MODELOPS_URL MODELOPS_USER
```

### P2. The definitions dry run

The dry run is read-only. It prints one status line per Task and Workflow, then
the `IDENTIFIER-GAP` lines, the `PIN` lines and a `summary:` line. This
runbook never uses `sync --apply`.

```bash
mkdir -p "$EV/M17"
uv run python -m deploy.modelops.apply sync --env-file <env-file> \
  | tee "$EV/M17/sync-$(date -u +%Y%m%dT%H%M%SZ).txt"
```

### P3. Starting a [v2] execution with chosen parameters

1. In the ModelOps web UI (F16 gives its path), open the workflow named by the
   check: `NEWAVE - PEM [v2]`, `DECOMP - PEM [v2]` or `Upload NEWAVE [v2]`.
   Never start an original workflow for this matrix (R133).
2. Start an execution. Set the execution name the check gives, and change only
   the parameters the check names (for example `inputFile`, `parentPath`,
   `jobTimeoutHours`). Leave every other parameter at the copy's default.
3. Record the ExecutionId, the execution name, the parameters you changed and
   the start time (UTC).

### P4. Exporting an execution's stored log (read-only API)

The export reads the stored execution record with the repository's own client
(`ModelOpsClient.get_json`, the same redaction and transport rules as
`apply`). It writes two files:

- `execution-<execution-id>.json`: the raw record. It holds the status,
  annotation, artifacts path, metadata, the task list with each `RawCommand`,
  and every stored log entry.
- `execution-<execution-id>.log`: one line per stored log line, shaped
  `<timestamp> [<Type>] <task name>: <text>`.

If the endpoint answers differently from what the formatter expects, the export
stops with an `export:` error. The row is then **BLOCKED**: record the error
and escalate. A hand copy from the UI is never accepted for M14, because it
cannot prove completeness.

```bash
export_execution() {  # $1 = execution id, $2 = output directory
  mkdir -p -- "$2"
  uv run python - "$1" "$2" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

from deploy.modelops.modelops_api import ModelOpsClient

execution_id, out = sys.argv[1], Path(sys.argv[2])
if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", execution_id):
    sys.exit("export: not an execution id")
doc = ModelOpsClient.from_env(os.environ).get_json(
    f"/api/WorkflowExecution/executions/{execution_id}"
)
if not isinstance(doc, dict) or not isinstance(
    doc.get("workflowExecutionResponse"), list
):
    sys.exit("export: unexpected response shape; the row is BLOCKED")
(out / f"execution-{execution_id}.json").write_text(
    json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
# ModelOps OutputType, in declaration order.
types = [
    "Info", "Debug", "Error", "ExecutionOutput", "ErrorOutput",
    "ExecutionStart", "ExecutionEnd", "TaskChanged",
    "ExecutionStatusUpdate", "ParameterUpdate",
]
tasks = {
    t.get("taskExecutionId"): t.get("taskName")
    for t in doc.get("tasksExecution") or []
}
lines = []
for entry in doc["workflowExecutionResponse"]:
    kind = entry.get("type")
    name = types[kind] if isinstance(kind, int) and 0 <= kind < len(types) else str(kind)
    text = entry.get("text") or ""
    if text.startswith("Tarefa iniciada: ") and name != "Info":
        sys.exit("export: OutputType order differs; the row is BLOCKED")
    task = tasks.get(entry.get("taskExecutionId")) or "-"
    parts = re.split(r"\r\n|\r|\n", text)
    if len(parts) > 1 and parts[-1] == "":
        parts.pop()
    for part in parts:
        lines.append(f"{entry.get('timestamp')} [{name}] {task}: {part}")
(out / f"execution-{execution_id}.log").write_text(
    "\n".join(lines) + "\n", encoding="utf-8"
)
print(f"export: status={doc.get('executionStatus')} lines={len(lines)} dir={out}")
PY
}
```

Use it as `export_execution <execution-id> "$EV/M<n>"`, after the execution
has reached a terminal status.

### P5. The stored-hook check (positive checks, AM-001b)

ModelOps swallows an identifier exception without logging it (SR-010), so no
check here relies on an error message being absent. For each hook line a task
prints (`${CurrentExecution.…}`), ModelOps logs an Info entry `Trecho de
código detectado: …` before it evaluates that hook. A swallowed exception
skips the rest of the task, including its `Tarefa finalizada: <task>` entry.
The check below compares what was emitted with what was stored:

- the status the last status hook sets, against the stored status;
- emitted hook lines, against detected code blocks;
- every emitted `SetMetadata` key, against the stored metadata;
- an emitted annotation and artifacts path, against the stored ones;
- every `Tarefa iniciada`, against a matching `Tarefa finalizada`;
- with a name file, the stored execution name and the name substituted into the
  `preprocess` Task's `RawCommand`, against the expected name, byte for byte.

```bash
check_hooks() {  # $1 = execution-<id>.json, $2 = optional file holding the expected name
  uv run python - "$@" <<'PY'
import json
import re
import sys
from collections import Counter

doc = json.load(open(sys.argv[1], encoding="utf-8"))
entries = doc["workflowExecutionResponse"]

def texts(kind, prefix=""):
    return [
        e.get("text") or ""
        for e in entries
        if e.get("type") == kind and (e.get("text") or "").startswith(prefix)
    ]

emitted = [t for t in texts(3) if "${CurrentExecution." in t]
detected = texts(0, "Trecho de código detectado: CurrentExecution.")
joined = "\n".join(emitted)
hooks = re.findall(
    r"\$\{CurrentExecution\.Set(Success|DataError|ModelError|RuntimeError)\(", joined
)
hook_status = hooks[-1] if hooks else None
stored_status = doc.get("executionStatus")
keys = set(re.findall(r'\$\{CurrentExecution\.SetMetadata\("([a-z0-9_.]+)"', joined))
stored_keys = {m.get("key") for m in doc.get("executionMetadata") or []}
missing = sorted(keys - stored_keys)
ann_ok = "SetAnnotation(" not in joined or bool(doc.get("executionAnnotation"))
art_ok = "SetExecutionArtifactsPath(" not in joined or bool(doc.get("executionArtifactsPath"))
started = Counter(t.removeprefix("Tarefa iniciada: ") for t in texts(0, "Tarefa iniciada: "))
finished = Counter(t.removeprefix("Tarefa finalizada: ") for t in texts(0, "Tarefa finalizada: "))
unfinished = sorted((started - finished).elements())
ok = (
    hook_status == stored_status
    and len(emitted) == len(detected)
    and not missing and ann_ok and art_ok and not unfinished
)
print(f"status: stored={stored_status} hook={hook_status}")
print(f"hooks: emitted={len(emitted)} detected={len(detected)}")
print(f"metadata keys emitted but not stored: {missing}")
print(f"annotation stored: {ann_ok}; artifacts path stored: {art_ok}")
print(f"tasks started={sum(started.values())} finished={sum(finished.values())} unfinished={unfinished}")
if len(sys.argv) > 2:
    expected = open(sys.argv[2], encoding="utf-8").read().removesuffix("\n")
    stored_name = doc.get("executionName")
    raw = next(
        (t.get("rawCommand") or "" for t in doc.get("tasksExecution") or []
         if (t.get("taskName") or "").startswith("Preprocessamento")), ""
    )
    match = re.search(r"read -r -d '' EXECUTION_NAME <<'(HPCMU_[^']*)'[^\n]*\n(.*?)\n\1", raw, re.S)
    if match is None:
        substituted = "<not found in RawCommand>"
    else:
        substituted = match.group(2)
    print(f"name stored verbatim: {stored_name == expected}")
    print(f"name substituted verbatim: {substituted == expected} ({substituted!r})")
    ok = ok and stored_name == expected and substituted == expected
print(f"hooks-check: {'PASS' if ok else 'FAIL'}")
PY
}
```

If `RawCommand` still shows the `{{CurrentExecution.ExecutionName}}` template,
the substituted name prints as `<not found in RawCommand>`. Record that, and
judge the name by the stored name and the row's other evidence.

### P6. Reading Slurm within the MinJobAge window

Slurm accounting is off in prd (F1), so `sacct` is never used. Every Slurm fact
comes from `scontrol show job <id>`. slurmctld keeps a finished job's record for
MinJobAge = 300 s only (F2). Read a job while it runs, or within 300 s of its
end. Job ids come from the `run` Task's `Submitted batch job <id>` lines: the
model job first, then the finalize job (ADR-020).

```bash
# on the head node
watch_job() {  # $1 = job id, $2 = output file; waits while the job is queued, then records it
  while scontrol show job "$1" 2>/dev/null | grep -qE 'JobState=(PENDING|CONFIGURING)'; do sleep 15; done
  { date -u +%Y-%m-%dT%H:%M:%SZ; scontrol show job "$1"; } | tee "$2"
}
```

### P7. Where the artifacts live

A [v2] run publishes under `s3://<artifacts-bucket>/artifacts/<hash>/`, where
`<hash>` is the execution's ExecutionHash: `entradas/`, `saidas/` (with
`saidas/run.json`, `saidas/metadata.modelops` and `saidas/logs/<phase>-<jobid>.out`)
and `sintese/`. The stored `executionArtifactsPath` names the prefix. Read S3
with the AWS CLI on the head node, which uses the node's IAM role.

```bash
# on the head node
s3_keys() {  # $1 = s3://bucket/prefix/, $2 = saidas | sintese; prints keys relative to the prefix
  local uri=${1%/}/ bucket prefix
  bucket=${uri#s3://}; bucket=${bucket%%/*}
  prefix=${uri#"s3://$bucket/"}
  aws s3api list-objects-v2 --bucket "$bucket" --prefix "$prefix$2/" \
    --query 'Contents[].[Key]' --output text \
    | grep -v '^None$' | sed "s#^$prefix##" | LC_ALL=C sort
}
```

Copy head-node files to the workstation with
`mkdir -p "$EV/M<n>" && scp <user>@<head-node>:<head-scratch>/M<n>/<file> "$EV/M<n>/"`.

### P8. Order and run sharing

1. **M1 first.** The first [v2] execution is the first real install, the miss
   path. It is also the first real exercise of ticket-060's venv guards (F11).
2. **M2 next**, then **M17**.
3. The rest follow in any order. M15 accumulates across M5, M6, M9 and every
   other chained DECOMP run.

One execution may serve several rows when it meets each row's conditions, for
example a NEWAVE run that is the M6 parent and also the M4, M12 and M14 NEWAVE
run. Record its ExecutionId under every row it serves.

## M1 First real install, the miss path

**Requirements:** ADR-044 (R131, R97).

**Steps.**

1. Before the first [v2] execution, confirm that no install exists yet:
   `ls -la <tools-root>` on the head node. Record the listing; `.logs/` may be
   absent.
2. Start the first [v2] execution (P3), normally `NEWAVE - PEM [v2]` with a
   known-good deck. Its ensure Task installs `hpc-model-utils` and
   `sintetizador-newave`. The first DECOMP run later installs
   `sintetizador-decomp`; repeat steps 3-4 for that tool after it.
3. When the ensure Task has finished, run on the head node:

   ```bash
   R=<tools-root>
   for d in "$R/hpc-model-utils/<utils-sha40>" "$R/sintetizador-newave/<newave-synthesis-sha40>"; do
     echo "== $d"
     cat -- "$d/.ready"
     grep -E '^home ?=' -- "$d/.venv/pyvenv.cfg"
     readlink -f -- "$d/.venv/bin/python"
     echo "writable entries (expect none):"
     find "$d" ! -type l -perm /222 -print | head -n 5
   done
   ls -l -- "$R/.logs/"
   tail -n 1 -- "$R"/.logs/*.log
   ```

4. Export the execution (P4), and record the ensure Task's
   `HPCMU_TOOL <name> <dir>` lines.

**Evidence:** the ExecutionId; the step 1 listing; the step 3 output verbatim;
the `HPCMU_TOOL` lines; the UTC timestamps.

**Pass criterion:** for every tool the run installed:
- `.ready` holds `python=<X.Y.Z>`;
- the `pyvenv.cfg` `home` line names `<tools-root>/.python/cpython-<X.Y.Z>-…/bin`;
- `find` prints no writable entry, so the install directory is read-only;
- exactly one `<tools-root>/.logs/<tool>-<sha40>-*.log` exists for that tool, ending with `== installed <dir>`.

## M2 Cache hit

**Requirements:** R2 (ADR-030).

**Steps.**

1. On the workstation, extract the script the ensure Task inlines, at the
   applied commit, and copy it to the head node:

   ```bash
   mkdir -p "$EV/M2"
   git show <applied-sha>:deploy/modelops/scripts/ensure-tools.sh > "$EV/M2/ensure-tools.sh"
   ssh <user>@<head-node> 'mkdir -p <head-scratch>/M2'
   scp "$EV/M2/ensure-tools.sh" <user>@<head-node>:<head-scratch>/M2/
   ```

2. On the head node, time the rendered invocation five times. It is the ensure
   Task's `bash -s -- …` line with the NEWAVE copy's pins, run with the script
   on stdin:

   ```bash
   mkdir -p <head-scratch>/M2 && cd <head-scratch>/M2
   ls -1 <tools-root>/.logs | wc -l > logs-before.txt
   TIMEFORMAT='%R'
   for i in 1 2 3 4 5; do
     { time bash -s -- --root '<tools-root>' --uv '<uv-bin>' --python '<X.Y.Z>' \
         --tool hpc-model-utils https://github.com/rjmalves/hpc-model-utils.git '<utils-tag>' '<utils-sha40>' hpc-model-utils \
         --tool sintetizador-newave https://github.com/rjmalves/sintetizador-newave.git '<newave-synthesis-tag>' '<newave-synthesis-sha40>' sintetizador-newave \
         <ensure-tools.sh >"stdout.$i"; } 2>>times.txt
     echo "run $i exit $?" >>exits.txt
   done
   ls -1 <tools-root>/.logs | wc -l > logs-after.txt
   cat times.txt exits.txt logs-before.txt logs-after.txt
   sort -n times.txt | tail -n 1   # nearest-rank p95 of 5 samples = the maximum
   ```

3. Start a second [v2] NEWAVE execution (P3), export it (P4), and read the
   ensure Task's start and end times from `tasksExecution`. This duration is
   recorded as information only.

**Evidence:** the five times; the exit codes; each `stdout.<i>` (two
`HPCMU_TOOL` lines each); the `.logs` counts before and after; the
ExecutionId and the ensure Task's ModelOps duration.

**Pass criterion:** all five runs exit 0 and print both `HPCMU_TOOL` lines; the nearest-rank p95 (the maximum of the 5 samples) is < 1.0 s; the `.logs` count is unchanged, so the hit created no log file.

## M3 Status before stderr: a deliberate fatal DataError

**Requirements:** R85, R126.

**Steps.**

1. Build a DECOMP test deck whose dadger keeps its `FC NEWV21` and `FC NEWCUT`
   records, but with the NEWCUT cut file removed from the deck archive. Upload
   it to a test location in S3.
2. Start `DECOMP - PEM [v2]` (P3) with `inputFile` = that deck and `parentPath`
   left empty. The FC coupling (`cuts.apply_coupling`) then raises the
   DataError in the **`preprocess`** Task, before the `run` Task ever starts.
3. Export the execution (P4), then:

   ```bash
   L="$EV/M3/execution-<execution-id>.log"
   P='Preprocessamento especifico do modelo (v2)'
   grep -nF "[Info] $P: Trecho de código detectado: CurrentExecution.SetDataError(" "$L"
   grep -nF "[ErrorOutput] $P: " "$L"
   grep -nF 'Tarefa finalizada: ' "$L"
   jq -r .executionStatus "$EV/M3/execution-<execution-id>.json"
   ```

**Evidence:** the ExecutionId; the deck change made; the four outputs above
verbatim, with line numbers.

**Pass criterion:** the stored status is `DataError`; in the `preprocess` Task, the line number of the `SetDataError` code-block entry is lower than that of its first `[ErrorOutput]` entry; the cancellation branch logs `Tarefa finalizada: Cancela job submetido na fila do SLURM (v2)` and `Tarefa finalizada: Remove diretorio temporario da execucao (v2)`.

## M4 Finalize node shape on both queues

**Requirements:** R85, R117, ADR-051 (F3, F6, DES-06).

**Steps.**

1. Pick one NEWAVE [v2] run on `<newave-queue>` and one DECOMP [v2] run on
   `<decomp-queue>`. When each `run` Task prints its second
   `Submitted batch job <id>` line (the finalize job), start on the head node:

   ```bash
   mkdir -p <head-scratch>/M4
   watch_job <finalize-job-id> <head-scratch>/M4/finalize-<finalize-job-id>.scontrol
   grep -oE 'JobState=[^ ]+|Partition=[^ ]+|NumNodes=[^ ]+|NumCPUs=[^ ]+|OverSubscribe=[^ ]+|Shared=[^ ]+|TRES=[^ ]+' \
     <head-scratch>/M4/finalize-<finalize-job-id>.scontrol
   ```

   If the job ended before `watch_job` saw it running, run
   `scontrol show job <finalize-job-id>` at once. It still answers within
   300 s of the end (F2).
2. After the run publishes, take the `HPCMU_SHAPE` line from the finalize job's
   log: `grep -h '^HPCMU_SHAPE ' finalize-<finalize-job-id>.out`. The log is
   under `saidas/logs/` (P7).
3. Convert the `TRES=` `mem=` value to MB: no suffix or `M` as is, `G` × 1024,
   `T` × 1048576. Compare it with the F6 configured memory of that partition,
   to the printed precision. Do not read a cgroup memory file, and do not use `sacct … AllocTRES`: accounting is not enabled (F1), and the allocation is the evidence (DES-06).
4. Record the `HPCMU_SHAPE` fields. `mem_per_node` is the first probe of what
   `SLURM_MEM_PER_NODE` holds for a `--mem=0` job under `CR_CPU` (F3).
   `mem_total_kb` is the kernel's MemTotal. Record it only; it is not compared
   with Slurm's configured memory.

**Evidence:** per queue, the ExecutionId, the finalize job id, the `scontrol`
output with its UTC read time, the converted memory in MB, and the
`HPCMU_SHAPE` line verbatim.

**Pass criterion:** for each queue, `scontrol show job <finalize id>`, read while the job ran or within 300 s of its end, shows `NumNodes=1`, exclusive (`OverSubscribe=NO`, or `Shared=0` on an older Slurm), `NumCPUs` = the F6 CPUs per node, and a `TRES=` memory equal to the F6 configured memory after unit conversion; the `HPCMU_SHAPE` line is recorded.

## M5 DECOMP cut selection, v2 on a v1 NEWAVE parent

**Requirements:** R85, R126 (ADR-016).

**Steps.**

1. Pick a historical successful v1 NEWAVE run, `<v1-newave-run-uri>`, and a
   DECOMP deck whose dadger `FC NEWV21`/`FC NEWCUT` records name files that
   are members of that run's `saidas/cortes.zip`.
2. Start `DECOMP - PEM [v2]` (P3) with that deck and
   `parentPath` = `<v1-newave-run-uri>`. Export it (P4).
3. Collect the three pieces of coupling evidence:

   ```bash
   # workstation: the coupling line that preprocess logs
   grep -nF 'hpc_model_utils.models.decomp.cuts: FC cut coupling: CutCoupling(' "$EV/M5/execution-<execution-id>.log"
   # head node: the parent's cut archive
   mkdir -p <head-scratch>/M5
   aws s3 cp "<v1-newave-run-uri>saidas/cortes.zip" <head-scratch>/M5/parent-cortes.zip
   unzip -l <head-scratch>/M5/parent-cortes.zip
   # head node: the run's parent record
   aws s3 cp "s3://<artifacts-bucket>/artifacts/<hash>/saidas/run.json" - | jq '.parent'
   ```

`run.json` itself carries no cut-coupling item. A durable `run.json` coupling
record is a candidate for a later release.

**Evidence:** the ExecutionId; the stored status; the `FC cut coupling` line
verbatim; the `unzip -l` listing; the `run.json` `parent` object.

**Pass criterion:** the stored status is `Success`; the `FC cut coupling` line shows `source='parent'`; both its `header` and `cuts` names appear in the `unzip -l` listing of the parent's `cortes.zip`; `run.json` `parent.path` equals the parent URI.

## M6 DECOMP cut selection, v2 on a v2 NEWAVE parent

**Requirements:** R133 a, R126.

**Steps.** As M5, with a successful `NEWAVE - PEM [v2]` run as the parent:
`parentPath` = that run's `s3://<artifacts-bucket>/artifacts/<hash>/`.

**Evidence:** as M5, plus the parent's ExecutionId.

**Pass criterion:** as M5: the stored status is `Success`; the `FC cut coupling` line shows `source='parent'`; both named files are listed in the v2 parent's `cortes.zip`; `run.json` `parent.path` equals the v2 parent URI.

## M7 Hostile and canary names

**Requirements:** R126, AM-001, AM-001b (SR-010), and the carried empty-name
check.

**Steps.**

1. Write each canary to its own name file with the top-level block below,
   then copy names into the UI from these files only. The block uses quoted
   heredocs, so no shell expands a name.

   | Canary | Kind | Value | Workflow |
   | --- | --- | --- | --- |
   | K1 | execution name | the C9 hostile name, extended with a backtick `id` substitution | `DECOMP - PEM [v2]` |
   | K2 | execution name | `Path`, `QUEUE` and `JobId` as whole words | `DECOMP - PEM [v2]` |
   | K3 | study name | a deck title containing `path` | `DECOMP - PEM [v2]` |
   | K4 | execution name | the fold characters U+0130 `İ`, U+0131 `ı`, U+017F `ſ`, U+212A `K` (Kelvin sign) | `DECOMP - PEM [v2]` |
   | K5 | execution name | `coreCount` and `jobTimeoutHours`, in exact case | `DECOMP - PEM [v2]` |
   | K6 | execution name | `CURRENTEXECUTION.EXECUTIONID` | `DECOMP - PEM [v2]` |
   | K7 | execution name | K1 again | `NEWAVE - PEM [v2]` |

   NEWAVE is the only model that writes the execution name into the deck (the
   `dger.dat` title), so K1 also runs there as K7.

```bash
# M7 name files (workstation)
mkdir -p "$EV/M7"
cat > "$EV/M7/K1.name" <<'NAME'
PMO Água 'x' "y" $z $(id) ${HOME} `id`
NAME
cat > "$EV/M7/K2.name" <<'NAME'
Canary Path QUEUE JobId
NAME
cat > "$EV/M7/K4.name" <<'NAME'
Canary İ ı ſ K
NAME
cat > "$EV/M7/K5.name" <<'NAME'
Canary coreCount jobTimeoutHours
NAME
cat > "$EV/M7/K6.name" <<'NAME'
Canary CURRENTEXECUTION.EXECUTIONID
NAME
cp -- "$EV/M7/K1.name" "$EV/M7/K7.name"
```

2. For K3, edit a DECOMP deck's dadger `TE` title to contain the word `path`
   (for example `TE  PMO path canary`), upload it, and start it with a plain
   execution name (`M7 K3 study-name canary`). For K1, K2 and K4-K7, start the
   run with the canary as its execution name and a known-good deck (P3).
3. Export each execution (P4) and run the stored-hook check (P5):

   ```bash
   check_hooks "$EV/M7/execution-<execution-id>.json" "$EV/M7/K<n>.name"   # K1, K2, K4-K7
   check_hooks "$EV/M7/execution-<execution-id>.json"                       # K3
   jq -r '.executionMetadata[] | select(.key == "study_name") | .value' "$EV/M7/execution-<execution-id>.json"   # K3
   ```

   Also record the canary as the CLI saw it:
   - for DECOMP, the `preprocess` line `--execution-name '…' accepted but not applied`;
   - for K7, the `dger.dat` title inside the run's `entradas/deck_processado.zip`.

4. **Empty execution name (carried check).** The v2 `preprocess.sh` rejects an
   empty `CurrentExecution.ExecutionName` (`^[^[:cntrl:]]+$`), and v1 did not.
   Check whether ModelOps can deliver an empty name. Use a known-good DECOMP deck
   for both starts. Once a run's `preprocess` Task has logged `Tarefa finalizada`,
   it may be cancelled from the UI.

   - **E1, UI:** start `DECOMP - PEM [v2]` with the execution-name field left
     empty. If the UI refuses to start it, record the refusal verbatim.
   - **E2, API:** start the same workflow through the tunnel with
     `"executionName": ""`. curl runs with no proxy and without following
     redirects, and it reads the bearer header from a builtin `printf` pipe on
     stdin, so the token is never on a command line or in a temporary file. The command is the E2 block below.
   - For each started run, export it (P4). Record the stored `executionName`, and
     whether the `preprocess` Task logged `Tarefa finalizada` or an
     `[ErrorOutput]` with `hpcmu-task: invalid CurrentExecution.ExecutionName`.

```bash
# M7 E2: empty execution name through the API (workstation, tunnel open)
cat > "$EV/M7/E2-request.json" <<'JSON'
{
  "workflowId": "<decomp-v2-workflow-id>",
  "executionName": "",
  "requesterUserName": "<your.login>",
  "workflowParameters": [{"name": "inputFile", "value": "<decomp-deck-uri>"}]
}
JSON
# printf is a shell builtin: the token reaches curl through a pipe, never
# through argv or a temporary file.
printf 'header = "Authorization: Bearer %s"\n' "$MODELOPS_TOKEN" \
  | curl --silent --show-error --noproxy '*' --max-time 30 \
      -X POST "$MODELOPS_URL/api/WorkflowExecution/ExecuteWorkflow" \
      -H 'Content-Type: application/json' -H 'Accept: application/json' \
      --data-binary @"$EV/M7/E2-request.json" \
      --output "$EV/M7/E2-response.json" --write-out '%{http_code}\n' \
      --config -
jq . "$EV/M7/E2-response.json"
```

**Evidence:** per canary run: the ExecutionId, the name file, the
`check_hooks` output verbatim, the CLI-side echo of the name, and the stored
status. For E1 and E2: the HTTP code and response (E2), the stored
`executionName`, and the `preprocess` outcome lines.

**Pass criterion:** for every canary run K1-K7:
- `check_hooks` prints `hooks-check: PASS`: the terminal status equals the emitted status hook; every hook after the canary, in the same and later tasks, is stored; and `Tarefa finalizada: <task>` appears for every started task;
- the name arrives verbatim: the stored name, and the name substituted into `preprocess`, equal the name file byte for byte; for K3, the stored `study_name` contains `path`.

For E1 and E2, no run fails in `preprocess` with `hpcmu-task: invalid CurrentExecution.ExecutionName`. A UI that refuses an empty name counts as no such run.

**Fail rule (empty name):** if E1 or E2 reaches `preprocess` with an empty name and fails there, M7 is FAIL: the [v2] copies would cancel where v1 ran. The remedy is the carried one-character relaxation of `deploy/modelops/tasks/preprocess.sh`, from `^[^[:cntrl:]]+$` to `^[^[:cntrl:]]*$`. It is decided, released and re-applied outside this runbook, and M7 is then re-run.

## M8 UI cancel during the model job

**Requirements:** R126 (R136).

**Steps.**

1. Start a [v2] execution (P3) with a deck that runs long enough to cancel.
   When the `run` Task has printed both `Submitted batch job <id>` lines and
   `squeue -h -j <model-id> -o %T` shows `RUNNING`, press Cancel in the UI.
   Record the UTC time.
2. On the head node, poll both ids immediately. A finished job can stay listed
   in a terminal state for MinJobAge (F2), so "absent" means that neither id is
   in a non-terminal state. That is the CLI's own `wait_gone` rule.

   ```bash
   mkdir -p <head-scratch>/M8
   t0=$(date -u +%s)
   while :; do
     now=$(date -u +%s)
     rows=$(squeue -h -j <model-id>,<finalize-id> -o '%i|%T' 2>&1)
     echo "$(date -u +%H:%M:%SZ) +$((now - t0))s ${rows//$'\n'/ ; }"
     grep -qE '\|(PENDING|CONFIGURING|RUNNING|COMPLETING|REQUEUED|SUSPENDED|RESIZING|REQUEUE_HOLD|REQUEUE_FED|STOPPED|SIGNALING)' <<<"$rows" || break
     ((now - t0 >= 120)) && break
     sleep 5
   done | tee <head-scratch>/M8/squeue.txt
   ```

3. Export the execution (P4) and `grep -nF 'Tarefa finalizada: ' "$EV/M8/execution-<execution-id>.log"`.

**Evidence:** the ExecutionId; both job ids; the cancel time; `squeue.txt`;
the stored status; the `Tarefa finalizada` lines.

**Pass criterion:** within 60 s of the cancel, neither job id is in a non-terminal `squeue` state; the stored status is `Canceled`; the cancellation tasks finished (`Tarefa finalizada` for `Cancela job submetido na fila do SLURM (v2)` and `Remove diretorio temporario da execucao (v2)`).

## M9 INFEASIBLE deck flexibilized by encadeador

**Requirements:** R126, and the carried ExecutionId-reuse check (ADR-050).

**Steps.**

1. In encadeador, set up a **test** study (never a production study) whose
   DECOMP submissions target `<decomp-v2-workflow-id>`, with a deck known to
   make DECOMP infeasible.
2. Let encadeador submit the first run. Once it ends, export it (P4), then
   record its stored status and annotation, and encadeador's record of the
   flexibilization.
3. Let encadeador submit the next run. Export it (P4).
4. **ExecutionId reuse (carried check).** The heredoc delimiter
   `HPCMU_<ExecutionId>` relies on the id being unknown when a parameter value
   is supplied. Record the two ExecutionIds side by side. Then, in the prd UI,
   inspect the first run's detail view and the executions list. Record whether
   any re-run, retry or resume action exists for a [v2] execution, with its
   label. If one exists, use it once on the first run, record the ExecutionId
   it produces, and export that run as well.

**Evidence:** both ExecutionIds (and any UI re-run's); the first run's stored
status and annotation; encadeador's flexibilization record and the next run's
submission; the UI action inventory.

**Pass criterion:** the first run's stored status is `ModelError`, with an annotation that starts `INFEASIBLE:` and carries `[decomp.infeasible]`; encadeador records a flexibilization and submits the next run; the next run's ExecutionId differs from the first run's (and any UI re-run has an ExecutionId differing from every earlier one); the evidence records whether the prd UI offers a re-run or resume action.

## M10 Upload NEWAVE offline

**Requirements:** R126 (R96).

**Steps.**

1. Start `Upload NEWAVE [v2]` (P3) with `inputFile`, `outputFile` and `cutFile`
   pointing to an offline-executed NEWAVE run's deck, outputs and cuts.
2. Export it (P4), then on the head node:
   `s3_keys "s3://<artifacts-bucket>/artifacts/<hash>/" saidas` and
   `aws s3 cp "s3://<artifacts-bucket>/artifacts/<hash>/saidas/metadata.modelops" -`.

**Evidence:** the ExecutionId; the stored status, metadata and
`executionArtifactsPath`; the key listing; `metadata.modelops` verbatim.

**Pass criterion:** the stored status is `Success`; the stored metadata and `saidas/metadata.modelops` both carry `execution_source=OFFLINE`; the artifacts are under `s3://<artifacts-bucket>/artifacts/<hash>/`, with `<hash>` the execution's ExecutionHash.

## M11 Ranqueamento after the [v2] apply

**Requirements:** R126, R89.

**Steps.**

1. Run Ranqueamento Prospectivo as usual. It uses the shared Tasks (R89), which
   the [v2] apply must not have touched. Export it (P4).
2. After it ends, run the dry run (P2), then:
   `grep -E '^[A-Z]+ task v1-' "$EV/M17/sync-<stamp>.txt"`.

**Evidence:** the Ranqueamento ExecutionId and stored status; the `task v1-`
lines verbatim; the dry run's `summary:` line.

**Pass criterion:** Ranqueamento Prospectivo's stored status is `Success`; the dry run reports every `v1-` Task (14 lines) as `UNCHANGED`.

## M12 Artifact and parquet-schema parity against a historical v1 run

**Requirements:** R126 (R55, R16, R17).

**Steps.** Do this once for NEWAVE and once for DECOMP. Pick a historical v1 run
(`<v1-newave-run-uri>` or `<v1-decomp-run-uri>`), and start the [v2] copy (P3)
with the same deck (and, for DECOMP, the same `parentPath`). Then, on the
head node:

1. **Key sets.** Run block 1 below. Match every diff line to a row of the
   "Intended differences" table in
   [`tests/goldens/v1_1_2/README.md`](../../tests/goldens/v1_1_2/README.md).
   Those rows include:
   - `stdout.modelops`/`stderr.modelops` replaced by `saidas/logs/<phase>-<jobid>.out`;
   - `saidas/run.json` added;
   - `saidas/relgnl.<ext>` added for DECOMP;
   - the residual NEWAVE `saidas/{bid,elnino,ensoaux,itaipu}.dat` now inside `deck_processado.zip`;
   - `saidas/status.modelops`.

   The `entradas/<dadger>` echo sits outside these two prefixes.
2. **Parquet schemas.** Run block 2 below. It reads each schema with pyarrow
   (`pyarrow.parquet.read_schema`, which reads only the file footer), using the
   interpreter of the pinned sintetizador install under `<tools-root>`.
   sintetizador writes these files, and both pinned sintetizador lockfiles
   carry pyarrow as a direct dependency.

```bash
# M12 block 1: key sets (head node)
mkdir -p <head-scratch>/M12 && cd <head-scratch>/M12
V1=<v1-newave-run-uri>                       # or <v1-decomp-run-uri>
V2=s3://<artifacts-bucket>/artifacts/<hash>/  # the [v2] run
for d in saidas sintese; do
  s3_keys "$V1" "$d" > "v1-$d.keys"
  s3_keys "$V2" "$d" > "v2-$d.keys"
  diff "v1-$d.keys" "v2-$d.keys" > "$d.diff"; echo "$d diff exit $?"
done
cat saidas.diff sintese.diff
```

```bash
# M12 block 2: parquet schemas (head node, same directory and variables)
aws s3 cp --recursive --exclude '*' --include '*.parquet' "${V1%/}/sintese/" v1-sintese/
aws s3 cp --recursive --exclude '*' --include '*.parquet' "${V2%/}/sintese/" v2-sintese/
PY=<tools-root>/sintetizador-newave/<newave-synthesis-sha40>/.venv/bin/python   # or the DECOMP install
env -u PYTHONPATH -u PYTHONHOME "$PY" -I - v1-sintese v2-sintese <<'PY' | tee parquet.txt
import sys
from pathlib import Path

import pyarrow.parquet as pq

a, b = (Path(p) for p in sys.argv[1:3])
names = sorted(
    {p.relative_to(a).as_posix() for p in a.rglob("*.parquet")}
    | {p.relative_to(b).as_posix() for p in b.rglob("*.parquet")}
)
bad = 0
for name in names:
    pa_, pb_ = a / name, b / name
    if not (pa_.is_file() and pb_.is_file()):
        print(f"ONLY-ONE-SIDE {name}")
        bad += 1
        continue
    sa = [(f.name, str(f.type)) for f in pq.read_schema(pa_)]
    sb = [(f.name, str(f.type)) for f in pq.read_schema(pb_)]
    if sa == sb:
        print(f"SAME {name} columns={len(sa)}")
    else:
        bad += 1
        print(f"DIFF {name}")
        print(f"  v1: {sa}")
        print(f"  v2: {sb}")
print(f"parquet: {'PASS' if bad == 0 else 'FAIL'} files={len(names)} differing={bad}")
PY
```

**Evidence:** per model, the v1 URI, the [v2] ExecutionId, both diffs, each
diff line mapped to its intended-differences row, and `parquet.txt` verbatim.

**Pass criterion:** for each model, the `saidas/` and `sintese/` key sets are equal apart from rows of the intended-differences table; `parquet.txt` ends `parquet: PASS`, so every `sintese/*.parquet` has identical column names and dtypes on both sides.

## M13 1-hour TIMEOUT

**Requirements:** R126 (ADR-022, F1, F2).

**Steps.**

1. In any operator-chosen window, start `NEWAVE - PEM [v2]` (P3) with
   `jobTimeoutHours` = `1` and a deck that runs longer than 1 h. It holds one
   node of `<newave-queue>` for about 1 h. Record the date.
2. When it ends, export it (P4), fetch `saidas/run.json` and both job logs
   (P7), and record:

   ```bash
   jq '.diagnosis | {status, rule_id, reason, evidence}' run.json
   grep -h 'CANCELLED AT\|DUE TO TIME LIMIT\|^HPCMU_START ' model-<model-id>.out finalize-<finalize-id>.out
   ```

**Evidence:** the date and ExecutionId; the stored status and annotation; the
`run.json` diagnosis; the model job's time-limit line and the finalize job's
`HPCMU_START` time, which show how long after the model's end finalize began.

**Pass criterion:** the stored status is `RuntimeError`; the annotation starts `TIMEOUT:` and carries a `[slurm.<rule>]` rule id, **or** the `run.json` evidence holds the L1 fall-through item (`"layer": "slurm"`, `"source": "accounting"`, `"detail": "no sacct/scontrol record"`), recorded as observed (F2).

## M14 prd log completeness

**Requirements:** R133 b, ADR-046 (F15, the E3 regression).

**Steps.** For at least one NEWAVE and one DECOMP [v2] run:

1. Export the execution (P4), and keep only the `run` Task's stored output:

   ```bash
   mkdir -p "$EV/M14"
   grep -F '[ExecutionOutput] Executa e acompanha modelo no SLURM (v2): ' \
     "$EV/M14/execution-<execution-id>.log" > "$EV/M14/relayed-<execution-id>.txt"
   ```

2. On the head node, download the job logs, then copy them to `$EV/M14/` (P7):
   `mkdir -p <head-scratch>/M14/<hash> && aws s3 cp --recursive "s3://<artifacts-bucket>/artifacts/<hash>/saidas/logs/" <head-scratch>/M14/<hash>/`.
3. From the repository root:

   ```bash
   uv run python -m deploy.modelops.check_relay \
     --relayed "$EV/M14/relayed-<execution-id>.txt" \
     --job-log "$EV/M14/model-<model-id>.out" \
     --job-log "$EV/M14/finalize-<finalize-id>.out"
   ```

**Evidence:** per run, the ExecutionId, both job ids and the `check_relay`
output verbatim.

**Pass criterion:** `check_relay` prints `relay: PASS` (exit 0) for the model and finalize logs of at least one NEWAVE and one DECOMP run.

## M15 FC stage-mismatch evidence

**Requirements:** R133 c, R111.

**Steps.** For every chained DECOMP [v2] run (M5, M6, M9 and any other with a
parent), count the warnings that `preprocess` logs:

```bash
grep -cF 'WARNING hpc_model_utils.models.decomp.cuts: FC stage mismatch: ' "$EV/M<n>/execution-<execution-id>.log"
jq '[.diagnosis.evidence[] | select(.source == "dadger FC")]' run.json
```

**Evidence:** per run, the ExecutionId, the count, and any `dadger FC`
evidence item verbatim.

**Pass criterion:** every chained DECOMP run's count of `FC stage mismatch:` warnings is recorded with its ExecutionId. This row records evidence for ticket-079 and passes once recorded.

## M16 Synthesis failure on a SUCCESS run

**Requirements:** R137, ADR-053.

**Steps.** Do this without editing sintetizador.

- **Preferred:** start `NEWAVE - PEM [v2]` (P3) with a NEWAVE deck known to hit
  sintetizador's tolerated `EARPF_REE` error. That error is R137's source:
  the ranqueamento job tolerates it
  (`ranqueamento-prospectivo-utils/assets/jobs/ranqueamento.job:119-122`).
  Export the run (P4) and fetch the real execution's `saidas/run.json` and
  `saidas/metadata.modelops` (P7).
- **Fallback:** run the CLI by hand on the head node in a scratch workspace,
  with `--synthesis-bin /usr/bin/false`. It **writes nothing to S3**: there is
  no `result_upload` and no read of any artifacts prefix. It only reads the
  executables and the deck. The evidence stays in the scratch workspace and on
  stdout.
  - **Hooks:** the login-side `run` command prints only `SetMetadata` hooks on
    stdout, for `job_id`, `status` (here `SUCCESS`) and `synthesis_status`.
    The finalize job runs with `HPCMU_PLATFORM=off` and prints none.
    `run` never emits the terminal `SetSuccess` or `SetAnnotation` hooks;
    those belong to `result_upload`, which the fallback does not run.
  - **Diagnosis and annotation:** without `result_upload`, no
    `saidas/run.json` is rendered. The workspace's `.hpcmu/state.json` holds
    the same `diagnosis` object, and `result_upload` would publish it
    unchanged as `run.json`'s `diagnosis`. Its annotation would be
    `<status>: <reason> [<rule_id>]`, built from that same object; the block
    below prints that form.
  - **Metadata:** `metadata.modelops` is a local file at the workspace root.
    `synthesis_status` is never written there; it exists only as the
    `synthesis_status` metadata hook line on stdout.

```bash
# M16 fallback (head node): local only, no S3 writes. The subshell makes every
# `|| exit 1` fail closed without closing the operator's shell.
(
  mkdir -p <head-scratch>/M16 || exit 1
  U=<tools-root>/hpc-model-utils/<utils-sha40>/.venv/bin/hpc-model-utils
  W=$(mktemp -d -p <root-path> -t newave_XXXXXX) || exit 1
  cd -- "$W" || exit 1
  {
    "$U" check_and_fetch_executables newave "s3://<versions-bucket>/versoes/newave/<model-version>/"
    "$U" check_and_fetch_inputs newave "<deck-uri>" --parent-path "" --delete
    "$U" extract_sanitize_inputs newave
    "$U" preprocess newave --execution-name "M16 synthesis fallback"
    "$U" run newave <newave-queue> <cores> --max-job-time-hours <hours> \
      --mpich-path <mpich-path> --slurm-path <slurm-path> --synthesis-bin /usr/bin/false
  } 2>&1 | tee <head-scratch>/M16/fallback.out
  echo "workspace: $W" | tee <head-scratch>/M16/workspace.txt
  grep -F '${CurrentExecution.SetMetadata(' <head-scratch>/M16/fallback.out | tee <head-scratch>/M16/hooks.txt
  jq '.diagnosis' "$W/.hpcmu/state.json" | tee <head-scratch>/M16/diagnosis.json
  jq -r '.diagnosis | "\(.status): \(.reason) [\(.rule_id)]"' "$W/.hpcmu/state.json" \
    | tee <head-scratch>/M16/annotation.txt   # the annotation result_upload would publish
  cat -- "$W/metadata.modelops" | tee <head-scratch>/M16/metadata.modelops
  cd / && rm -rf -- "$W"   # every piece of evidence is already copied to <head-scratch>/M16
)
```

**Evidence:**
- the route taken;
- the ExecutionId (preferred), or the scratch workspace path (fallback,
  `workspace.txt`);
- the status (preferred: the stored status and the `SetSuccess` hook line in
  the exported log; fallback: the `SetMetadata` `status` hook line in
  `hooks.txt`, and `diagnosis.status` in `.hpcmu/state.json`);
- the annotation verbatim (preferred: the stored annotation; fallback:
  `diagnosis.reason` in `.hpcmu/state.json`, and its published form in
  `annotation.txt`);
- the `diagnosis` (preferred: from `run.json`; fallback: from
  `.hpcmu/state.json`, `diagnosis.json`);
- `synthesis_status` (preferred: the stored metadata; fallback: its
  `SetMetadata` hook line in `hooks.txt`);
- `metadata.modelops` verbatim (preferred: `saidas/metadata.modelops`;
  fallback: the workspace's `metadata.modelops`).

**Pass criterion:** on the preferred route:
- the stored status is `Success`;
- the stored annotation contains `synthesis failed: `;
- the first `run.json` `diagnosis.evidence` item is the synthesis item (`"source": "synthesis"`);
- the stored metadata carries `synthesis_status=failed`.

On the fallback route:
- `hooks.txt` has the `SetMetadata` hook line for `status` = `SUCCESS`, and `.hpcmu/state.json` `diagnosis.status` is `SUCCESS`;
- `diagnosis.reason` starts `synthesis failed: `, so `annotation.txt` reads `SUCCESS: synthesis failed: … [<rule_id>]`;
- the first `.hpcmu/state.json` `diagnosis.evidence` item is the synthesis item (`"source": "synthesis"`);
- `hooks.txt` has the `synthesis_status` metadata hook line with the value `failed`.

## M17 Live identifier diff

**Requirements:** AM-001b (SR-006, SR-012).

**Steps.** Run the dry run (P2) right after M2, then:

```bash
grep -c '^IDENTIFIER-GAP' "$EV/M17/sync-<stamp>.txt"
grep -E '^(PIN|summary:) ' "$EV/M17/sync-<stamp>.txt"
```

**Evidence:** the dry-run file, the `IDENTIFIER-GAP` count, and the `PIN`
and `summary:` lines.

**Pass criterion:** the `sync` dry run prints no `IDENTIFIER-GAP` line (the count is 0).

## M18 Head-node SSH-session locale

**Requirements:** SR-008.

**Steps.** From the workstation, in a non-interactive session:

```bash
mkdir -p "$EV/M18"
ssh <user>@<head-node> 'locale; bash --version | head -n 1' | tee "$EV/M18/locale.txt"
```

**Evidence:** `locale.txt` verbatim, with its UTC time.

**Pass criterion:** the output of the non-interactive `locale` and `bash --version` (first line) is recorded. This row records evidence and passes once recorded.

## Exit

- Record every row in the private evidence record. A row moves from `PENDING`
  to `PASS` only when its pass criterion holds on recorded evidence.
- A row that fails, or that is `BLOCKED` (for example by a failed P4 export),
  stops the rollout. Fix the cause outside this runbook, then re-run the row.
- **ticket-067 (the switch of the original workflows) starts only when every
  row M1-M18 reads PASS.** Until then the original workflows keep their v1 pins
  (R133), and nothing in this runbook moves them.
