# Runbook: roll out the final re-pin to v2.2.1

This runbook is the ticket-083b apply (ADR-033, ADR-054, R89 as amended
2026-10-05, R111, R127, R131). It is the last rollout of the v2 plan and runs
after every earlier rollout has passed. When it ends, `NEWAVE - PEM`,
`DECOMP - PEM`, `Upload NEWAVE` and `cobre` run the `hpc-model-utils` `v2.2.1`
release, and a successful cobre run publishes the cobre-bridge dashboard.

What the apply changes:

- The four workflows `newave-pem`, `decomp-pem`, `upload-newave` and `cobre`
  change their `utilsAppVersion`/`utilsAppSha` pair to the `v2.2.1` pin. The
  first three move from `v2.0.2`, `cobre` from `v2.1.0`. This puts the
  ticket-079 FC stage promotion (R111) and the epic-06 log changes into
  production.
- The workflow `cobre` gains three hidden parameters: the cobre-bridge pin
  `synthesisAppVersion` `v0.17.0` and `synthesisAppSha`
  `1b059ac505a8d57b6549c0a60411c41a9d7167cb`, and `synthesisToolDir`, which
  captures the installed cobre-bridge directory from `ensure-utils`.
- Two Task documents change, both used only by `cobre`: `ensure-utils` now
  installs cobre-bridge next to `hpc-model-utils` (and is renamed to say so),
  and `cobre-run` passes the installed cobre-bridge as `--synthesis-bin`.
- No shared Task document changes. Ranqueamento Prospectivo and Upload Versão
  do not change. Nothing is created or deleted.

The repository is public, so this file uses placeholders only (R104, R127).
Every real value lives in the private environment files. Record evidence only
in the `## Final re-pin` section of the private rollout-evidence record, never
here. This runbook reuses P1 (the tunnel and token), P4 (the
`export_execution` function) and P7 (the `s3_keys` function and the artifacts
layout) of `docs/runbooks/v2-pre-rollout.md`, the M14 `check_relay` steps of
the same runbook, and the `active_executions` function of
`docs/runbooks/v2-switch.md`. `$EV` is the pre-rollout runbook's evidence
directory.

## Preconditions

| Placeholder | Where its value comes from |
| --- | --- |
| `<repin-commit>` | the ticket-088 commit that pins the four workflows to `v2.2.1` |
| `<last-applied-commit>` | the commit stamped by the `## Upload Versão cobre` apply: the `[deploy/modelops <sha>]` stamp recorded in that evidence section |
| `<env-file>` | the private environment file of the Cobre apply, holding the three ids that apply created; this runbook adds no key |
| `<utils-v2.2.1-sha12>` | the first 12 characters of the `utilsAppSha` default of the four workflows (the `hpc-model-utils` `v2.2.1` pin) |
| `<tools-root>` | env `toolsRoot` |
| `<utils-sha40>` | the full `utilsAppSha` default of the four workflows |
| `<artifacts-bucket>`, `<hash>` | env `outputsBucket`, and the execution's ExecutionHash (P7) |
| `<inputs-uri>` | the S3 location the operator picks `inputFile` from |
| `<head-scratch>` | a private scratch directory of the operator on the head node |
| `<execution-id>` | a ModelOps ExecutionId |
| `<model-id>`, `<finalize-id>` | the Slurm job ids from the run Task's `Submitted batch job` lines: the model job first, then the finalize job |
| `<task-id>`, `<workflow-id>` | ids that ModelOps assigned |

**Agent-checked** before this runbook was committed, recorded in the evidence:

- the private release-pin record holds the `hpc-model-utils` `v2.2.1` row,
  and `<utils-v2.2.1-sha12>` is `3e94f2d89207`;
- at `<repin-commit>`, the four workflows carry that pin, and `cobre` carries
  the cobre-bridge `v0.17.0` pin `1b059ac505a8`; the `PIN` lines below pass
  `tests/deploy/test_runbook_pins.py`;
- an offline simulation ran the repository's own `plan()` and `execute()`
  against an in-memory copy of the expected live state (the
  `<last-applied-commit>` tree): it printed every `summary:` line quoted below.

**Operator-checked.** All of the following hold before the Preview. The first
comes first on purpose: nothing below may run without it.

1. **Every earlier rollout has passed, and nothing is running.**
   - In the private rollout-evidence record, the `## Switch`, `## Sweep`,
     `## Cobre apply` and `## Upload Versão cobre` sections read
     `Status: PASS`.
   - In the private cobre validation record, the rows V1, V2, V2b and V3 read
     `PASS` or `WAIVED`.
   - The `## FC stage evidence (ticket-079)` section reads `Status: PASS`. If
     it reads `FAIL`, stop and escalate: the FC stage promotion is reverted in
     a follow-up release, or its formula is revisited for pre-study years.
   - `docs/notices/v2-fc-stage-mismatch.md` has been handed to the encadeador
     developers. Record the date and the recipient.
   - No `NEWAVE - PEM`, `DECOMP - PEM`, `Upload NEWAVE` or `cobre` execution is
     active. Run `active_executions` now, and again immediately before the
     Apply. If any of them is executing, wait for it to end.

   If any is missing, stop. The repository change may already be committed;
   the prd apply may not run. Record the statement and its date in the
   evidence.
2. **The committed re-pin tree.** Work from a clean checkout of
   `<repin-commit>`: the apply refuses a dirty tree and stamps every write
   with `HEAD`. Record `<repin-commit>` and `<last-applied-commit>` in the
   evidence now, because the rollback starts from the second. Do not use the
   parent of `<repin-commit>` as the rollback target: since tickets 080f and
   080g it already holds the changed cobre Tasks.
3. **Tunnel and token** per P1 of the pre-rollout runbook, with the tunnel
   opened as `ssh -N -o ServerAliveInterval=30 -L …`, a token read without
   echo, and `MODELOPS_USER` exported.
4. **The environment file.** `<env-file>` holds `tasks.ensure-utils`,
   `tasks.cobre-run` and `workflows.cobre`, recorded at the Cobre apply. It
   needs no new key.

## Preview

The Preview is a dry run with the environment file. It is read-only.

```bash
EV=<evidence-dir>
mkdir -p "$EV/repin"
uv run python -m deploy.modelops.apply sync --env-file <env-file> \
  | tee "$EV/repin/sync-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
```

Expected output:

- `CHANGED` for exactly the Tasks `cobre-run` and `ensure-utils` and the
  workflows `cobre`, `decomp-pem`, `newave-pem` and `upload-newave`. Each
  `CHANGED` entry is followed by its diff against live (`-` is the rendered
  definition, `+` is live): read them.
  - `newave-pem`, `decomp-pem` and `upload-newave`: the `utilsAppVersion`
    default `"v2.2.1"` against `"v2.0.2"`, and the `utilsAppSha` default, and
    nothing else.
  - `cobre`: the same pair against `"v2.1.0"`, the three new parameters
    `synthesisToolDir`, `synthesisAppVersion` and `synthesisAppSha`, and the
    `order` values of the parameters after them shifting, and nothing else.
  - `ensure-utils`: the name, description and observation naming cobre-bridge,
    and the script installing it.
  - `cobre-run`: the script checking `synthesisToolDir` and passing
    `--synthesis-bin`.
- Every other line `UNCHANGED`, and the six own `v1-` Tasks `PROTECTED`.
- The eleven `PIN` lines, in the order of the workflow slugs:

  ```text
  PIN v2.2.1 3e94f2d89207 ok
  PIN v0.17.0 1b059ac505a8 ok
  PIN v2.2.1 3e94f2d89207 ok
  PIN v3.0.1 b5f4bbaccc7f ok
  PIN v2.2.1 3e94f2d89207 ok
  PIN v2.4.5 00305719ee16 ok
  PIN v1.0.1 f61dd5633c8c ok
  PIN v2.4.5 00305719ee16 ok
  PIN v2.2.1 3e94f2d89207 ok
  PIN v2.4.5 00305719ee16 ok
  PIN upload-versao: no SHA pin (v1)
  ```

  That is `PIN v2.2.1 3e94f2d89207 ok` four times (`cobre`, `decomp-pem`,
  `newave-pem`, `upload-newave`), the cobre-bridge pin
  `PIN v0.17.0 1b059ac505a8 ok` once, and the unchanged `v3.0.1`, `v2.4.5`,
  `v1.0.1` and Upload Versão lines.
- No `IDENTIFIER-GAP`, `NEW` or `MISSING` line.
- The summary line:

  ```text
  summary: 20 unchanged, 6 changed, 0 new, 0 failures
  ```

Any other result: stop before `--apply`. Nothing was written. The environment
file is wrong, the checkout is not `<repin-commit>`, or a document drifted live
since the Upload Versão cobre apply.

## Apply

1. **Apply.** Re-check the active executions (`active_executions`,
   Preconditions item 1). Then run `sync --apply`, read the list of writes, and
   type `apply`:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> --apply \
     | tee "$EV/repin/sync-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect 6 `WRITTEN PUT` lines, the two Tasks first, then the four workflows,
   each in slug order, and no `WRITTEN POST` or `CREATED`:

   ```text
   WRITTEN PUT task cobre-run <task-id>
   WRITTEN PUT task ensure-utils <task-id>
   WRITTEN PUT workflow cobre <workflow-id>
   WRITTEN PUT workflow decomp-pem <workflow-id>
   WRITTEN PUT workflow newave-pem <workflow-id>
   WRITTEN PUT workflow upload-newave <workflow-id>
   ```

2. **Run the post-apply dry run.**

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> \
     | tee "$EV/repin/sync-post-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect every definition `UNCHANGED` (the six own `v1-` Tasks `PROTECTED`),
   the same eleven `PIN` lines, and:

   ```text
   summary: 26 unchanged, 0 changed, 0 new, 0 failures
   ```

3. **Check the UI.** The workflow `cobre` has 22 parameters, and the Task
   `ensure-utils` is named `Garante hpc-model-utils e cobre-bridge versionados`.

**If a step deviates:**

- **A guard refuses the apply:** nothing was written. Read the one `apply:`
  line, fix the cause, and run the Preview again.
- **A workflow PUT returns 422** (that workflow is executing) after the Task
  PUTs landed. Wait for that execution to end, then re-run `sync --apply`:
  only the remaining `CHANGED` workflows are written. Record the affected
  execution. Only `cobre` uses the two changed Tasks, so a `cobre` execution
  that started between the Task PUTs and the `cobre` PUT ran the new scripts
  under the old definition and likely failed in `ensure-utils` with
  `invalid synthesisAppVersion` or in `cobre-run` with `invalid synthesisToolDir`:
  record it, and re-run it. A NEWAVE, DECOMP or Upload NEWAVE execution in
  that window ran `v2.0.2` unchanged.

## Smoke runs

Four runs, started from the UI on the re-pinned workflows, one after the
other. Each run collects its files the same way, with `<n>` the run number:

1. **Export the execution** (P4) after it reaches a terminal status:
   `D="$EV/repin/run<n>"; export_execution <execution-id> "$D"`. The stored
   `executionArtifactsPath` names `s3://<artifacts-bucket>/artifacts/<hash>/`.
2. **Read the artifacts** on the head node (P7), with the run's copy list
   below:

   ```bash
   A=s3://<artifacts-bucket>/artifacts/<hash>
   H=<head-scratch>/repin/run<n>
   mkdir -p "$H"
   s3_keys "$A/" saidas | tee "$H/saidas.txt"
   ```

3. **Copy** `$H/` to `$D/` on the workstation, as in P7, and run the checks
   from the repository root.

Record each run's ExecutionId, stored status and check results in the
evidence.

1. **`NEWAVE - PEM`** on a current PMO deck.

   ```bash
   # head node copy list
   aws s3 cp --recursive "$A/saidas/logs/" "$H/"
   # checks
   L="$D/execution-<execution-id>.log"
   jq -r .executionStatus "$D/execution-<execution-id>.json"
   grep -nF 'HPCMU_TOOL hpc-model-utils <tools-root>/hpc-model-utils/<utils-sha40>' "$L"
   grep -nxF 'saidas/logs/synthesis.out' "$D/saidas.txt"
   grep -F '[ExecutionOutput] Executa e acompanha modelo no SLURM: ' "$L" \
     > "$D/relayed-<execution-id>.txt"
   uv run python -m deploy.modelops.check_relay \
     --relayed "$D/relayed-<execution-id>.txt" \
     --job-log "$D/model-<model-id>.out" \
     --job-log "$D/finalize-<finalize-id>.out"
   ```

   **Pass criterion:** status `Success`; the ensure Task log shows
   `HPCMU_TOOL hpc-model-utils <tools-root>/hpc-model-utils/<utils-sha40>`;
   the artifacts hold `saidas/logs/synthesis.out`; `check_relay` prints
   `relay: PASS` (exit 0) on the model and finalize logs.

   **Evidence:** the ExecutionId, both job ids, the status, the `HPCMU_TOOL`
   line, the `synthesis.out` key line and the `check_relay` output verbatim.

2. **`DECOMP - PEM` chained on run 1**, with `parentPath` = run 1's artifacts
   URI.

   ```bash
   # head node copy list
   aws s3 cp --recursive "$A/saidas/logs/" "$H/"
   # checks
   L="$D/execution-<execution-id>.log"
   jq -r .executionStatus "$D/execution-<execution-id>.json"
   grep -nF 'FC cut coupling' "$L" | grep -F "source='parent'"
   grep -cF 'FC stage mismatch' "$L"
   grep -F '[ExecutionOutput] Executa e acompanha modelo no SLURM: ' "$L" \
     > "$D/relayed-<execution-id>.txt"
   uv run python -m deploy.modelops.check_relay \
     --relayed "$D/relayed-<execution-id>.txt" \
     --job-log "$D/model-<model-id>.out" \
     --job-log "$D/finalize-<finalize-id>.out"
   ```

   **Pass criterion:** status `Success`; `FC cut coupling … source='parent'`;
   no `FC stage mismatch` (the count prints `0`); `check_relay` prints
   `relay: PASS` (exit 0).

   **Evidence:** the ExecutionId, both job ids, the status, the coupling line,
   the count and the `check_relay` output verbatim.

3. **`Upload NEWAVE` offline**, per M10's procedure in the pre-rollout runbook
   (`inputFile`, `outputFile` and `cutFile` of an offline-executed NEWAVE run).

   ```bash
   # head node copy list
   aws s3 cp "$A/saidas/metadata.modelops" "$H/"
   # checks
   jq -r .executionStatus "$D/execution-<execution-id>.json"
   grep -o 'execution_source[^,}]*' "$D/execution-<execution-id>.json"
   jq -r .execution_source "$D/metadata.modelops"
   ```

   **Pass criterion:** status `Success`; `execution_source=OFFLINE` in the
   stored metadata and in `saidas/metadata.modelops` (the last two commands
   name `OFFLINE`).

   **Evidence:** the ExecutionId, the status, the key listing and
   `metadata.modelops` verbatim.

4. **`cobre` on one node** with the V1 deck and inputs of
   `docs/runbooks/cobre-rollout.md` (`inputFile` = `<inputs-uri>/cobre-4ree.zip`,
   `coreCount` 100, `maxCoresPerNode` 100, the queue default,
   `jobTimeoutHours` 1). This run is the first prd proof of the cobre
   dashboard: the validation rows V1 to V3 ran on `v2.1.0`, which has none.

   ```bash
   # head node copy list
   s3_keys "$A/" sintese | tee "$H/sintese.txt"
   aws s3 cp "$A/saidas/run.json" "$H/"
   aws s3 cp "$A/sintese/dashboard.html" "$H/"
   test -s "$H/dashboard.html" && echo 'dashboard: non-empty'
   # checks
   L="$D/execution-<execution-id>.log"
   jq -r .executionStatus "$D/execution-<execution-id>.json"
   grep -nF 'HPCMU_TOOL cobre-bridge <tools-root>/cobre-bridge/1b059ac505a8d57b6549c0a60411c41a9d7167cb' "$L"
   grep -nxF 'sintese/dashboard.html' "$D/sintese.txt"
   grep -nxF 'saidas/logs/synthesis.out' "$D/saidas.txt"
   jq -r '.diagnosis.reason' "$D/run.json"
   jq -e '(.diagnosis.reason // "") | startswith("synthesis failed:") | not' "$D/run.json"
   ```

   **Pass criterion:** status `Success`; the `ensure-utils` output has
   `HPCMU_TOOL cobre-bridge <tools-root>/cobre-bridge/1b059ac505a8d57b6549c0a60411c41a9d7167cb`;
   the artifacts hold a non-empty `sintese/dashboard.html`
   (`dashboard: non-empty`) and `saidas/logs/synthesis.out`; the `run.json`
   `diagnosis.reason` does not start with `synthesis failed:` (`jq -e` prints
   `true`).

   **Evidence:** the ExecutionId, the status, the `HPCMU_TOOL` line, both key
   lines, the dashboard's size and the `diagnosis.reason` verbatim.

If a run does not meet its pass criterion, check the rollback triggers below.

## Rollback triggers

Roll back when any one of these holds:

1. a smoke run fails its pass criterion for a reason the release introduced;
2. a chained DECOMP run ends with the stage-mismatch `DATA_ERROR`
   (`FC stage mismatch: NEWCUT …`) on a deck the operator confirms correct;
3. `check_relay` prints `relay: FAIL` for a smoke run.

Record the trigger with its evidence, then follow the procedure below. A
failure the release did not introduce (a deck error, a capacity hold) is
recorded and the run repeated; it is not a trigger.

## Rollback

Rollback means re-applying `<last-applied-commit>` with the same
`<env-file>`. It puts the four workflows and the two cobre Tasks back to their
pre-apply content; nothing is created or deleted.

1. **Switch the checkout:**

   ```bash
   git switch --detach <last-applied-commit>
   ```

2. **Run the dry run** with the same environment file:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> \
     | tee "$EV/repin/sync-rollback-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expected output:

   - `CHANGED` for the same two Tasks and four workflows as in the Preview,
     their diffs now restoring the previous pins and scripts;
   - `UNCHANGED … PROTECTED` six times, for the six own `v1-` Tasks;
   - the ten `PIN` lines of the Upload Versão cobre apply: `v2.1.0` for
     `cobre`, `v2.0.2` for the other three, and no cobre-bridge line:

     ```text
     PIN v2.1.0 3c97bb385ea8 ok
     PIN v2.0.2 13f5c0516496 ok
     PIN v3.0.1 b5f4bbaccc7f ok
     PIN v2.0.2 13f5c0516496 ok
     PIN v2.4.5 00305719ee16 ok
     PIN v1.0.1 f61dd5633c8c ok
     PIN v2.4.5 00305719ee16 ok
     PIN v2.0.2 13f5c0516496 ok
     PIN v2.4.5 00305719ee16 ok
     PIN upload-versao: no SHA pin (v1)
     ```

   - the summary line:

     ```text
     summary: 20 unchanged, 6 changed, 0 new, 0 failures
     ```

   Any other result: stop and report it. Nothing was written.
3. **Apply.** Re-check the active executions, run `sync --apply` with the same
   environment file and type `apply`. Expect the same 6 `WRITTEN PUT` lines as
   in the Apply: two `WRITTEN PUT task` lines, then four
   `WRITTEN PUT workflow` lines. A workflow PUT that returns 422 means that
   workflow is executing: wait for it to end and re-run.
4. **Run the dry run again** with the same environment file. Every definition
   is `UNCHANGED`:

   ```text
   summary: 26 unchanged, 0 changed, 0 new, 0 failures
   ```

5. Record the trigger, the time and the affected executions in the evidence.

The installed `hpc-model-utils` `v2.2.1` and cobre-bridge directories under
`<tools-root>` stay. Each is immutable, keyed by its commit, and harmless
after a rollback: no definition references it any more. Do not remove them.
