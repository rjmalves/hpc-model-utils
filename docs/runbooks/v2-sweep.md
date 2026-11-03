# Runbook: sweep the shared Tasks onto the v2 set

This runbook is the ticket-067c sweep (ADR-044, ADR-050, R89 as amended
2026-10-05, R102, R131). It runs after the ticket-067 switch. When it ends,
Ranqueamento Prospectivo and Upload Versão use the v2 `create-workdir` and
`remove-workdir` Tasks, Ranqueamento also uses `ensure-tools` and
`fetch-executables`, and the eight orphaned v1 documents are gone.

What the apply changes:

- The v2 `create-workdir`, `remove-workdir` and `fetch-executables` Tasks lose
  their ` (NEWAVE/DECOMP)` suffix and take the original names (`Cria diretorio
  temporario para execucao`, `Remove diretorio temporario da execucao`,
  `Obtem executaveis dos modelos do S3`).
- `v1-ranking-run`, the one own Task of Ranqueamento that changes, gains a
  prepended block: the ADR-050 quoted-heredoc capture and validation of
  `utilsToolDir` and `synthesisToolDir`, then the two exports
  `HPCMU_UTILS_APP` and `HPCMU_SYNTHESIS_APP`. Its three legacy lines stay
  byte for byte, with no `set -e`, so `result_upload` still runs after a failed
  `run`.
- Ranqueamento Prospectivo takes the chain `ensure-tools`, `create-workdir`,
  its three clone Tasks, `fetch-executables`, its fetch-inputs Task, its run
  Task and `remove-workdir`. The two clone nodes (`hpc-model-utils`,
  `sintetizador-newave`) go. It gains four hidden parameters (`utilsAppSha`,
  `synthesisAppSha`, `utilsToolDir`, `synthesisToolDir`) and the pins
  `hpc-model-utils` `v1.0.1`, `sintetizador-newave` `v2.4.5` and
  `ranqueamento-prospectivo-utils` `v1.1.0`. Its workflow id, its other
  parameters (including `awsKeyId` and `awsSecretKey`) and its own Tasks stay.
- Upload Versão repoints its create and remove nodes, main and cancel, to the
  v2 Tasks. Nothing else changes.
- Nothing is deleted by the apply. The eight orphaned v1 documents are deleted
  in the UI, after both validation runs pass.

The repository is public, so this file uses placeholders only (R104, R127).
Every real value lives in the private environment files. Record evidence only
in the `## Sweep` section of the private rollout-evidence record, never here.
This runbook reuses P1-P4 of `docs/runbooks/v2-pre-rollout.md` (the tunnel and
token, the dry run, starting an execution, exporting its stored log) and the
`active_executions` function of `docs/runbooks/v2-switch.md`. `$EV` is the
pre-rollout runbook's evidence directory.

## Preconditions

| Placeholder | Where its value comes from |
| --- | --- |
| `<pre-sweep-commit>` | the parent of the sweep commit: `git rev-parse HEAD^` on a clean checkout of the sweep commit |
| `<env-file>` | the private post-sweep environment file; its siblings `prd.pre-sweep.json` and `prd.pre-switch.json` hold the ids as they were before the sweep and before the switch |
| `<rollback-env-file>` | the private rollback environment file, also a sibling of `<env-file>` |
| `<tools-root>` | env `toolsRoot` |
| `<utils-v1-sha40>` | the Ranqueamento `utilsAppSha` parameter (the `hpc-model-utils` `v1.0.1` pin) |
| `<newave-synthesis-sha40>` | the Ranqueamento `synthesisAppSha` parameter (the `sintetizador-newave` `v2.4.5` pin) |
| `<root-path>` | env `rootPath`, without its trailing slash |
| `<sweep-check-version>` | a throwaway `modelVersion` for the Upload Versão check, for example the date |
| `<execution-id>` | a ModelOps ExecutionId |
| `<artifacts-uri>` | the artifacts URI of the sweep's Ranqueamento run: `s3://<outputs-bucket>/artifacts/<execution-hash>` |
| `<switch-run4-artifacts-uri>` | the same for the Switch evidence's smoke run 4 (Ranqueamento on a v2 parent) |
| `<head-scratch>` | a scratch directory on the head node |
| `<outputs-bucket>`, `<versions-bucket>` | env `outputsBucket`, `versionsBucket` |
| `<snapshot-dir>` | the private prd snapshot directory |

All of the following hold before the Preview. The first comes first on
purpose: nothing below may run without it.

1. **The switch's rollback window may close.** In the private rollout-evidence
   record, the `## Switch` section reads `Status: PASS` (its four smoke runs
   are recorded), and the operator has stated that the switch's rollback window
   may close. This apply closes it by design: Ranqueamento and Upload Versão
   then reference the renamed Tasks, and the unmanaged-reference guard would
   refuse the switch's rename-back. Record the statement and its date in the
   `## Sweep` section now. If either is missing, stop. The repository change
   may already be committed; the prd apply may not run.
2. **The committed sweep tree.** Work from a clean checkout of the sweep
   commit: the apply refuses a dirty tree and stamps every write with `HEAD`.
   Record `<pre-sweep-commit>` in the evidence now, because the rollback
   starts from it.
3. **Tunnel and token** per P1 of the pre-rollout runbook, with `MODELOPS_USER`
   exported.
4. **No active execution** of Ranqueamento Prospectivo, Upload Versão or the
   three originals. Run `active_executions` (the switch runbook defines it)
   now, and again immediately before the Sweep apply. If any of them is
   executing, wait. A workflow PUT is refused while that workflow executes, and
   between the Task PUTs and the workflow PUTs a Ranqueamento run would fail
   with `invalid utilsToolDir`.
5. **The ensure-tools preflight (recommended, needs network).** Ranqueamento's
   first run installs `hpc-model-utils` `v1.0.1` into the tools root. That
   release has a `uv.lock` whose own root entry says `1.0.0`, and the install
   with `uv sync --frozen` has to accept it. Prove it on a workstation first,
   in a scratch directory whose path has no `@`:

   ```bash
   S=<scratch-dir>
   git clone <repository-url> "$S/hmu-src"
   bash deploy/modelops/scripts/ensure-tools.sh --root "$S/tools" \
     --uv "$(command -v uv)" --python 3.12.13 \
     --tool hpc-model-utils "file://$S/hmu-src" v1.0.1 <utils-v1-sha40> hpc-model-utils
   ```

   Expect exit 0, one `HPCMU_TOOL hpc-model-utils <scratch-dir>/tools/hpc-model-utils/<utils-v1-sha40>`
   line on stdout, and an install log under `$S/tools/.logs/` that ends with
   `== installed …`. If it fails at `uv sync` or at the smoke run, stop before
   the Preview and report it: the options are a later `hpc-model-utils`
   `v1.0.x` pin whose lock installs, the `v2.0.2` pin for Ranqueamento too, or
   a `v1.0.x` patch release that refreshes `uv.lock`. Without the preflight,
   the first install in the Validation step is the proof, and a failure there
   follows the Rollback section.

## Preview

The Preview is a dry run with the post-sweep environment file. It is
read-only.

```bash
EV=<evidence-dir>
mkdir -p "$EV/sweep"
uv run python -m deploy.modelops.apply sync --env-file <env-file> \
  | tee "$EV/sweep/sync-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
```

Expected output:

- `CHANGED` for `create-workdir`, `remove-workdir`, `fetch-executables`,
  `v1-ranking-run`, `ranqueamento` and `upload-versao`. Each `CHANGED` entry is
  followed by its diff against live: read them. They show, and show nothing
  else:
  - the three renamed Tasks: the `taskName` line, ` (NEWAVE/DECOMP)` leaving;
  - `v1-ranking-run`: the prepended capture block and the two exports, above
    the three legacy lines;
  - `upload-versao`: three `taskId` values;
  - `ranqueamento`: the two removed clone nodes, the `ensure-tools` node, the
    three repointed `taskId` values, the four appended parameters, the
    `synthesisAppVersion` and `rankingAppVersion` defaults, and the canvas
    positions of the nodes.
- Every other line `UNCHANGED`, and the six own `v1-` Tasks `PROTECTED`:
  `v1-clone-simulprospec`, `v1-clone-evalprospec`, `v1-clone-ranking-utils`,
  `v1-ranking-fetch-inputs`, `v1-clone-upload-cli` and `v1-upload-version`.
- The nine `PIN` lines: the six `ok` lines of the originals, then
  `PIN v1.0.1 f61dd5633c8c ok`, `PIN v2.4.5 00305719ee16 ok` and
  `PIN upload-versao: no SHA pin (v1)`, in the order of the workflow slugs:

  ```text
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

- No `IDENTIFIER-GAP`, `NEW` or `MISSING` line.
- The summary line:

  ```text
  summary: 17 unchanged, 6 changed, 0 new, 0 failures
  ```

Any other result: stop before `--apply`. Nothing was written. The environment
file is wrong, the checkout is not the sweep commit, or a document drifted live
since the switch.

## Sweep

1. **Apply.** Re-check the active executions (`active_executions`,
   Preconditions item 4). Then run `sync --apply`, read the list of writes, and
   type `apply`:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> --apply \
     | tee "$EV/sweep/sync-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect 6 `WRITTEN PUT` lines: 4 Tasks, then 2 workflows.

   ```text
   WRITTEN PUT task create-workdir <task-id>
   WRITTEN PUT task fetch-executables <task-id>
   WRITTEN PUT task remove-workdir <task-id>
   WRITTEN PUT task v1-ranking-run <task-id>
   WRITTEN PUT workflow ranqueamento <workflow-id>
   WRITTEN PUT workflow upload-versao <workflow-id>
   ```

2. **Run the post-apply dry run.**

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> \
     | tee "$EV/sweep/sync-post-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect every definition `UNCHANGED` (the six own `v1-` Tasks `PROTECTED`),
   the same nine `PIN` lines, and:

   ```text
   summary: 23 unchanged, 0 changed, 0 new, 0 failures
   ```

3. **Re-check the active executions** (`active_executions`). Any execution of
   Ranqueamento Prospectivo or Upload Versão that started during steps 1-2 may
   have run the new Task content under the old definition. Record it, and
   re-run it.

**If a step deviates:**

- **A guard refuses the apply:** nothing was written. Read the one `apply:`
  line, fix the cause, and run the Preview again.
- **A workflow PUT returns 422** (that workflow is executing) after the Task
  PUTs landed. Wait for that execution to end, then re-run `sync --apply`: only
  the remaining `CHANGED` workflows are written. Record the affected
  execution.

## Validation

Two runs, using P3 and P4 of the pre-rollout runbook. Export each run (P4) after
it reaches a terminal status, under `$EV/sweep/run<n>/`, and record its
ExecutionId and stored status in the evidence.

1. **Ranqueamento Prospectivo**, with the same `inputFile` and `parentPath` as
   the Switch evidence's smoke Ranqueamento run (smoke run 4). It is the A side
   of an A/B comparison: same input, same parent, other tools.

   ```bash
   L="$EV/sweep/run1/execution-<execution-id>.log"
   jq -r .executionStatus "$EV/sweep/run1/execution-<execution-id>.json"
   grep -nF 'HPCMU_TOOL hpc-model-utils <tools-root>/hpc-model-utils/<utils-v1-sha40>' "$L"
   grep -nF 'HPCMU_TOOL sintetizador-newave <tools-root>/sintetizador-newave/<newave-synthesis-sha40>' "$L"
   ```

   On the head node, the first-install record of the `hpc-model-utils`
   `v1.0.1` install (the M1 procedure of the pre-rollout runbook):

   ```bash
   R=<tools-root>
   cat -- "$R/hpc-model-utils/<utils-v1-sha40>/.ready"
   tail -n 1 -- "$R"/.logs/hpc-model-utils-<utils-v1-sha40>-*.log
   ```

   Then the `evalsimul.zip` member-name sets of both runs, the Switch smoke run
   and this one, taken the same way:

   ```bash
   members() {  # $1 = artifacts URI, $2 = output file
     aws s3 cp "$1/saidas/evalsimul.zip" - > <head-scratch>/evalsimul.zip
     unzip -Z1 <head-scratch>/evalsimul.zip | sort > "$2"
   }
   members <switch-run4-artifacts-uri> <head-scratch>/members-switch
   members <artifacts-uri> <head-scratch>/members-sweep
   diff <head-scratch>/members-switch <head-scratch>/members-sweep; echo "diff exit $?"
   grep -c '^inferior/sintese/.' <head-scratch>/members-sweep
   grep -c '^superior/sintese/.' <head-scratch>/members-sweep
   ```

   **Pass criterion:**
   - status `Success`;
   - the ensure Task log has `HPCMU_TOOL hpc-model-utils <tools-root>/hpc-model-utils/<utils-v1-sha40>`
     and `HPCMU_TOOL sintetizador-newave <tools-root>/sintetizador-newave/<newave-synthesis-sha40>`;
   - the first-install record: `.ready` holds `python=3.12.13` and the install
     log ends `== installed …`;
   - the member-name set of `evalsimul.zip` equals the Switch smoke run's
     (`diff exit 0`);
   - both `inferior/sintese/` and `superior/sintese/` are non-empty (both counts
     above are greater than zero).

   If the status is `Success` but the member sets differ: record the
   difference and stop before the retirement. The operator decides whether it
   is the expected `sintetizador-newave` `v2.3.0` to `v2.4.5` change.

2. **Upload Versão**, with `modelName` `newave`, the throwaway `modelVersion`
   `<sweep-check-version>` and the executables zip of an existing version as
   `executablesFile`.

   ```bash
   L="$EV/sweep/run2/execution-<execution-id>.log"
   jq -r .executionStatus "$EV/sweep/run2/execution-<execution-id>.json"
   grep -nF 'Created temporary dir <root-path>/newave_' "$L"
   aws s3 ls "s3://<versions-bucket>/versoes/newave/<sweep-check-version>/"
   ```

   **Pass criterion:**
   - status `Success`;
   - the log has `Created temporary dir <root-path>/newave_`, written by the
     Task named `Cria diretorio temporario para execucao`;
   - the versions-bucket listing of that version's prefix is non-empty.

   Removing that prefix afterwards is an operator choice, and the choice is
   recorded. List first, then delete:

   ```bash
   aws s3 rm --recursive --dryrun "s3://<versions-bucket>/versoes/newave/<sweep-check-version>/"
   aws s3 rm --recursive "s3://<versions-bucket>/versoes/newave/<sweep-check-version>/"
   ```

If a run does not meet its pass criterion, do not retire. Run the Rollback
section, record the failure, and reopen ticket-067c with the evidence.

## Retire the orphaned v1 documents

Only after both validations pass. This is the point of no return: the Rollback
section needs the shared v1 documents.

Delete these 8 documents in the UI **by id**:

- from `prd.pre-sweep.json` (a sibling of `<env-file>`): `tasks.v1-create-workdir`,
  `tasks.v1-remove-workdir`, `tasks.v1-fetch-executables`,
  `tasks.v1-clone-hpc-model-utils` and `tasks.v1-clone-sintetizador-newave`;
- from `prd.pre-switch.json`: `tasks.v1-clone-sintetizador-decomp` and
  `tasks.v1-list-files`;
- from the snapshot: the Task named `Obtem rodada executada do S3`, which no
  workflow references.

```bash
D="$(dirname <env-file>)"
jq -r '.tasks | to_entries[] | select(.key | IN("v1-create-workdir","v1-remove-workdir","v1-fetch-executables","v1-clone-hpc-model-utils","v1-clone-sintetizador-newave")) | "\(.key) \(.value)"' "$D/prd.pre-sweep.json"
jq -r '.tasks | to_entries[] | select(.key | IN("v1-clone-sintetizador-decomp","v1-list-files")) | "\(.key) \(.value)"' "$D/prd.pre-switch.json"
jq -r '.[] | select(.taskName == "Obtem rodada executada do S3") | "snapshot \(._id)"' <snapshot-dir>/tasks.json
```

| Key | Displayed name before the delete |
| --- | --- |
| `v1-create-workdir` | `Cria diretorio temporario para execucao` |
| `v1-remove-workdir` | `Remove diretorio temporario da execucao` |
| `v1-fetch-executables` | `Obtem executaveis dos modelos do S3` |
| `v1-clone-hpc-model-utils` | `Clona hpc-model-utils` |
| `v1-clone-sintetizador-newave` | `Clona sintetizador-newave` |
| `v1-clone-sintetizador-decomp` | `Clona sintetizador-decomp` |
| `v1-list-files` | `Lista arquivos no diretório da execução` |
| (snapshot) | `Obtem rodada executada do S3` |

**Never delete by name.** Two documents now carry each of the first three
names: the v1 document and its renamed v2 successor, which is the live document
`<env-file>` maps to `create-workdir`, `remove-workdir` or `fetch-executables`.
So before each delete, confirm that the id matches the one printed above, and
that the document's script has **no** `HPCMU_EXECUTION_ID` line (the v2 scripts
all have one). Record each name, id and the UTC time in the private evidence.

Afterwards, run the dry run again. It reads:

```text
summary: 23 unchanged, 0 changed, 0 new, 0 failures
```

## Rollback

Valid only before the retirement step. Rollback means re-applying the
pre-sweep commit with its dedicated environment file. Do not use the
post-sweep `<env-file>` for it.

1. Switch the checkout to the pre-sweep commit:

   ```bash
   git switch --detach <pre-sweep-commit>
   ```

2. Run the dry run with the rollback environment file:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <rollback-env-file> \
     | tee "$EV/sweep/sync-rollback-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expected output:

   - `CHANGED` for the same six slugs: `create-workdir`, `remove-workdir`,
     `fetch-executables`, `v1-ranking-run`, `ranqueamento` and `upload-versao`;
   - `UNCHANGED … PROTECTED` eleven times: the five shared `v1-` Tasks and the
     six own ones;
   - the `PIN` lines: `PIN ranqueamento: no SHA pin (v1)` and
     `PIN upload-versao: no SHA pin (v1)`, plus the six `ok` lines of the
     originals;
   - the summary line:

     ```text
     summary: 22 unchanged, 6 changed, 0 new, 0 failures
     ```

   Any other result: stop and report it. Nothing was written.

3. Run `sync --apply` with the same environment file and type `apply`. Expect
   the same six `WRITTEN PUT` lines. A workflow PUT that returns 422 means that
   workflow is executing: wait for it to end and re-run.

4. Run the dry run again with the same environment file. Every definition is
   `UNCHANGED`:

   ```text
   summary: 28 unchanged, 0 changed, 0 new, 0 failures
   ```

5. Record the failure, the time and the affected executions in the evidence.

**Why this works.** On the pre-sweep commit `v1-ranking-run` is a protected
baseline file, and the apply refuses to write a protected Task. The rollback
environment file is the pre-sweep file without that one id in
`protectedTaskIds`, so the apply can put the legacy content back. The three
renamed Tasks, the two workflows and the ranking run Task are the whole of the
change, and the shared v1 documents still exist for Ranqueamento and Upload
Versão to reference again.

**After the retirement,** a sweep failure is fixed forward: the shared v1
documents are gone, so the rollback has nothing to point at.

### Wrong-deletion recovery

A v2 document deleted by mistake shows up in the dry run as `MISSING`, with a
failure in the summary. Recover it:

1. Remove its key from `<env-file>` (`tasks.<slug>`), so the dry run shows
   `NEW` for it and `CHANGED` for every workflow that references it:
   `create-workdir` and `remove-workdir` are referenced by all five workflows,
   `ensure-tools` and `fetch-executables` by four.
2. Run `sync --apply`. It creates the Task again (`WRITTEN POST`) and writes the
   referencing workflows again. If it refuses with `taskName already exists
   live`, the v1 namesake is still live, because the retirement is not
   finished. Do not record that document's id: finish the retirement by
   deleting the v1 document (it is on the list above), then run the apply
   again. A workflow PUT that returns 422 means that workflow is executing:
   wait for it to end and re-run.
3. Record the `CREATED` id under the same key in `<env-file>`. The dry run
   then reads `summary: 23 unchanged, 0 changed, 0 new, 0 failures`.
