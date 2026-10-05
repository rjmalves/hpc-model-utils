# Runbook: apply the cobre workflow definitions

This runbook is the ticket-073 apply (ADR-056, ADR-057, ADR-050, ADR-051,
ADR-054, R89 as amended 2026-10-05, R105, R131). It runs after the ticket-067c
sweep. When it ends, the ModelOps catalog holds the workflow `cobre` and its
two own Tasks, and the seven shared Tasks accept the model `cobre`.

What the apply changes:

- The seven model-agnostic shared Tasks `create-workdir`, `remove-workdir`,
  `fetch-executables`, `fetch-inputs`, `extract-sanitize`, `result-upload` and
  `cancel-run` widen two regular expressions in their scripts, `modelName` and
  `path`, from `newave|decomp` to `newave|decomp|cobre`. The behavior for
  NEWAVE and DECOMP does not change.
- Two Task documents are created: `ensure-utils`, which installs only
  `hpc-model-utils`, and `cobre-run`, which passes `--max-cores-per-node` and
  the cobre MPICH to the run command.
- The workflow `cobre` is created, pinned to the `hpc-model-utils` `v2.1.0`
  release. Its chain is `ensure-utils`, `create-workdir`, `fetch-executables`,
  `fetch-inputs`, `extract-sanitize`, `cobre-run`, `result-upload` and
  `remove-workdir`.
- No existing workflow changes, and nothing is deleted by the apply.

The repository is public, so this file uses placeholders only (R104, R127).
Every real value lives in the private environment files. Record evidence only
in the `## Cobre apply` section of the private rollout-evidence record, never
here. This runbook reuses P1 of `docs/runbooks/v2-pre-rollout.md` (the tunnel
and token) and the `active_executions` function of
`docs/runbooks/v2-switch.md`. `$EV` is the pre-rollout runbook's evidence
directory.

## Preconditions

| Placeholder | Where its value comes from |
| --- | --- |
| `<cobre-workflow-commit>` | the commit that holds the cobre definitions |
| `<pre-cobre-commit>` | the parent of the cobre commit: `git rev-parse HEAD^` on a clean checkout of it |
| `<env-file>` | the private environment file; it has `env.cobreMpichPath`, and no key yet for the Tasks `ensure-utils` and `cobre-run` or the workflow `cobre` |
| `<pre-cobre-env-file>` | its sibling, a byte copy of `<env-file>` taken before `cobreMpichPath` was added |
| `<utils-v2.1.0-sha12>` | the first 12 characters of the `utilsAppSha` default of the workflow `cobre` (the `hpc-model-utils` `v2.1.0` pin) |
| `<task-id>`, `<workflow-id>` | ids that ModelOps assigns |

All of the following hold before the Preview. The first comes first on
purpose: nothing below may run without it.

1. **The sweep has passed, and nothing is running.**
   - In the private rollout-evidence record, the `## Sweep` section reads
     `Status: PASS`, with the post-retirement
     `summary: 23 unchanged, 0 changed, 0 new, 0 failures` recorded.
   - No NEWAVE - PEM, DECOMP - PEM, Upload NEWAVE, Ranqueamento Prospectivo or
     Upload Versão execution is active. Run `active_executions` (the switch
     runbook defines it) now, and again immediately before the Apply. If any of
     them is executing, wait for it to end.

   If either is missing, stop. The repository change may already be committed;
   the prd apply may not run. The `hpc-model-utils` `v2.1.0` tag must exist
   too: the Preview proves it with its `PIN v2.1.0 … ok` line.
2. **The committed cobre tree.** Work from a clean checkout of
   `<cobre-workflow-commit>`: the apply refuses a dirty tree and stamps every
   write with `HEAD`. Record `<cobre-workflow-commit>` and `<pre-cobre-commit>`
   in the evidence now, because the rollback starts from the second.
3. **Tunnel and token** per P1 of the pre-rollout runbook, with the tunnel
   opened as `ssh -N -o ServerAliveInterval=30 -L …`, a token read without
   echo, and `MODELOPS_USER` exported.
4. **The environment files.** `<env-file>` has `env.cobreMpichPath`, and
   `<pre-cobre-env-file>` exists beside it. Do not add keys for the three new
   documents: they are `NEW` until the apply creates them.

## Preview

The Preview is a dry run with the environment file. It is read-only.

```bash
EV=<evidence-dir>
mkdir -p "$EV/cobre"
uv run python -m deploy.modelops.apply sync --env-file <env-file> \
  | tee "$EV/cobre/sync-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
```

Expected output:

- `CHANGED` for `create-workdir`, `remove-workdir`, `fetch-executables`,
  `fetch-inputs`, `extract-sanitize`, `result-upload` and `cancel-run`. Each
  `CHANGED` entry is followed by its diff against live: read them. Each shows
  two changed lines, the `modelName` check and the `path` check gaining
  `|cobre`, and nothing else.
- `NEW` for `ensure-utils`, `cobre-run` and `cobre`. The workflow is rendered
  with `<new:ensure-utils>` and `<new:cobre-run>` in place of the ids of the
  two Tasks that do not exist yet, and the dry run prints no diff for a `NEW`
  document.
- Every other line `UNCHANGED`, and the six own `v1-` Tasks `PROTECTED`:
  `v1-clone-simulprospec`, `v1-clone-evalprospec`, `v1-clone-ranking-utils`,
  `v1-ranking-fetch-inputs`, `v1-clone-upload-cli` and `v1-upload-version`.
- The ten `PIN` lines, in the order of the workflow slugs. `PIN v2.1.0
  <utils-v2.1.0-sha12> ok` comes first, the pin of the workflow `cobre`:

  ```text
  PIN v2.1.0 <utils-v2.1.0-sha12> ok
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

- No `IDENTIFIER-GAP` line.
- The summary line:

  ```text
  summary: 16 unchanged, 7 changed, 3 new, 0 failures
  ```

Any other result: stop before `--apply`. Nothing was written. The environment
file is wrong, the checkout is not the cobre commit, or a document drifted live
since the sweep.

## Apply

1. **Apply.** Re-check the active executions (`active_executions`,
   Preconditions item 1). Then run `sync --apply`, read the list of writes, and
   type `apply`:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> --apply \
     | tee "$EV/cobre/sync-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect 7 `WRITTEN PUT` Task lines, then 3 `WRITTEN POST` lines (the two
   Tasks, then the workflow), then 3 `CREATED` lines, each with its
   `(add to env file: …)` hint. The Tasks are written in slug order, and the
   `CREATED` lines print after the last write, or when the apply aborts:

   ```text
   WRITTEN PUT task cancel-run <task-id>
   WRITTEN PUT task create-workdir <task-id>
   WRITTEN PUT task extract-sanitize <task-id>
   WRITTEN PUT task fetch-executables <task-id>
   WRITTEN PUT task fetch-inputs <task-id>
   WRITTEN PUT task remove-workdir <task-id>
   WRITTEN PUT task result-upload <task-id>
   WRITTEN POST task cobre-run
   WRITTEN POST task ensure-utils
   WRITTEN POST workflow cobre
   CREATED task cobre-run <task-id>  (add to env file: tasks.cobre-run)
   CREATED task ensure-utils <task-id>  (add to env file: tasks.ensure-utils)
   CREATED workflow cobre <workflow-id>  (add to env file: workflows.cobre)
   ```

2. **Record the three ids** from the `CREATED` lines in `<env-file>`, under
   `tasks.ensure-utils`, `tasks.cobre-run` and `workflows.cobre`.

3. **Run the post-apply dry run.**

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> \
     | tee "$EV/cobre/sync-post-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect every definition `UNCHANGED` (the six own `v1-` Tasks `PROTECTED`),
   the same ten `PIN` lines, and:

   ```text
   summary: 26 unchanged, 0 changed, 0 new, 0 failures
   ```

4. **Check the UI.** The workflow list has `cobre`, and its parameter list has
   the 19 parameters.

5. **Re-check the active executions** (`active_executions`). Record the first
   NEWAVE - PEM or DECOMP - PEM execution that starts after the apply
   (ExecutionId and stored status) in the evidence; it is not a gate.

**If a step deviates:**

- **A guard refuses the apply:** nothing was written. Read the one `apply:`
  line, fix the cause, and run the Preview again.
- **The apply aborts after a `CREATED` line:** record that id in `<env-file>`
  under its key, then run the Preview again. The created document shows
  `UNCHANGED`, and the documents not yet created are still `NEW`. Run
  `sync --apply` again for those.

## Rollback

Rollback means deleting the three new documents in the UI, then re-applying
the pre-cobre commit with its own environment file. Do not use `<env-file>`
for it.

1. **Delete in the UI, by id,** first the workflow `cobre`, then the Tasks
   `cobre-run` and `ensure-utils`. The ids are `workflows.cobre`,
   `tasks.cobre-run` and `tasks.ensure-utils` of `<env-file>`. The apply has no
   delete command, and its unmanaged-reference guard refuses to rewrite a
   shared Task while a live workflow outside the tree, the workflow `cobre`,
   still references it. Never delete by name.
2. **Switch the checkout** to the pre-cobre commit:

   ```bash
   git switch --detach <pre-cobre-commit>
   ```

3. **Run the dry run** with the pre-cobre environment file:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <pre-cobre-env-file> \
     | tee "$EV/cobre/sync-rollback-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expected output:

   - the same 7 `CHANGED` Tasks as in the Preview, their diffs now removing
     `|cobre`;
   - `UNCHANGED … PROTECTED` six times, for the six own `v1-` Tasks;
   - the nine `PIN` lines of the five existing workflows, without the
     `v2.1.0` line;
   - the summary line:

     ```text
     summary: 16 unchanged, 7 changed, 0 new, 0 failures
     ```

   Any other result: stop and report it. Nothing was written.
4. **Apply.** Run `sync --apply` with the same environment file and type
   `apply`. Expect the same 7 `WRITTEN PUT` Task lines.
5. **Run the dry run again** with the same environment file. Every definition
   is `UNCHANGED`:

   ```text
   summary: 23 unchanged, 0 changed, 0 new, 0 failures
   ```

6. Record the failure, the time and the affected executions in the evidence.
   Before any later apply, remove the three keys for `cobre-run`,
   `ensure-utils` and `cobre` from `<env-file>`: the documents they name are
   gone.

### Wrong-deletion recovery

A document deleted by mistake shows up in the dry run as `MISSING`, with a
failure in the summary. Recover it:

1. Remove its key from the environment file in use (`<env-file>` before the
   rollback, `<pre-cobre-env-file>` during it), under `tasks.<slug>` or
   `workflows.<slug>`, so the dry run shows `NEW` for it and `CHANGED` for
   every workflow that references it.
2. Run `sync --apply`. It creates the document again (`WRITTEN POST`) and
   writes the referencing workflows again. A workflow PUT that returns 422
   means that workflow is executing: wait for it to end and re-run.
3. Record the `CREATED` id under the same key. The dry run then reads
   `summary: 26 unchanged, 0 changed, 0 new, 0 failures` before the rollback,
   or `summary: 23 unchanged, 0 changed, 0 new, 0 failures` during it.
