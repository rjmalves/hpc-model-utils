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

## Upload Versão cobre option

This section is the ticket-074 apply (ADR-056, R89 as amended 2026-10-05, R105,
R110). It runs after the Cobre apply above has passed. When it ends, Upload
Versão offers `cobre` among its `modelName` options and clones the
`upload-versoes-cli` `v1.1.0` release, and the cobre-mpi `v0.17.0` binary sits
under `versoes/cobre/v0.17.0/`, where the cobre cluster runs fetch it.

What the apply changes: two values of the workflow `upload-versao`, and
nothing else.

- The `modelName` options become `newave`, `decomp` and `cobre`. The default
  stays `newave`.
- The hidden `uploadCliVersion` default becomes `v1.1.0`. It is a tag-only pin:
  the CLI has no SHA pin (`PIN upload-versao: no SHA pin (v1)`).
- The Tasks `v1-clone-upload-cli` and `v1-upload-version` are not edited. The
  pin is the workflow parameter.

What the upload run delivers: the cobre release asset, copied to S3 unchanged
and uploaded as-is. The CLI selects the extractor by the `.tar.gz` suffix, so
the archive keeps its release name. It holds `./cobre-mpi` and five other files
(`README.txt`, `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` and
`THIRD_PARTY_LICENSES.md`). The processor uploads `cobre-mpi` and skips the
other five. The plain `cobre` command-line asset is not delivered.

Security note (F12): ModelOps has no RBAC. Adding `cobre` widens which models
can be published, not who can publish. No compensating control is decided
here. This section records two sha256 values for the read-back comparison and
enforces no checksum check; adding one needs an explicit security decision.

Record evidence only in the `## Upload Versão cobre` section of the private
rollout-evidence record, never here. `$EV` and `active_executions` are the ones
of the sections above.

| Placeholder | Where its value comes from |
| --- | --- |
| `<upload-versao-cobre-commit>` | the commit that holds the Upload Versão change |
| `<cobre-workflow-commit>` | the commit recorded in `## Cobre apply`; its Upload Versão still has the previous two values |
| `<env-file>` | the private environment file of the Cobre apply, now holding the three ids that apply created |
| `<versoes-bucket>` | env `versionsBucket` |
| `<root-path>` | env `rootPath`, without its trailing slash |
| `<inputs-uri>` | the S3 location the operator picks `executablesFile` from |
| `<head-scratch>` | a scratch directory on the machine that fetches the asset |
| `<execution-id>` | a ModelOps ExecutionId |

### Preconditions

All of the following hold before the Preview. The first comes first on
purpose: nothing below may run without it.

1. **The Cobre apply has passed, and nothing is running.**
   - In the private rollout-evidence record, the `## Cobre apply` section reads
     `Status: PASS`.
   - `git ls-remote https://github.com/rjmalves/upload-versoes-cli.git refs/tags/v1.1.0`
     prints one line.
   - No Upload Versão execution is active. Run `active_executions` now, and
     again immediately before the Apply. If one is executing, wait for it to
     end.

   If any is missing, stop. The repository change may already be committed; the
   prd apply may not run.
2. **The committed Upload Versão tree.** Work from a clean checkout of
   `<upload-versao-cobre-commit>`: the apply refuses a dirty tree and stamps
   the write with `HEAD`. Record `<upload-versao-cobre-commit>` in the
   evidence now, with the date of items 1 and 2.
3. **Tunnel, token and environment file** as in items 3 and 4 of the
   Preconditions of the Cobre apply. `<env-file>` needs no new key.

### Preview

The Preview is a dry run. It is read-only.

```bash
EV=<evidence-dir>
mkdir -p "$EV/cobre/upload"
uv run python -m deploy.modelops.apply sync --env-file <env-file> \
  | tee "$EV/cobre/upload/sync-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
```

Expected output:

- `CHANGED` for `upload-versao` only, followed by its diff against live (`-` is
  the rendered definition, `+` is live). Read it: it shows the options pair
  `"decomp",` and `"cobre"` against `"decomp"`, and the default
  `"v1.1.0"` against `"v1.0.0"`, and nothing else.
- Every other line `UNCHANGED`, and the six own `v1-` Tasks `PROTECTED`.
- The same ten `PIN` lines as in the Preview of the Cobre apply, the last one
  being `PIN upload-versao: no SHA pin (v1)`.
- No `IDENTIFIER-GAP` line, and no `NEW`.
- The summary line:

  ```text
  summary: 25 unchanged, 1 changed, 0 new, 0 failures
  ```

Any other result: stop before `--apply`. Nothing was written.

### Apply

1. **Apply.** Re-check the active executions. Then run `sync --apply`, read the
   list of writes, and type `apply`:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> --apply \
     | tee "$EV/cobre/upload/sync-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect exactly one `WRITTEN PUT` line, and no `WRITTEN POST` or `CREATED`:

   ```text
   WRITTEN PUT workflow upload-versao <workflow-id>
   ```

2. **Run the post-apply dry run.**

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> \
     | tee "$EV/cobre/upload/sync-post-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect every definition `UNCHANGED` (the six own `v1-` Tasks `PROTECTED`),
   the same ten `PIN` lines, and:

   ```text
   summary: 26 unchanged, 0 changed, 0 new, 0 failures
   ```

3. **Check the UI.** The `modelName` list of Upload Versão offers `newave`,
   `decomp` and `cobre`, with `newave` selected.

**If a step deviates:**

- **A guard refuses the apply:** nothing was written. Read the one `apply:`
  line, fix the cause, and run the Preview again.
- **The workflow PUT returns 422:** an Upload Versão execution is active. Wait
  for it to end and run the Preview again.

### Fetch the cobre-mpi asset

On the head node or the workstation, in `<head-scratch>`.

1. **Download** `cobre-mpi-0.17.0-x86_64-unknown-linux-gnu.tar.gz` from
   `https://github.com/cobre-rs/cobre/releases/tag/v0.17.0`, confirming the
   name on that page. No other asset is needed.
2. **Check the archive** holds the binary. The command prints exactly
   `cobre-mpi`; `members.txt` is the member list, for the evidence:

   ```bash
   tar -tzf cobre-mpi-0.17.0-x86_64-unknown-linux-gnu.tar.gz \
     | tee members.txt | sed 's|^\./||' | grep -x cobre-mpi
   ```

3. **Record the two hashes**, the archive and the binary inside it:

   ```bash
   sha256sum cobre-mpi-0.17.0-x86_64-unknown-linux-gnu.tar.gz
   tar -xzOf cobre-mpi-0.17.0-x86_64-unknown-linux-gnu.tar.gz ./cobre-mpi | sha256sum
   ```

4. **Copy the archive,** unchanged and under its release name:

   ```bash
   aws s3 cp cobre-mpi-0.17.0-x86_64-unknown-linux-gnu.tar.gz \
     <inputs-uri>/cobre-mpi-0.17.0-x86_64-unknown-linux-gnu.tar.gz
   ```

Do not unpack and repack the archive, and do not rename it: the CLI selects
the extractor by the `.tar.gz` suffix.

### Upload run

Run Upload Versão with `modelName` `cobre`, `modelVersion` `v0.17.0` and
`executablesFile` `<inputs-uri>/cobre-mpi-0.17.0-x86_64-unknown-linux-gnu.tar.gz`.

```bash
L="$EV/cobre/upload/execution-<execution-id>.log"
jq -r .executionStatus "$EV/cobre/upload/execution-<execution-id>.json"
grep -nF 'Created temporary dir <root-path>/cobre_' "$L"
grep -nF 'Using processor for model: cobre' "$L"
grep -nF 'Will process: cobre-mpi -> cobre-mpi' "$L"
grep -nF 'Skipping file (no matching pattern): README.txt' "$L"
aws s3 ls "s3://<versoes-bucket>/versoes/cobre/v0.17.0/"
aws s3 cp "s3://<versoes-bucket>/versoes/cobre/v0.17.0/cobre-mpi" - | sha256sum
```

**Pass criterion:**

- status `Success`;
- the log has `Created temporary dir <root-path>/cobre_`,
  `Using processor for model: cobre`, `Will process: cobre-mpi -> cobre-mpi`
  and `Skipping file (no matching pattern): README.txt`;
- `aws s3 ls s3://<versoes-bucket>/versoes/cobre/v0.17.0/` lists exactly one
  object, `cobre-mpi`;
- `aws s3 cp s3://<versoes-bucket>/versoes/cobre/v0.17.0/cobre-mpi - | sha256sum`
  equals the recorded binary hash.

**If the run fails:**

- **`Unsupported model`, or `must point to a .zip file`:** the clone is not
  `v1.1.0`. Check the tag and the applied `uploadCliVersion` default, and
  record the failure.
- **`must point to a .zip or .tar.gz file`:** the `executablesFile` URI lost the
  `.tar.gz` suffix. Copy the archive again under its release name, and record
  the deviation.

If the run does not meet its pass criterion, run the Rollback below, record the
failure, and reopen ticket-074 with the evidence.

### Rollback

Rollback means re-applying `<cobre-workflow-commit>`. It reverts the two
values; it does not remove an uploaded binary.

1. **Switch the checkout:**

   ```bash
   git switch --detach <cobre-workflow-commit>
   ```

2. **Run the dry run** with the same `<env-file>`. Expect `CHANGED` for
   `upload-versao` only, and:

   ```text
   summary: 25 unchanged, 1 changed, 0 new, 0 failures
   ```

   Any other result: stop and report it. Nothing was written.
3. **Apply.** Run `sync --apply` with the same environment file and type
   `apply`. Expect one `WRITTEN PUT workflow upload-versao <workflow-id>` line.
4. **Run the dry run again.** Every definition is `UNCHANGED`:

   ```text
   summary: 26 unchanged, 0 changed, 0 new, 0 failures
   ```

5. Record the failure, the time and the affected executions in the evidence.
   Removing the `versoes/cobre/v0.17.0/` prefix is an operator choice, and the
   choice is recorded. List first, then delete:

   ```bash
   aws s3 rm --recursive --dryrun "s3://<versoes-bucket>/versoes/cobre/v0.17.0/"
   aws s3 rm --recursive "s3://<versoes-bucket>/versoes/cobre/v0.17.0/"
   ```
