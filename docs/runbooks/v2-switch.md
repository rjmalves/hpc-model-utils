# Runbook: switch the original workflows to the v2 definitions

This runbook is the ticket-067 switch (ADR-047, R133, R67, R68, R89 as amended
2026-10-05). It runs after the pre-rollout matrix is closed. When it ends, the
prd originals `NEWAVE - PEM`, `DECOMP - PEM` and `Upload NEWAVE` run `v2.0.2`
from the tools root, and no `(v2)` or `[v2]` document is left.

What the apply changes:

- The three originals take the content of their [v2] copies, pinned to
  `v2.0.2`. Workflow ids and the encadeador-set parameters (`inputFile`,
  `coreCount`, `modelVersion`, `parentPath`) stay. `awsKeyId` and
  `awsSecretKey` are gone (ADR-034).
- Seven Task documents that only the originals reference receive the v2 content
  in place, under their original names and ids: `cancel-run`,
  `extract-sanitize`, `fetch-inputs`, `ingest-offline`, `preprocess`,
  `result-upload` and `run`.
- `ensure-tools` loses its `(v2)` suffix. The v2 `create-workdir`,
  `remove-workdir` and `fetch-executables` Tasks take the suffix
  ` (NEWAVE/DECOMP)`: Ranqueamento Prospectivo and Upload Versão still use the
  v1 documents of the original names, so those names are taken only at
  ticket-067c.
- The five shared `v1-` documents, and the two originals-only documents that
  have no v2 counterpart (`Clona sintetizador-decomp`, `Lista arquivos no
  diretório da execução`), are not touched. The rollback needs the last two.

The repository is public, so this file uses placeholders only (R104, R127).
Every real value lives in the private environment files. Record evidence only
in the `## Switch` section of the private rollout-evidence record, never here.
This runbook reuses P1-P4 of `docs/runbooks/v2-pre-rollout.md` (the tunnel and
token, the dry run, starting an execution, exporting its stored log), run
against the originals instead of the [v2] copies. `$EV` is that runbook's
evidence directory.

## Preconditions

| Placeholder | Where its value comes from |
| --- | --- |
| `<pre-switch-commit>` | the parent of the switch commit: `git rev-parse HEAD^` on a clean checkout of the switch commit |
| `<env-file>` | the private post-switch environment file; its sibling `prd.pre-switch.json` holds the ids as they were before the switch |
| `<rollback-env-file>` | the private rollback environment file, also a sibling of `<env-file>` |
| `<tools-root>` | env `toolsRoot` |
| `<utils-sha40>` | the originals' `utilsAppSha` parameter (the `v2.0.2` release pin) |
| `<execution-id>` | a ModelOps ExecutionId |

All of the following hold before the Preview. The first is the IAM closure and
comes first on purpose: nothing below may run without it.

1. **IAM audit closed.** In the private IAM audit record, section 6, the
   "Closed by:" field is filled and no finding is left un-deactivated. If it
   is not, stop. The repository change may already be committed; the prd apply
   may not run.
2. **Matrix closed.** Every `Status:` line under the M1-M18 headings of the
   private rollout-evidence record reads `PASS` or `WAIVED`.
3. **The committed switch tree.** Work from a clean checkout of the switch
   commit: the apply refuses a dirty tree and stamps every write with `HEAD`.
   Record `<pre-switch-commit>` in the evidence now, because the rollback
   starts from it.
4. **Tunnel and token** per P1 of the pre-rollout runbook, with `MODELOPS_USER`
   exported.
5. **Encadeador notice.** `docs/notices/v2-status-semantics.md` is handed over
   as is (no delivery gate). Record the hand-over date and the recipient in the
   evidence header.
6. **No active execution** of the three originals, the three [v2] copies or
   Ranqueamento Prospectivo, by the check below. Run it now, and again
   immediately before step 4 of the Switch section. If any of them is
   executing, wait for it to end.

### The active-executions check

Define the function once per terminal, with the token set (P1):

```bash
active_executions() {
  uv run python - <<'PY'
import json
import os

from deploy.modelops.modelops_api import ModelOpsClient

doc = ModelOpsClient.from_env(os.environ).get_json(
    "/api/WorkflowExecution/active"
)
print(json.dumps(doc, ensure_ascii=False, indent=2))
PY
}
active_executions
```

Read the response for the workflow names `NEWAVE - PEM`, `DECOMP - PEM`,
`Upload NEWAVE`, their three `[v2]` copies and `Ranqueamento Prospectivo`. If
it names workflows only by id, match the ids against `workflows.*` of
`prd.pre-switch.json`. Do not keep the raw response: record only the UTC time
and that none of the eight was executing.

## Preview

The Preview is a dry run with the post-switch environment file. It is
read-only.

```bash
EV=<evidence-dir>
mkdir -p "$EV/switch"
uv run python -m deploy.modelops.apply sync --env-file <env-file> \
  | tee "$EV/switch/sync-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
```

Expected output:

- `CHANGED` for the seven in-place Tasks (`cancel-run`, `extract-sanitize`,
  `fetch-inputs`, `ingest-offline`, `preprocess`, `result-upload`, `run`),
  `ensure-tools`, `create-workdir`, `remove-workdir`, `fetch-executables` and
  the three originals (`decomp-pem`, `newave-pem`, `upload-newave`). Each
  `CHANGED` entry is followed by its diff against live: read them. They show
  the v1 content being replaced by the v2 content and the Task renames, and
  nothing else.
- `UNCHANGED task v1-… PROTECTED` five times: `v1-clone-hpc-model-utils`,
  `v1-clone-sintetizador-newave`, `v1-create-workdir`, `v1-fetch-executables`
  and `v1-remove-workdir`.
- The six `PIN` lines, with their commit prefixes:

  ```text
  PIN v2.0.2 13f5c0516496 ok
  PIN v3.0.1 b5f4bbaccc7f ok
  PIN v2.0.2 13f5c0516496 ok
  PIN v2.4.5 00305719ee16 ok
  PIN v2.0.2 13f5c0516496 ok
  PIN v2.4.5 00305719ee16 ok
  ```

  That is `PIN v2.0.2 13f5c0516496 ok` three times, `PIN v2.4.5 00305719ee16 ok`
  twice and `PIN v3.0.1 b5f4bbaccc7f ok` once.
- No `IDENTIFIER-GAP` line and no `NEW` line.
- The summary line:

  ```text
  summary: 5 unchanged, 14 changed, 0 new, 0 failures
  ```

Any other result: stop. Nothing was written. The environment file is wrong, or
the checkout is not the switch commit.

## Switch

**Why the UI deletions come before the apply.** The apply refuses a Task write
while a live workflow outside the managed set references that Task. This commit
deletes the `-v2` workflow files, so the three [v2] workflows become unmanaged.
They reference `ensure-tools`, `create-workdir`, `remove-workdir` and
`fetch-executables`, four Tasks this switch renames, so `--apply` would refuse
with `task ensure-tools is referenced by unmanaged workflow …`. Deleting the
[v2] workflows first makes that condition false. Do not bypass the guard. The
seven `(v2)` Tasks go in the same session, so the rollback environment file has
a single shape.

1. **Delete the three [v2] workflows in the UI**, by the ids in
   `prd.pre-switch.json`, keys `workflows.newave-pem-v2`,
   `workflows.decomp-pem-v2` and `workflows.upload-newave-v2`. Before each
   delete, check that the displayed name ends in ` [v2]`. Record each name and
   the UTC time.

   ```bash
   jq -r '.workflows | to_entries[] | select(.key | endswith("-v2")) | "\(.key) \(.value)"' \
     "$(dirname <env-file>)/prd.pre-switch.json"
   ```

2. **Delete the seven `(v2)` Task documents in the UI**, by the ids in
   `prd.pre-switch.json`, keys `tasks.cancel-run`, `tasks.extract-sanitize`,
   `tasks.fetch-inputs`, `tasks.ingest-offline`, `tasks.preprocess`,
   `tasks.result-upload` and `tasks.run`. Before each delete, check that the
   displayed name ends in ` (v2)`. Their v1 namesakes have the same name
   without the suffix and are the documents the apply overwrites in place:
   **never delete those.** Record each name and the UTC time.

   ```bash
   jq -r '.tasks | to_entries[] | select(.key | IN("cancel-run","extract-sanitize","fetch-inputs","ingest-offline","preprocess","result-upload","run")) | "\(.key) \(.value)"' \
     "$(dirname <env-file>)/prd.pre-switch.json"
   ```

   | Key | Displayed name before the delete |
   | --- | --- |
   | `cancel-run` | `Cancela job submetido na fila do SLURM (v2)` |
   | `extract-sanitize` | `Extrai e trata encoding dos dados de entrada do modelo (v2)` |
   | `fetch-inputs` | `Obtem dados de entrada do S3 (v2)` |
   | `ingest-offline` | `Obtem dados de rodada para upload do S3 (v2)` |
   | `preprocess` | `Preprocessamento especifico do modelo (v2)` |
   | `result-upload` | `Upload das saidas do modelo para o S3 (v2)` |
   | `run` | `Executa e acompanha modelo no SLURM (v2)` |

3. **Run the dry run again.** The output is identical to the Preview.

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> \
     | tee "$EV/switch/sync-pre-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   diff "$EV/switch/sync-preview-<stamp>.txt" "$EV/switch/sync-pre-apply-<stamp>.txt"; echo "diff exit $?"
   ```

   Expect `diff exit 0` and the same summary line. A `NEW` or `MISSING` line, or
   any difference: stop. Nothing was written. A deletion is incomplete or the
   environment file is wrong.

4. **Apply.** Re-check the active executions (`active_executions`,
   Preconditions item 6). Then run `sync --apply`, read the list of writes, and type `apply`:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> --apply \
     | tee "$EV/switch/sync-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect 14 `WRITTEN PUT` lines: 11 Tasks, then 3 workflows.

   ```text
   WRITTEN PUT task cancel-run <task-id>
   WRITTEN PUT task create-workdir <task-id>
   WRITTEN PUT task ensure-tools <task-id>
   WRITTEN PUT task extract-sanitize <task-id>
   WRITTEN PUT task fetch-executables <task-id>
   WRITTEN PUT task fetch-inputs <task-id>
   WRITTEN PUT task ingest-offline <task-id>
   WRITTEN PUT task preprocess <task-id>
   WRITTEN PUT task remove-workdir <task-id>
   WRITTEN PUT task result-upload <task-id>
   WRITTEN PUT task run <task-id>
   WRITTEN PUT workflow decomp-pem <workflow-id>
   WRITTEN PUT workflow newave-pem <workflow-id>
   WRITTEN PUT workflow upload-newave <workflow-id>
   ```

5. **Run the post-apply dry run.**

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <env-file> \
     | tee "$EV/switch/sync-post-apply-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expect every definition `UNCHANGED` (the 5 protected `v1-` Tasks included),
   the same six `PIN` lines, and:

   ```text
   summary: 19 unchanged, 0 changed, 0 new, 0 failures
   ```

6. **Re-check the active executions** (`active_executions`). Any execution of
   an original that started during steps 4-5 may have run v2 Task content under
   a v1 definition. Record it, and re-run it.

**If a step deviates:**

- **A guard refuses the apply** (`referenced by unmanaged workflow`), or a
  `NEW` or `MISSING` line appears: stop. The UI deletions are incomplete or the
  environment file is wrong, and nothing was written.
- **A workflow PUT returns 422** (that workflow is executing) after the Task
  PUTs landed. The originals now reference v2 Task content with v1 definitions.
  Wait for that execution to end, then re-run `sync --apply`: only the
  remaining `CHANGED` workflows are written. Record the affected execution. It
  likely failed, because `invalid utilsToolDir` is the expected failure of a v2
  script under a v1 workflow.
- **A `(v2)` Task cannot be deleted in the UI:** restore its `tasks.<slug>` key,
  with the id from `prd.pre-switch.json`, in `<rollback-env-file>`. The
  rollback then shows it `UNCHANGED` instead of `NEW`. The switch itself is
  unaffected.

## Post-switch smoke

Four runs, using P3 and P4 of the pre-rollout runbook on the originals. Export
each run (P4) after it reaches a terminal status, under `$EV/switch/run<n>/`,
and record its ExecutionId and stored status in the evidence.

1. **`NEWAVE - PEM`** on a known-good deck.

   ```bash
   L="$EV/switch/run1/execution-<execution-id>.log"
   jq -r .executionStatus "$EV/switch/run1/execution-<execution-id>.json"
   grep -nF 'HPCMU_TOOL hpc-model-utils <tools-root>/hpc-model-utils/<utils-sha40>' "$L"
   ```

   **Pass criterion:** status `Success`; the ensure Task log shows
   `HPCMU_TOOL hpc-model-utils <tools-root>/hpc-model-utils/<utils-sha40>`.

2. **`DECOMP - PEM` chained on that run**, with `parentPath` = run 1's artifacts
   URI.

   ```bash
   L="$EV/switch/run2/execution-<execution-id>.log"
   jq -r .executionStatus "$EV/switch/run2/execution-<execution-id>.json"
   grep -nF 'FC cut coupling' "$L" | grep -F "source='parent'"
   grep -nF 'WARNING hpc_model_utils.models.decomp.cuts: FC stage mismatch: ' "$L"
   ```

   **Pass criterion:** status `Success`; `FC cut coupling … source='parent'`;
   any FC stage-mismatch warning line is recorded for ticket-079.

3. **`Upload NEWAVE` offline**, per M10's procedure in the pre-rollout runbook
   (`inputFile`, `outputFile` and `cutFile` of an offline-executed NEWAVE run;
   then `s3_keys` and `aws s3 cp …/saidas/metadata.modelops -` on the head
   node).

   **Pass criterion:** status `Success`; `execution_source=OFFLINE` in the
   stored metadata and in `saidas/metadata.modelops`.

4. **`Ranqueamento Prospectivo`**, unchanged, with run 1 as its parent.

   **Pass criterion:** status `Success`. This is the first prd proof that a v2
   parent satisfies Ranqueamento's input contract, which M11 waived. Keep the
   export and the artifacts listing: ticket-067c compares its own run against
   this one.

If a run does not meet its pass criterion, check the rollback triggers below.

## Rollback triggers

Roll back when any one of these holds:

1. an execution finalized `Success` after a fatal CLI error;
2. an execution left non-terminal beyond its job time limit plus margin;
3. an ensure-step failure on a cache hit;
4. encadeador is unable to process a v2 outcome (a case stuck, or a status
   mapping error);
5. Ranqueamento is unable to consume a v2 parent;
6. operator decision: a run v1 would have completed now ends `RuntimeError`
   for a reason outside the status notice.

Record the trigger with its evidence, then follow the procedure below.

## Rollback procedure

Rollback means re-applying the pre-switch commit with its dedicated environment
file. Do not use the post-switch `<env-file>` for it.

1. Switch the checkout to the pre-switch commit:

   ```bash
   git switch --detach <pre-switch-commit>
   ```

2. Run the dry run with the rollback environment file:

   ```bash
   uv run python -m deploy.modelops.apply sync --env-file <rollback-env-file> \
     | tee "$EV/switch/sync-rollback-preview-$(date -u +%Y%m%dT%H%M%SZ).txt"
   ```

   Expected output:

   - `CHANGED` for the seven `v1-` in-place Tasks (`v1-cancel-run`,
     `v1-extract-sanitize`, `v1-fetch-inputs`, `v1-ingest-offline`,
     `v1-preprocess`, `v1-result-upload`, `v1-run`), `ensure-tools`,
     `create-workdir`, `remove-workdir`, `fetch-executables` and the three
     originals;
   - `NEW` for the seven v2 slugs and the three `-v2` workflows;
   - `UNCHANGED … PROTECTED` seven times: the five shared `v1-` Tasks plus
     `v1-clone-sintetizador-decomp` and `v1-list-files`;
   - the `PIN` lines: `PIN <slug>: no SHA pin (v1)` three times (one per
     original), plus the six `ok` lines of the copies;
   - the summary line:

     ```text
     summary: 7 unchanged, 14 changed, 10 new, 0 failures
     ```

   Any other result: stop and report it. Nothing was written.

3. Run `sync --apply` with the same environment file and type `apply`. The
   `CREATED` lines name the recreated [v2] lane. Add each id under the key the
   line names (`tasks.<slug>` or `workflows.<slug>`) in `<rollback-env-file>`,
   which is from then on the live environment file for the pre-switch tree.
   A workflow PUT that returns 422 means that workflow is executing: wait for
   it to end and re-run.

4. Run the dry run again with the updated `<rollback-env-file>`:

   ```text
   summary: 31 unchanged, 0 changed, 0 new, 0 failures
   ```

5. Record the trigger, the time and the affected executions in the evidence.

**Why this works.** A plain re-apply of the pre-switch commit with the
pre-switch environment file would fail for three reasons:

- **Protection.** That file lists the seven overwritten v1 ids in
  `protectedTaskIds`, so restoring them is refused.
- **Deleted documents.** It maps the deleted [v2] workflows and `(v2)` Tasks to
  ids that no longer exist, so the dry run reports `MISSING` and fails.
- **Slug mapping.** The post-switch file maps the v2 slugs onto the v1 ids, so
  it cannot render the pre-switch tree.

The rollback file is the pre-switch file with three changes: the three [v2]
workflow ids and the seven `(v2)` Task ids are removed, so the apply recreates
them as `NEW`; the seven overwritten v1 ids are removed from
`protectedTaskIds`; everything else stays. On the pre-switch commit the `v1-`
baseline files map to the same prd ids the switch overwrote, so the apply PUTs
the v1 content back into them, puts the originals back to v1, renames
`ensure-tools` and the three distinctly named Tasks back to `(v2)`, and
re-POSTs the [v2] lane.

**The window rule.** This rollback is valid only until ticket-067c's apply.
After it, Ranqueamento Prospectivo and Upload Versão reference the renamed
Tasks, the unmanaged-reference guard refuses the rename-back, and the switch's
rollback window is closed by design.
