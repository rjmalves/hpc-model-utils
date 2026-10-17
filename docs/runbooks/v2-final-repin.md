# Runbook: roll out the final re-pin to v2.3.1

This runbook is the ticket-062 apply, retargeted by ticket-060b (R71, R98,
ADR-033, ADR-066, ADR-097, ADR-098, ADR-099, ADR-100). When it ends,
`NEWAVE - PEM`, `DECOMP - PEM`, `Upload NEWAVE` and `cobre` run the
`hpc-model-utils` `v2.3.1` release with job telemetry on, and cobre runs the
novomodelo fork: the `novomodelo-mpi` binary and the novomodelo-bridge
dashboard. No by-hand validation precedes the apply: the smoke runs and the
first production run of each workflow are the check (ADR-097, ADR-098). The
mitigation is the telemetry off lever (ADR-066), with the pin rollback behind
it.

What the apply changes:

- The four workflows `newave-pem`, `decomp-pem`, `upload-newave` and `cobre`
  change their `utilsAppVersion`/`utilsAppSha` pair to the `v2.3.1` pin, from
  their live pair `<prev-utils-tag>`/`<prev-utils-sha40>`. This puts job
  telemetry (`v2.3.0`) and the novomodelo plugin (`v2.3.1`) into production,
  with every earlier fix, such as v2.2.3's support of cobre v0.18.0.
- The class (a) documents of the private live-edits record land with it: the
  `ensure-tools` and `ensure-utils` Tasks, field `script`. Both embed the
  shared `ensure-tools.sh`, whose `pyarrow.parquet` smoke import (ADR-091) git
  gained after the last apply and live never received. None of the record's
  rows is an `apply reverts` decision of the operator.
- The novomodelo chain (tickets 060c–060f, ADR-099, ADR-100):
  - `cobre` drops the `synthesisAppVersion` and `synthesisAppSha` parameters
    and the `v0.17.0` `modelVersion` option, and its `synthesisToolDir`
    captures the novomodelo-bridge directory from `ensure-utils`;
  - `ensure-utils` installs the novomodelo-bridge tag that the run's
    `modelVersion` names, and is renamed
    `Garante hpc-model-utils e novomodelo-bridge versionados`;
  - `cobre-run` passes that bridge as `--synthesis-bin`;
  - the shared `ensure-tools.sh` gains `--tool-tag`, so the `ensure-tools`
    Task changes too, while its `--tool` path stays as it is. Ranqueamento
    Prospectivo, which also runs `ensure-tools`, runs the new script from its
    next run.
- The `modelVersion` default is already live, folded by ticket-060a, and does
  not change.
- Adding a cobre version needs no change in this repository (ADR-100, R96): a
  novomodelo and a novomodelo-bridge tag of the same name, a `novomodelo-mpi`
  Upload Versão, and the option in the UI.
- The `ranqueamento` and `upload-versao` workflows do not change. Nothing is
  created or deleted.

The repository is public, so this file uses placeholders only (R104, R127).
Every real value lives in the private environment files. This runbook reuses
P1 (the tunnel), P4 (the `export_execution` function), P6 (MinJobAge 300 s,
no `sacct`) and P7 (the `s3_keys` function and the artifacts layout) of
`docs/runbooks/v2-pre-rollout.md`, the M14 `check_relay` steps of the same
runbook, and the `active_executions` function of `docs/runbooks/v2-switch.md`.
`$EV` is the pre-rollout runbook's evidence directory.

Evidence goes to `$EV/repin/`, never here:

- one directory per watched run, each holding its `readings.txt`: `run1` to
  `run4`, `prod-newave-pem`, `prod-decomp-pem`, `prod-upload-newave` and
  `prod-cobre`;
- per apply attempt, with `<TS>` its `TS`: `snapshot-pre-apply-<TS>/`,
  `sync-preview-<TS>.txt`, `live-guard-<TS>.txt`, `sync-apply-<TS>.txt`,
  `apply-report-diff-<TS>.txt` and, once it wrote, `sync-post-apply-<TS>.txt`
  and `snapshot-post-apply-<TS>/`;
- for the pin rollback, also `rollback-check-<TS>.txt`.

The hand-back copies `$EV/repin/` to `<private-plan-dir>/design/rollout/raw/`
for ticket-062's private record:

```bash
test ! -e <private-plan-dir>/design/rollout/raw && cp -r "$EV/repin" <private-plan-dir>/design/rollout/raw
```

## Preconditions

| Placeholder | Where its value comes from |
| --- | --- |
| `<repin-commit>` | the commit holding ticket-060 and ticket-060b, on top of the `v2.3.1` release (tickets 060a, 060c–060f, 061 and 057b) |
| `<fold-commit>` | ticket-060a's commit, which the agent-checked list below names |
| `<rollback-commit>` | `<fold-commit>`, or a commit on top of it that sets a value the rollback check names (`## Rollback`, level 2) |
| `<lever-commit>` | the commit that `## Telemetry off lever` step 1 makes |
| `<env-file>` | the private environment file of the Cobre apply, holding the three ids that apply created; this runbook adds no key |
| `<tools-root>` | env `toolsRoot` |
| `<utils-sha40>` | the full `utilsAppSha` default of the four workflows |
| `<prev-utils-tag>`, `<prev-utils-sha40>` | each workflow's live `utilsAppVersion` and `utilsAppSha` before the apply, from the private live-edits record's `## Live pins` |
| `<model-version>` | a `modelVersion` option of the committed `cobre.json`; the cobre smoke run uses the default |
| `<bridge-sha40>` | the commit that the novomodelo-bridge tag `<model-version>` peels to: `git ls-remote https://github.com/ons-ccee-epe/novomodelo-bridge.git 'refs/tags/<model-version>*'` prints it, on the `^{}` line of an annotated tag, or else on the tag line |
| `<bridge-sha12>` | the first 12 characters of `<bridge-sha40>`, which the Preview's `resolved` line prints |
| `<versions-bucket>` | env `versionsBucket` |
| `<keycloak-username>` | the operator's Keycloak username, exported as `MODELOPS_USER` |
| `<first-apply-TS>` | the `TS` of the first `--apply` that wrote |
| `<reference-snapshot>` | for the re-pin apply, the `Snapshot dir` of the private live-edits record's last fold round, under `<private-plan-dir>`; for each later apply, the `$EV/repin/snapshot-post-apply-<TS>` of the apply before it |
| `<baseline-execution-id>` | per watched Slurm run, the operator's last comparable execution of the same workflow before the apply (same model, a comparable deck and core count), or `none` |
| `<private-plan-dir>` | the gitignored directory of this rollout's plan in the operator's repository checkout |
| `<artifacts-bucket>`, `<hash>` | env `outputsBucket`, and the execution's ExecutionHash (P7) |
| `<inputs-uri>` | the S3 location the operator picks `inputFile` from |
| `<head-scratch>` | a private scratch directory of the operator on the head node |
| `<execution-id>` | a ModelOps ExecutionId |
| `<model-id>`, `<finalize-id>` | the Slurm job ids from the run Task's `Submitted batch job` lines: the model job first, then the finalize job |
| `<task-id>`, `<workflow-id>` | ids that ModelOps assigned |

**Agent-checked** before this runbook was committed, recorded in the evidence:

- `<fold-commit>` is `be45f652d0c88c8e0cd118e2278abf39feba3a5c`
  (`chore: fold the live prd UI edits into git before the re-pin (ticket-060a)`),
  the first commit after `b890a77` that touches
  `deploy/modelops/workflows/upload-newave.json`. It lies below the `v2.3.1`
  tag;
- the four workflows carry the `v2.3.1` pin, the commit that
  `git rev-parse 'v2.3.1^{commit}'` prints; `cobre.json` has no
  `synthesisAppVersion` or `synthesisAppSha`, and the dry run's bridge check
  resolves every `modelVersion` option with 0 failures;
- the `PIN` lines below pass `tests/deploy/test_runbook_pins.py`;
- the expected `CHANGED` list of `## Preview` is the documents of the private
  live-edits record with a class (a) or (c) row, or with an `apply reverts`
  Action, plus the documents that the chain changed after `<fold-commit>`;
- the rollback's `CHANGED` list of `## Rollback` is the documents that the
  chain changed after `<fold-commit>`;
- the live guard passes ticket-060b's fixture check (its AC 6), and the
  rollback check passes its own (AC 7).

**Operator-checked.** All of the following hold before the Preview. The first
comes first on purpose: nothing below may run without it.

1. **Every earlier rollout has passed, and nothing is running.**
   - The private live-edits record of ticket-060a,
     `<private-plan-dir>/design/rollout/live-edits.md`, reads `FOLD COMPLETE`.
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
   with `HEAD`. Record `<repin-commit>` and `<fold-commit>` in the evidence
   now. The rollback target is `<rollback-commit>` of `## Rollback`, which
   starts from `<fold-commit>`. Do not use the parent of `<repin-commit>` as
   the rollback target, because it already holds the novomodelo chain, nor
   the commit that the last apply stamped, because it lacks the live edits
   that ticket-060a folded.
3. **Tunnel and token** per P1 of the pre-rollout runbook, with the tunnel
   opened as `ssh -N -o ServerAliveInterval=30 -L …`, and
   `MODELOPS_USER=<keycloak-username>` exported. Since the Keycloak switch, a
   UI save records the token's username in `lastChangeBy`, and `apply.py`
   sends `MODELOPS_USER` there. The token is not read once for the session:
   it lives 300 s, so each API step takes a fresh one with the fresh-token
   step of `## Apply` step 1.
4. **The environment file.** `<env-file>` holds `tasks.ensure-utils`,
   `tasks.cobre-run` and `workflows.cobre`, recorded at the Cobre apply. It
   needs no new key.
5. **The versions bucket** (R95, ADR-099), before the Preview. The plugin no
   longer accepts `cobre-mpi`, so every offered cobre version needs a
   `novomodelo-mpi` upload. On the workstation, list the offered versions:

   ```bash
   jq -r '.parameters[] | select(.name == "modelVersion") | .options[]' deploy/modelops/workflows/cobre.json
   ```

   On the head node, for each version as `<model-version>`:

   ```bash
   aws s3 ls --recursive "s3://<versions-bucket>/versoes/cobre/<model-version>/" | /usr/bin/grep -c -F novomodelo-mpi || true
   ```

   Each prints at least `1`. A `0` stops: that version needs a
   `novomodelo-mpi` Upload Versão first. The operator uploads the build, or
   removes the option through a git change; the runbook does not choose.

## Preview

The Preview runs three read-only steps under one `TS`, then checks their
output. Nothing is written. The re-pin apply, the lever, its revert and the
pin rollback each run it, with their own `<reference-snapshot>` and their own
expected output.

### 1. Capture

Take a fresh token with the first two parts of `## Apply` step 1. Then, from
the repository root:

```bash
EV=<evidence-dir>
mkdir -p "$EV/repin"
TS=$(date -u +%Y%m%dT%H%M%SZ)
uv run python -m deploy.modelops.apply snapshot --out "$EV/repin/snapshot-pre-apply-$TS"
uv run python -m deploy.modelops.apply sync --env-file <env-file> \
  | tee "$EV/repin/sync-preview-$TS.txt"
```

A 401 leaves a short snapshot or preview file. The guard then prints no
`SAME`: take a fresh token, and run the Preview again with a new `TS`.

### 2. The live guard

From the repository root, with the same `EV` and `TS`. The guard reads the
environment file and three local paths, and makes no network call. It renders
the tree through `apply.plan` on the pre-apply snapshot, with `repos={}`, and
applies `redact` with `MODELOPS_TOKEN`, as the printed report does.

- It compares `<reference-snapshot>` with the pre-apply snapshot, document by
  document, over the keys that the apply compares. A parameter is named
  `parameters:<name>`, or `parameters (order)`.
- It compares the Preview with the plan of the pre-apply snapshot, every line
  except the `PIN` and `summary:` lines.
- It prints `SNAPSHOTS reference=<takenAt> pre-apply=<takenAt>` first, then
  its `DIFFERS` lines, then its `NOTE` lines, and last `SAME` (exit 0) or
  `DIFFERENT <n>` (exit 1).

```bash
uv run python - <env-file> <reference-snapshot> "$EV/repin/snapshot-pre-apply-$TS" \
  "$EV/repin/sync-preview-$TS.txt" <<'LIVE_GUARD' | tee "$EV/repin/live-guard-$TS.txt"
import json
import os
import re
import sys
from pathlib import Path

import deploy.modelops.apply as apply_module
from deploy.modelops.modelops_api import redact
from deploy.modelops.render import canonical, comparison_keys, load_env

env_file, ref_dir, pre_dir, preview = (Path(a) for a in sys.argv[1:5])
FILES = {"/api/Task/all": "tasks.json", "/api/Workflow/all": "workflows.json"}
AUDIT = {"_id", "createdby", "createddate", "lastchangeby", "lastchangedate", "schedulestatus"}
HEAD = re.compile(r"(?:UNCHANGED|CHANGED|NEW|MISSING) (task|workflow) (\S+)")


class Snapshot:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def get_json(self, path: str):
        return json.loads((self.directory / FILES[path]).read_text("utf-8"))


def moved(before, after, keys) -> list[str]:
    labels = []
    for key in sorted(keys):
        old, new = before.get(key), after.get(key)
        if canonical(before, [key]) == canonical(after, [key]):
            continue
        if key == "parameters" and isinstance(old, list) and isinstance(new, list):
            old_by = {str(p.get("name")): p for p in old}
            new_by = {str(p.get("name")): p for p in new}
            names = sorted(n for n in old_by.keys() | new_by.keys() if old_by.get(n) != new_by.get(n))
            labels += [f"parameters:{n}" for n in names] or ["parameters (order)"]
        else:
            labels.append(key)
    return labels


def blocks(lines: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    name = "(start)"
    for line in lines:
        if line.startswith(("PIN ", "summary: ")):
            continue
        if head := HEAD.fullmatch(line.removesuffix(" PROTECTED")):
            name = f"{head[1]} {head[2]}"
        elif line.startswith("IDENTIFIER-GAP "):
            name = "IDENTIFIER-GAP"
        found.setdefault(name, []).append(line)
    return found


token = os.environ.get("MODELOPS_TOKEN", "")
root = Path(apply_module.__file__).resolve().parent
report = apply_module.plan(root, load_env(env_file), Snapshot(pre_dir), repos={})
taken = [json.loads((d / "meta.json").read_text("utf-8"))["takenAt"] for d in (ref_dir, pre_dir)]
print(f"SNAPSHOTS reference={taken[0]} pre-apply={taken[1]}")
stops = [f"DIFFERS render {redact(problem, token)}" for problem in report.problems]
notes = []
for entry in report.entries:
    name, writes = f"{entry.kind} {entry.slug}", entry.status == "CHANGED"
    if entry.status not in ("CHANGED", "UNCHANGED"):
        stops.append(f"DIFFERS {name} status {entry.status}")
        continue
    live = report.live_tasks if entry.kind == "task" else report.live_workflows
    after = live[entry.doc_id]
    ref = json.loads((ref_dir / FILES[f"/api/{entry.kind.title()}/all"]).read_text("utf-8"))
    before = next((doc for doc in ref if doc.get("_id") == entry.doc_id), None)
    if before is None:
        (stops if writes else notes).append(
            f"{'DIFFERS' if writes else 'NOTE'} {name} missing in the reference snapshot")
        continue
    keys = comparison_keys(entry.rendered)
    for label in moved(before, after, keys):
        if writes:
            stops.append(f"DIFFERS {name} {label}")
        else:
            notes.append(f"NOTE {name} {label} now equal to git")
    for key in sorted((before.keys() | after.keys()) - keys):
        if before.get(key) == after.get(key):
            continue
        if writes and key.lower() not in AUDIT:
            stops.append(f"DIFFERS {name} {key} not compared, and the PUT would drop it")
        else:
            notes.append(f"NOTE {name} {key} not compared")
mine = blocks([redact(line, token) for line in report.lines])
seen = blocks(preview.read_text("utf-8").splitlines())
for name in sorted(mine.keys() | seen.keys()):
    if mine.get(name) != seen.get(name):
        stops.append(f"DIFFERS preview {name}")
for line in stops + notes:
    print(line)
print("SAME" if not stops else f"DIFFERENT {len(stops)}")
sys.exit(1 if stops else 0)
LIVE_GUARD
```

### 3. The guard's rules

- **`SAME`, as the last line:** go on to the expected output.
- **`DIFFERS <kind> <slug> <field>`, or
  `DIFFERS <kind> <slug> <key> not compared, and the PUT would drop it`:** a
  user edited a document that the apply writes, after the reference snapshot.
  Stop.
  - For the re-pin apply, that means another ticket-060a round, then a new
    `<repin-commit>`, then this Preview again.
  - For a later apply, fold the edit into that apply's commit by hand, from
    the Preview's diff, and run this Preview again.
- **`DIFFERS preview <kind> <slug>`:** live changed between the snapshot and
  the dry run. Run this Preview again, with a new `TS`.
- **`DIFFERS <kind> <slug> status <status>`,
  `DIFFERS <kind> <slug> missing in the reference snapshot`, or
  `DIFFERS render …`:** the environment file or the live ids drifted. Stop.
- **`NOTE …` lines** are recorded, and do not stop. An audit key changed
  (`NOTE <kind> <slug> <key> not compared`), or a document that the apply
  does not write now equals git (`NOTE <kind> <slug> <field> now equal to git`).
- **A guard that prints no last `SAME` line,** such as one that cannot read
  `<reference-snapshot>`, is a stop. Without the guard, only `apply.py`'s
  compare-and-swap would remain, and it covers only the window from the
  apply's own read to each PUT. So this runbook never applies without a
  `SAME`. For the re-pin apply, another ticket-060a round makes a fresh
  reference.

Four windows, four guards:

| Window | Guard |
| --- | --- |
| the reference snapshot to the pre-apply snapshot | the live guard's per-document comparison |
| the pre-apply snapshot to the Preview | the live guard's comparison of the Preview with the snapshot's plan |
| the Preview to the apply's own read | the report check at the confirmation prompt (`## Apply` step 2) |
| the apply's read to each PUT | `apply.py`'s compare-and-swap |

### 4. The expected output of the dry run

The dry run prints one `<STATUS> <kind> <slug>[ PROTECTED]` line per document,
Tasks first and then workflows, each in slug order. Under each `CHANGED` line
comes its diff against live, in canonical JSON (`--- rendered`, `+++ live`:
`-` is the rendered definition, `+` is live). Read them. Then come the `PIN`
lines and the `summary:` line. Expected, in this order:

1. `CHANGED` for exactly these documents, in the apply's order:

   ```text
   CHANGED task cobre-run
   CHANGED task ensure-tools
   CHANGED task ensure-utils
   CHANGED workflow cobre
   CHANGED workflow decomp-pem
   CHANGED workflow newave-pem
   CHANGED workflow upload-newave
   ```

   They are the documents with a class (a) or (c) row, or with an
   `apply reverts` Action, in the private live-edits record (`task ensure-tools`
   and `task ensure-utils`, both class (a)), and the documents that the re-pin
   chain changed in git after `<fold-commit>` (all seven).
2. The diff fields, per document, and nothing else. A Task's whole script is
   one `"script"` line, so its diff is named here, not quoted.
   - `task cobre-run`: the script. It checks that `synthesisToolDir` names a
     `novomodelo-bridge/<sha40>` directory, and passes the
     `novomodelo-bridge` executable as `--synthesis-bin`.
   - `task ensure-tools`: the script, which embeds the shared
     `ensure-tools.sh`: its class (a) `pyarrow.parquet` smoke import, and the
     chain's `--tool-tag` install.
   - `task ensure-utils`: the `taskName`, `description` and `observation`,
     which name novomodelo-bridge where live names cobre-bridge, and the
     script: the class (a) smoke import, and the novomodelo-bridge install by
     the run's `modelVersion` tag in place of the `synthesisAppVersion` and
     `synthesisAppSha` pin.
   - `workflow cobre`: the `utilsAppVersion` default `"v2.3.1"` against
     `"<prev-utils-tag>"`, and the `utilsAppSha` default `"<utils-sha40>"`
     against `"<prev-utils-sha40>"`; the `synthesisAppVersion` and
     `synthesisAppSha` parameters and the `"v0.17.0"` `modelVersion` option,
     on the `+` side only; the `description` and `regexPattern` of
     `synthesisToolDir`, which name novomodelo-bridge; and the `order` values
     of `versionsBucket` and `jobTimeoutHours`.
   - `workflow decomp-pem`, `workflow newave-pem` and `workflow upload-newave`:
     the utils pair only, `"v2.3.1"` against `"<prev-utils-tag>"` and
     `"<utils-sha40>"` against `"<prev-utils-sha40>"`.

   The private live-edits record has no `apply reverts` row, so no other
   field is expected.
3. `UNCHANGED` for every other document, with the six own `v1-` Tasks
   `PROTECTED`.
4. These `PIN` lines, in workflow slug order:

   ```text
   PIN v2.3.1 dbc06b971fdb ok
   PIN novomodelo-bridge <model-version> <bridge-sha12> resolved
   PIN v2.3.1 dbc06b971fdb ok
   PIN v3.0.1 b5f4bbaccc7f ok
   PIN v2.3.1 dbc06b971fdb ok
   PIN v2.4.6 3df54dff1d51 ok
   PIN v1.0.1 f61dd5633c8c ok
   PIN v2.4.6 3df54dff1d51 ok
   PIN v2.3.1 dbc06b971fdb ok
   PIN v2.4.6 3df54dff1d51 ok
   PIN upload-versao: no SHA pin (v1)
   ```

   The dry run prints the bridge line once per `modelVersion` option of the
   committed `cobre.json`, in option order, each `resolved` with that tag's
   `<bridge-sha12>`. Record each option's `<bridge-sha12>` in the evidence. A
   bridge line that reads `MISSING` stops: the operator creates the tag, or
   removes the option through a git change, before `--apply`.
5. No `IDENTIFIER-GAP`, `NEW` or `MISSING` line.
6. The summary line:

   ```text
   summary: 19 unchanged, 7 changed, 0 new, 0 failures
   ```

### 5. The stop rule

Any other result stops before `--apply`. Nothing was written. That includes a
`CHANGED` document or field outside the lists above: it is a late UI edit that
the apply would revert, and the guard names it. The operator has it folded
through another ticket-060a round. The revert is accepted only in the
operator's own words, recorded in the evidence.

## Apply

### 1. A fresh token, then the apply

With the Preview's `EV` and `TS`, re-check the active executions
(`active_executions`, Preconditions item 1). Then take a fresh token and
apply.

1. In the browser where the operator is logged in to the ModelOps UI, run
   this line in the developer console. The UI is the public Keycloak client
   `modelops-ui`, and keeps its token in the localStorage key
   `oidc.user:/auth/realms/ModelOps:modelops-ui`. The line copies the access
   token to the clipboard, and prints its seconds left.

```js
(u => { copy(u.access_token); console.log(u.expires_at - Math.floor(Date.now() / 1000), "s left"); })(JSON.parse(localStorage.getItem("oidc.user:/auth/realms/ModelOps:modelops-ui")))
```

2. In the terminal, read the token without echo: run this line alone, paste
   the token, and press Enter.

```bash
read -rs MODELOPS_TOKEN && export MODELOPS_TOKEN
```

   Then export the user and check the token:

```bash
export MODELOPS_USER=<keycloak-username>
python3 -I - <<'TOKEN_CHECK'
import base64
import json
import os
import time

parts = os.environ.get("MODELOPS_TOKEN", "").split(".")
if len(parts) != 3:
    raise SystemExit("TOKEN not a JWT: copy it again")
claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
exp, iat = int(claims["exp"]), int(claims["iat"])
left = exp - int(time.time())
user = claims.get("preferred_username")
match = "absent" if user is None else "matches" if user == os.environ.get("MODELOPS_USER") else "DIFFERS"
print(f"TOKEN iss={claims.get('iss')} azp={claims.get('azp')} lifetime_s={exp - iat} left_s={left}"
      f" expires_at={time.strftime('%H:%M:%SZ', time.gmtime(exp))} user={match}")
print("TOKEN STALE" if left < 120 else "TOKEN USER DIFFERS" if match == "DIFFERS" else "TOKEN OK")
TOKEN_CHECK
```

   It decodes the token's payload without verifying it: this is a freshness
   check, and the server verifies the token. It prints one `TOKEN …` line with
   `iss`, `azp`, the lifetime, the seconds left, `expires_at` (UTC) and
   `user=<matches|DIFFERS|absent>`, which compares the `preferred_username`
   claim with `MODELOPS_USER`. It never prints the token. Its output stays in
   the terminal and goes to no file, because `iss` holds the private API
   address. Go on only on `TOKEN OK`.
   - `TOKEN STALE` (under 120 s left) or `TOKEN not a JWT: copy it again`:
     copy the token again, and run the check again.
   - `TOKEN USER DIFFERS`: correct `MODELOPS_USER`, and run the check again.

   The 120 s cover the apply from this check to its last PUT: its two GETs and
   about ten read-only `git ls-remote` pin checks (tens of seconds), the
   report check at the prompt (at most 60 s), and the GET-and-PUT pairs
   (seconds). A token that expires before the first PUT writes nothing,
   because each PUT's compare-and-swap GET comes first. One that expires
   between PUTs leaves a partial apply.
3. Right after `TOKEN OK`, run:

```bash
uv run python -m deploy.modelops.apply sync --env-file <env-file> --apply \
  | tee "$EV/repin/sync-apply-$TS.txt"
```

It prints the same report as the Preview, then any refusal, then one
`PUT <kind> <slug> <id>` line per write, and asks
`type 'apply' to write the changes above: ` on the terminal. Through `tee`,
stdin stays the terminal, and while the prompt waits the tee file already
holds the report and the `PUT` lines.

### 2. The report check, while the prompt waits

At `type 'apply' to write the changes above:`, run this in a second terminal
with the same `EV` and `TS`:

```bash
{ diff <(sed '/^summary: /q' "$EV/repin/sync-apply-$TS.txt") "$EV/repin/sync-preview-$TS.txt" \
    && echo 'APPLY REPORT = PREVIEW'; } | tee "$EV/repin/apply-report-diff-$TS.txt"
```

Type `apply` only after it prints `APPLY REPORT = PREVIEW`. A difference is a
live change after the guard. On any other output, type anything else, which
writes nothing (`confirmation not given; nothing written`), and run the
Preview again with a new `TS`.

Type `apply` only while the token has time: within 60 s of the prompt, and
before the `expires_at` that `TOKEN_CHECK` printed. Past either one, type
anything else. Then take a fresh token, and run the Preview again with a new
`TS`.

### 3. The writes

Expect 7 `WRITTEN PUT` lines (k, the number of `CHANGED` lines of the
Preview): the Tasks first, then the workflows, each in slug order, and no
`WRITTEN POST` or `CREATED`. The prompt has no newline, so the first one
follows it on the same line of the tee file:

```text
WRITTEN PUT task cobre-run <task-id>
WRITTEN PUT task ensure-tools <task-id>
WRITTEN PUT task ensure-utils <task-id>
WRITTEN PUT workflow cobre <workflow-id>
WRITTEN PUT workflow decomp-pem <workflow-id>
WRITTEN PUT workflow newave-pem <workflow-id>
WRITTEN PUT workflow upload-newave <workflow-id>
```

Record this attempt's `TS` as `<first-apply-TS>` if it is the first `--apply`
that wrote.

### 4. The post-apply dry run and snapshot

With a fresh token once `expires_at` has passed:

```bash
uv run python -m deploy.modelops.apply sync --env-file <env-file> \
  | tee "$EV/repin/sync-post-apply-$TS.txt"
uv run python -m deploy.modelops.apply snapshot --out "$EV/repin/snapshot-post-apply-$TS"
```

Expect every definition `UNCHANGED` (the six own `v1-` Tasks `PROTECTED`),
the same `PIN` lines as the Preview, and:

```text
summary: 26 unchanged, 0 changed, 0 new, 0 failures
```

The snapshot is the `<reference-snapshot>` of the next apply.

### 5. Check the UI

The workflow `cobre` has the parameter count of the committed `cobre.json`
(20 at `<repin-commit>`, from
`jq '.parameters | length' deploy/modelops/workflows/cobre.json`), and the
Task `ensure-utils` is named
`Garante hpc-model-utils e novomodelo-bridge versionados`.

### 6. A 422 on a workflow PUT

The workflow is executing. Wait for that execution to end. Then run the
Preview again (a new `TS`, the snapshot, the dry run and the guard), with the
same `<reference-snapshot>`, and this Apply with its fresh token and report
check. The documents that the first attempt wrote are `UNCHANGED` now, and the
guard prints `NOTE … now equal to git` lines for them. These are expected. Any
`DIFFERS` line stops. The retry's dry run lists `CHANGED` only for the
documents not yet written, and its summary and its `WRITTEN PUT` lines count
those.

**If a step deviates:**

- **A guard refuses the apply:** nothing was written. Read the one `apply:`
  line, fix the cause, and run the Preview again.
- **A workflow PUT returns 422** after the Task PUTs landed: step 6. Record
  the affected execution. `cobre-run` and `ensure-utils` are used only by
  `cobre`, so a `cobre` execution that started between the Task PUTs and the
  `cobre` PUT ran the new scripts under the old definition, and likely failed
  in `cobre-run` with `invalid synthesisToolDir`: record it, and re-run it. A
  NEWAVE, DECOMP or Upload NEWAVE execution in that window ran the new
  `ensure-tools` script, whose `--tool` path is unchanged, with
  `<prev-utils-tag>`.
- **A 401 between PUTs** (the token expired): the apply is partial. No
  execution holds the workflow, so there is nothing to wait for: run the
  Preview again and this Apply with a fresh token, as step 6 describes after
  its wait.

## Smoke runs

Four runs, started from the UI on the re-pinned workflows, one after the
other. Each run collects its files the same way, with `<n>` the run number:

1. **Export the execution** (P4) after it reaches a terminal status, with a
   fresh token (the first two parts of `## Apply` step 1):
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

Every smoke run then takes the readings of `## Telemetry readings`: its
head-node parts before step 3's copy, and its workstation part after the
checks. On runs 1, 2 and 4, the job-end poll is required: start it as soon as
the run Task prints its two `Submitted batch job` lines.

Runs 1, 2 and 4 give the first ModelOps-path telemetry evidence: the three
`saidas/telemetry/` objects of `docs/telemetry.md` (`samples.parquet`,
`workspace.parquet` and `summary.json`) and the `telemetry.` execution
metadata. A run missing the telemetry files or the hooks, with every other
criterion met, is not a rollback trigger. It is recorded, and ticket-062 holds
the announcement.

Record each run's ExecutionId, stored status and check results in the
evidence.

1. **`NEWAVE - PEM`** on a current PMO deck.

   ```bash
   # head node copy list
   aws s3 cp --recursive "$A/saidas/logs/" "$H/"
   aws s3 cp "$A/saidas/run.json" "$H/"
   aws s3 cp --recursive "$A/saidas/telemetry/" "$H/telemetry/"
   # checks
   L="$D/execution-<execution-id>.log"
   jq -r .executionStatus "$D/execution-<execution-id>.json"
   grep -nF 'HPCMU_TOOL hpc-model-utils <tools-root>/hpc-model-utils/<utils-sha40>' "$L"
   grep -nxF 'saidas/logs/synthesis.out' "$D/saidas.txt"
   /usr/bin/grep -c -x -E 'saidas/telemetry/(samples\.parquet|workspace\.parquet|summary\.json)' "$D/saidas.txt"
   /usr/bin/grep -q -F '"telemetry.' "$D/execution-<execution-id>.json" && echo 'hooks: present'
   grep -F '[ExecutionOutput] Executa e acompanha modelo no SLURM: ' "$L" \
     > "$D/relayed-<execution-id>.txt"
   uv run python -m deploy.modelops.check_relay \
     --relayed "$D/relayed-<execution-id>.txt" \
     --job-log "$D/model-<model-id>.out" \
     --job-log "$D/finalize-<finalize-id>.out"
   ```

   **Pass criterion:** status `Success`; the ensure Task log shows
   `HPCMU_TOOL hpc-model-utils <tools-root>/hpc-model-utils/<utils-sha40>`;
   the artifacts hold `saidas/logs/synthesis.out`; the telemetry count prints
   `3`; `hooks: present`; `check_relay` prints `relay: PASS` (exit 0) on the
   model and finalize logs.

   **Evidence:** the ExecutionId, both job ids, the status, the `HPCMU_TOOL`
   line, the `synthesis.out` key line, the telemetry count, the hooks line
   and the `check_relay` output verbatim.

2. **`DECOMP - PEM` chained on run 1**, with `parentPath` = run 1's artifacts
   URI.

   ```bash
   # head node copy list
   aws s3 cp --recursive "$A/saidas/logs/" "$H/"
   aws s3 cp "$A/saidas/run.json" "$H/"
   aws s3 cp --recursive "$A/saidas/telemetry/" "$H/telemetry/"
   # checks
   L="$D/execution-<execution-id>.log"
   jq -r .executionStatus "$D/execution-<execution-id>.json"
   grep -nF 'FC cut coupling' "$L" | grep -F "source='parent'"
   grep -cF 'FC stage mismatch' "$L"
   /usr/bin/grep -c -x -E 'saidas/telemetry/(samples\.parquet|workspace\.parquet|summary\.json)' "$D/saidas.txt"
   /usr/bin/grep -q -F '"telemetry.' "$D/execution-<execution-id>.json" && echo 'hooks: present'
   grep -F '[ExecutionOutput] Executa e acompanha modelo no SLURM: ' "$L" \
     > "$D/relayed-<execution-id>.txt"
   uv run python -m deploy.modelops.check_relay \
     --relayed "$D/relayed-<execution-id>.txt" \
     --job-log "$D/model-<model-id>.out" \
     --job-log "$D/finalize-<finalize-id>.out"
   ```

   **Pass criterion:** status `Success`; `FC cut coupling … source='parent'`;
   no `FC stage mismatch` (the count prints `0`); the telemetry count prints
   `3`; `hooks: present`; `check_relay` prints `relay: PASS` (exit 0).

   **Evidence:** the ExecutionId, both job ids, the status, the coupling line,
   both counts, the hooks line and the `check_relay` output verbatim.

3. **`Upload NEWAVE` offline**, per M10's procedure in the pre-rollout runbook
   (`inputFile`, `outputFile` and `cutFile` of an offline-executed NEWAVE run).

   ```bash
   # head node copy list
   aws s3 cp "$A/saidas/metadata.modelops" "$H/"
   aws s3 cp "$A/saidas/run.json" "$H/"
   # checks
   jq -r .executionStatus "$D/execution-<execution-id>.json"
   grep -o 'execution_source[^,}]*' "$D/execution-<execution-id>.json"
   jq -r .execution_source "$D/metadata.modelops"
   /usr/bin/grep -c -x -E 'saidas/telemetry/(samples\.parquet|workspace\.parquet|summary\.json)' "$D/saidas.txt" || true
   ```

   **Pass criterion:** status `Success`; `execution_source=OFFLINE` in the
   stored metadata and in `saidas/metadata.modelops` (the two commands after
   the status name `OFFLINE`). This run starts no Slurm job, so the telemetry
   count is evidence only, with no criterion.

   **Evidence:** the ExecutionId, the status, the key listing, the telemetry
   count and `metadata.modelops` verbatim.

4. **`cobre` on one node** with the V1 deck and inputs of
   `docs/runbooks/cobre-rollout.md` (`inputFile` = `<inputs-uri>/cobre-4ree.zip`,
   `coreCount` 100, `maxCoresPerNode` 100, the queue default,
   `jobTimeoutHours` 1), and `modelVersion` = `<model-version>`, the committed
   default. This run is the first prd proof of the novomodelo fork: the
   `novomodelo-mpi` binary, the novomodelo-bridge tag that `<model-version>`
   names, and its dashboard.

   ```bash
   # head node copy list
   s3_keys "$A/" sintese | tee "$H/sintese.txt"
   aws s3 cp --recursive "$A/saidas/logs/" "$H/"
   aws s3 cp "$A/saidas/run.json" "$H/"
   aws s3 cp --recursive "$A/saidas/telemetry/" "$H/telemetry/"
   aws s3 cp "$A/sintese/dashboard.html" "$H/"
   test -s "$H/dashboard.html" && echo 'dashboard: non-empty'
   # head node check of the tag record
   /usr/bin/grep -x -F 'sha=<bridge-sha40>' '<tools-root>/.tags/novomodelo-bridge/<model-version>' && echo 'tag record: ok'
   # checks
   L="$D/execution-<execution-id>.log"
   jq -r .executionStatus "$D/execution-<execution-id>.json"
   grep -nF 'HPCMU_TOOL novomodelo-bridge <tools-root>/novomodelo-bridge/<bridge-sha40>' "$L"
   grep -nxF 'sintese/dashboard.html' "$D/sintese.txt"
   grep -nxF 'saidas/logs/synthesis.out' "$D/saidas.txt"
   /usr/bin/grep -c -x -E 'saidas/telemetry/(samples\.parquet|workspace\.parquet|summary\.json)' "$D/saidas.txt"
   /usr/bin/grep -q -F '"telemetry.' "$D/execution-<execution-id>.json" && echo 'hooks: present'
   jq -r '.diagnosis.reason' "$D/run.json"
   jq -e '(.diagnosis.reason // "") | startswith("synthesis failed:") | not' "$D/run.json"
   ```

   **Pass criterion:** status `Success`; the `ensure-utils` output has
   `HPCMU_TOOL novomodelo-bridge <tools-root>/novomodelo-bridge/<bridge-sha40>`;
   the head node prints `tag record: ok`; the artifacts hold a non-empty
   `sintese/dashboard.html` (`dashboard: non-empty`), the novomodelo-bridge
   dashboard, and `saidas/logs/synthesis.out`; the `run.json`
   `diagnosis.reason` does not start with `synthesis failed:` (`jq -e` prints
   `true`); the telemetry count prints `3`; `hooks: present`.

   **Evidence:** the ExecutionId, the status, the `HPCMU_TOOL` line, the tag
   record line, both key lines, the dashboard's size, the telemetry count, the
   hooks line and the `diagnosis.reason` verbatim.

   If the V1 deck fails under `<model-version>` with a deck error that the
   release did not introduce, record it: it is no rollback trigger. The
   operator names a deck for that version, and the run is repeated.

If a run does not meet its pass criterion, check the rollback triggers below.

## Telemetry readings

One procedure for every watched run, smoke or production. `<run>` is the
run's directory name (`run1` to `run4`, or `prod-<slug>`), with
`H=<head-scratch>/repin/<run>` on the head node and `D="$EV/repin/<run>"` on
the workstation. Every reading appends machine lines to one `readings.txt`:
the head-node parts to `"$H/readings.txt"`, which step 3 of the collection
copies to `$D/`, and the workstation part to `"$D/readings.txt"`. Each line
starts with its prefix: `K1`, `ARTIFACTS`, `UPLOAD`, `WALL`, `COMPLETING`,
`NODE`, `AFFINITY` or `NOTE`. A missing input prints `NA` for its fields,
never 0, and a failed heredoc appends a `NOTE` line.

| Reading | Machine line | Threshold | Consequence |
| --- | --- | --- | --- |
| K1 | `K1` | telemetry changes no status, exit code, relayed log or diagnosis | trigger 4, level 1 |
| Budgets | `NODE`, reasons `cost` and `rss` | per node p99 `sample_cost_ms` ≤ 0.5 ms × `interval_s`; max `sampler_maxrss_bytes` < 30 000 000 | trigger 5, level 1 |
| Job end | `COMPLETING` | each `COMPLETING` ≤ 30 s | trigger 6, level 1 |
| Artifacts | `ARTIFACTS` | 3 `saidas/telemetry/` objects and at least 1 `telemetry.` key on a Slurm run | hold the announcement |
| Null fractions | `NODE`, reasons `cpu_util_null` and `cpu_iowait_null` | per node `cpu_util` null ≤ 1 %, `cpu_iowait_cores` null ≤ 10 % | flag ADR-093 |
| Export | `UPLOAD` | `duration_seconds.result_upload` growth over the baseline ≤ 150 s | flag ADR-073 |
| Page cache | `NODE`, field `mem=` | no `mem=PAGE-CACHE` node | flag ADR-077 |
| Model wall time | `WALL` | at most max(3 %, 60 s) above the baseline run | flag A5-wall |
| Affinity | `AFFINITY` | the sampler's `Cpus_allowed_list` is not a single CPU | flag ADR-062 |

The workflows end with the `remove-workdir` Task, so the workspace and its
telemetry staging are gone when a run ends. Every reading comes from the
stored execution, the artifacts, the job logs or a live read on the head
node. Every job log starts with `HPCMU_START job=<id> restart=<n> at=<UTC>`.

### While the run is live (head node, Slurm runs only)

**The job-end poll.** In a second head-node shell, start it as soon as the run
Task prints its two `Submitted batch job` lines. Set `MID` and `FID` by hand
from those lines: the model job first, then the finalize job. The poll reads
`squeue -h -j <id> -o %T` every 1 s for both jobs, and writes
`"$H/squeue.txt"`. It stops when neither job is live: each one is in a
terminal state or absent. MinJobAge (300 s, P6) keeps a finished job listed.
`ERROR` is an `squeue` failure other than an unknown job id; it counts as
live, so the poll goes on.

```bash
# on the head node, shell 2
H=<head-scratch>/repin/<run>
mkdir -p "$H"
MID=<model-id> FID=<finalize-id>
job_state() {  # $1 = job id; prints its squeue state, ABSENT or ERROR
  local out
  out=$(squeue -h -j "$1" -o %T 2>&1)
  case $out in
    '' | *'Invalid job id'*) echo ABSENT ;;
    *[!A-Z_]*) echo ERROR ;;
    *) echo "$out" ;;
  esac
}
LIVE='^(PENDING|CONFIGURING|RUNNING|COMPLETING|REQUEUED|SUSPENDED|RESIZING|REQUEUE_HOLD|REQUEUE_FED|STOPPED|SIGNALING|ERROR)$'
while :; do
  now=$(date -u +%s) m=$(job_state "$MID") f=$(job_state "$FID")
  echo "$now $(date -u -d "@$now" +%H:%M:%SZ) model=$m finalize=$f"
  [[ $m =~ $LIVE || $f =~ $LIVE ]] || break
  sleep 1
done | tee "$H/squeue.txt"
```

**The affinity reading** (optional; ADR-062). In a third head-node shell,
once the model job has run for at least 60 s. It waits on the poll's
`"$H/squeue.txt"`, so it is taken only on a polled run. It runs the `AFFINITY_CHECK`
body on every node of the model job, through
`srun --overlap --jobid=<model-id> -w <node> -N1 -n1 bash -s`. For each
sampler process whose `/proc/<pid>/comm` starts with `python`, it appends
`AFFINITY node=<hostname -s> pid=<pid> comm=<comm> cpus_allowed_list=<Cpus_allowed_list> vmhwm_kb=<VmHWM>`
to `"$H/readings.txt"`.

```bash
# on the head node, shell 3
H=<head-scratch>/repin/<run>
MID=<model-id>
until R=$(awk '$3 == "model=RUNNING" { print $1; exit }' "$H/squeue.txt" 2>/dev/null) && [[ -n $R ]]; do sleep 1; done
while (($(date -u +%s) < R + 60)); do sleep 1; done
for node in $(scontrol show hostnames "$(squeue -h -j "$MID" -o %N)"); do
  srun --overlap --jobid="$MID" -w "$node" -N1 -n1 bash -s <<'AFFINITY_CHECK' || echo "NOTE srun --overlap refused on $node"
for pid in $(pgrep -f 'hpc_model_utils[.]infra[.]telemetry[.]sampler'); do
  comm=$(cat "/proc/$pid/comm" 2>/dev/null) || continue
  case $comm in python*) ;; *) continue ;; esac
  cpus=$(awk '$1 == "Cpus_allowed_list:" { print $2 }' "/proc/$pid/status" 2>/dev/null)
  hwm=$(awk '$1 == "VmHWM:" { print $2 }' "/proc/$pid/status" 2>/dev/null)
  echo "AFFINITY node=$(hostname -s) pid=$pid comm=$comm cpus_allowed_list=$cpus vmhwm_kb=$hwm"
done
AFFINITY_CHECK
done | tee -a "$H/readings.txt"
```

If `srun --overlap` is refused on a node, its `NOTE` line says so. Then rerun
the loop body for that node with `ssh -o BatchMode=yes "$node"` in place of
the `srun --overlap --jobid="$MID" -w "$node" -N1 -n1` prefix. When the
reading is not taken, run `echo 'AFFINITY not taken' >> "$H/readings.txt"`.

### After the run ends (head node, Slurm runs only)

Run this after step 2 of the collection, whose copy list puts
`saidas/telemetry/` into `$H/telemetry/`, and before step 3's copy. It appends
the `COMPLETING` line, from the poll, and the `NODE` lines, from the local copy
in `$H/telemetry`. It never reads S3, and never a path under the workspace
root. The per-SHA install that ensure-utils or ensure-tools made for the run
has pyarrow.

```bash
# on the head node
H=<head-scratch>/repin/<run>
if [[ -e $H/squeue.txt ]]; then
  awk -v live='^(PENDING|CONFIGURING|RUNNING|COMPLETING|REQUEUED|SUSPENDED|RESIZING|REQUEUE_HOLD|REQUEUE_FED|STOPPED|SIGNALING|ERROR)$' '
    function secs(j) {
      if (last[j] ~ live) return "NA"
      if (!(j in from)) return 0
      return (j in to) ? to[j] - from[j] : "NA"
    }
    {
      for (i = 3; i <= 4; i++) {
        split($i, kv, "=")
        last[kv[1]] = kv[2]
        if (kv[2] == "ERROR") continue
        if (kv[2] == "COMPLETING") { if (!(kv[1] in from)) from[kv[1]] = $1 }
        else if ((kv[1] in from) && !(kv[1] in to)) to[kv[1]] = $1
      }
    }
    END {
      if (NR == 0) print "COMPLETING model_s=NA finalize_s=NA"
      else print "COMPLETING model_s=" secs("model") " finalize_s=" secs("finalize")
    }' "$H/squeue.txt"
else
  echo 'COMPLETING model_s=NA finalize_s=NA'
fi | tee -a "$H/readings.txt"
{ <tools-root>/hpc-model-utils/<utils-sha40>/.venv/bin/python -I - "$H/telemetry" <<'NODE_CHECK' || echo "NOTE NODE_CHECK failed with exit $?"
import json
import sys
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

KEYS = ["phase", "job_id", "restart", "node_index"]
out = Path(sys.argv[1])
summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
table = pq.read_table(
    out / "samples.parquet", columns=[*KEYS, "cpu_util", "cpu_iowait_cores"]
)
nulls = pc.CountOptions(mode="only_null")
counts = {
    (r["phase"], r["job_id"], r["restart"], r["node_index"]): (
        r["count_all"],
        r["cpu_util_count"],
        r["cpu_iowait_cores_count"],
    )
    for r in table.group_by(KEYS)
    .aggregate(
        [
            ([], "count_all"),
            ("cpu_util", "count", nulls),
            ("cpu_iowait_cores", "count", nulls),
        ]
    )
    .to_pylist()
}


def dec(value: float | None) -> str:
    return "NA" if value is None else f"{value:.4f}"


def num(value: int | None) -> str:
    return "NA" if value is None else str(value)


for job in summary["jobs"]:
    for node in job["nodes"]:
        head, stats = node["header"], node["stats"]
        key = (head["phase"], head["job_id"], head["restart"], head["node_index"])
        rows, util_nulls, iowait_nulls = counts.get(key, (0, 0, 0))
        util = util_nulls / rows if rows else None
        iowait = iowait_nulls / rows if rows else None
        limit = 0.5 * head["interval_s"]
        cost = stats.get("sample_cost_ms_p99")
        rss = stats.get("sampler_maxrss_bytes_max")
        peak = stats.get("job_mem_peak_bytes_max")
        anon = stats.get("job_mem_anon_bytes_max")
        checks = (
            ("cost", cost is None or cost > limit),
            ("rss", rss is None or rss >= 30_000_000),
            ("cpu_util_null", util is None or util > 0.01),
            ("cpu_iowait_null", iowait is None or iowait > 0.10),
        )
        reasons = [name for name, failed in checks if failed]
        if peak is None or anon is None:
            mem = "NA"
        else:
            mem = "PAGE-CACHE" if 2 * anon < peak else "OK"
        verdict = "FAIL:" + ",".join(reasons) if reasons else "PASS"
        print(
            f"NODE phase={head['phase']} job={head['job_id']}"
            f" restart={head['restart']} node_index={head['node_index']}"
            f" rows={rows} interval_s={head['interval_s']}"
            f" cost_p99_ms={dec(cost)} cost_limit_ms={dec(limit)}"
            f" rss_max_bytes={num(rss)} cpu_util_null={dec(util)}"
            f" cpu_iowait_null={dec(iowait)} peak_bytes={num(peak)}"
            f" anon_max_bytes={num(anon)}"
            f" file_max_bytes={num(stats.get('job_mem_file_bytes_max'))}"
            f" used_max_bytes={num(stats.get('mem_used_bytes_max'))}"
            f" mem={mem} verdict={verdict}"
        )
NODE_CHECK
} | tee -a "$H/readings.txt"
```

- `COMPLETING model_s=<n> finalize_s=<n>` gives the seconds each job spent in
  `COMPLETING`, the job-end delay. It is `NA` when the poll log is empty or
  missing, or ends with a job still live. A job never seen in `COMPLETING`
  spent less than one poll there, which counts as 0.
- `NODE_CHECK` joins `summary.json`'s `jobs[].nodes[]` with the per-node null
  counts of `cpu_util` and `cpu_iowait_cores` in `samples.parquet`. It prints
  one line per node entry:
  `NODE phase=<p> job=<id> restart=<r> node_index=<i> rows=<n> interval_s=<s> cost_p99_ms=<x> cost_limit_ms=<y> rss_max_bytes=<b> cpu_util_null=<f> cpu_iowait_null=<f> peak_bytes=<b> anon_max_bytes=<b> file_max_bytes=<b> used_max_bytes=<b> mem=<OK|PAGE-CACHE|NA> verdict=<PASS|FAIL:reasons>`.
  - `cost_limit_ms` is 0.5 × `interval_s` (R57).
  - The reasons come in the order `cost,rss,cpu_util_null,cpu_iowait_null`:
    `cost` when `cost_p99_ms` is NA or above the limit; `rss` when
    `rss_max_bytes` is NA or at least 30 000 000 (R58); `cpu_util_null` when
    that fraction is NA or above 0.01; `cpu_iowait_null` when that fraction
    is NA or above 0.10.
  - `mem=PAGE-CACHE` when 2 × `anon_max_bytes` < `peak_bytes`, that is, when
    more than half of the peak is not anonymous memory. `mem=NA` when either
    is absent.

### On the workstation, after the copy of `$H/` to `$D/`

**The baseline**, on a Slurm run whose `<baseline-execution-id>` is not
`none`. With a fresh token, run
`export_execution <baseline-execution-id> "$D/baseline"` (P4). Its
`executionArtifactsPath` names the baseline's `<hash>`. On the head node, copy
its job logs with
`aws s3 cp --recursive "s3://<artifacts-bucket>/artifacts/<hash>/saidas/logs/" "$H/baseline/"`,
before step 3's copy. With `<baseline-execution-id>` `none`, the baseline
values are `NA`.

Then run this block once, from the repository root, after the last copy of
`$H/` to `$D/`: a later copy would overwrite `$D/readings.txt`. It prints the
`K1` and `ARTIFACTS` lines on every run, and the `UPLOAD` and `WALL` lines on
a Slurm run only.

```bash
# on the workstation, from the repository root
D="$EV/repin/<run>"
X=<execution-id>
MID=<model-id> FID=<finalize-id>          # leave both empty on a run without Slurm jobs
B=<baseline-execution-id>                 # none without a baseline
RT='Executa e acompanha modelo no SLURM'  # on cobre: 'Executa e acompanha cobre no SLURM'
start_at() {  # $1 = job log; prints the epoch of its last HPCMU_START at=, or nothing
  local at
  at=$(sed -n 's/^HPCMU_START .* at=\([0-9TZ:-]*\)$/\1/p' "$1" 2>/dev/null | tail -n 1)
  [[ -n $at ]] && date -u -d "$at" +%s
}
model_wall() {  # $1 = model log, $2 = finalize log; prints whole seconds, or NA
  local s e
  s=$(start_at "$1") && e=$(start_at "$2") && [[ -n $s && -n $e ]] && { echo $((e - s)); return; }
  echo NA
}
meta() {  # $1 = execution-<id>.json, $2 = metadata key; prints the stored value, or NA
  [[ -e $1 ]] || { echo NA; return; }
  jq -r --arg k "$2" '[.executionMetadata[]? | select(.key == $k) | .value] | last // "NA"' "$1"
}
fcount() {  # $1 = fixed string, $2 = file; prints how many lines hold it, or NA without the file
  if [[ -e $2 ]]; then /usr/bin/grep -c -F -- "$1" "$2" || true; else echo NA; fi
}
J="$D/execution-$X.json" L="$D/execution-$X.log" R="$D/run.json"
M="$D/model-$MID.out" F="$D/finalize-$FID.out"
{
  status=$(jq -r '.executionStatus // "NA"' "$J" 2>/dev/null) || status=NA
  diagnosis=$(jq -r '.diagnosis.status // "NA"' "$R" 2>/dev/null) || diagnosis=NA
  relay=NA lines=NA reason=NA
  if [[ -n $MID && -e $M && -e $F && -e $L ]]; then
    /usr/bin/grep -F "[ExecutionOutput] $RT: " "$L" > "$D/relayed-$X.txt" || true
    uv run python -m deploy.modelops.check_relay --relayed "$D/relayed-$X.txt" \
      --job-log "$M" --job-log "$F" > "$D/relay-$X.txt" 2>&1 || true
    relay=$(sed -n 's/^relay: \([A-Z]*\) .*/\1/p' "$D/relay-$X.txt" | tail -n 1)
    lines=$(cat -- "$M" "$F" | /usr/bin/grep -c -E 'hpcmu-telemetry|infra\.telemetry' || true)
  fi
  if [[ -e $R ]]; then
    reason=$(jq -r '.diagnosis.reason // ""' "$R" | /usr/bin/grep -c -F telemetry || true)
  fi
  echo "K1 status=$status diagnosis=$diagnosis relay=${relay:-NA} telemetry_lines=$lines" \
    "telemetry_in_reason=$reason telemetry_upload_failed=$(fcount 'upload of saidas/telemetry/' "$L")"
  files=NA
  if [[ -e $D/saidas.txt ]]; then
    files=$(/usr/bin/grep -c -x -E 'saidas/telemetry/(samples\.parquet|workspace\.parquet|summary\.json)' "$D/saidas.txt" || true)
  fi
  hooks=$(jq '[.executionMetadata[]? | select(.key | startswith("telemetry."))] | length' "$J" 2>/dev/null) || hooks=NA
  echo "ARTIFACTS telemetry_files=$files hooks=${hooks:-NA} export_failed=$(fcount 'telemetry export failed:' "$L")"
  if [[ -n $MID ]]; then
    u=$(meta "$J" duration_seconds.result_upload) ub=NA g=NA flag=NA
    [[ $B == none ]] || ub=$(meta "$D/baseline/execution-$B.json" duration_seconds.result_upload)
    if [[ $u != NA && $ub != NA ]]; then
      g=$(LC_ALL=C awk -v a="$u" -v b="$ub" 'BEGIN { printf "%.2f", a - b }')
      flag=$(LC_ALL=C awk -v g="$g" 'BEGIN { print (g > 150 ? "ABOVE" : "OK") }')
    fi
    echo "UPLOAD duration_s=$u baseline_s=$ub growth_s=$g flag=$flag"
    w=NA bw=NA t=NA flag=NA
    if [[ $status == Success ]]; then
      w=$(model_wall "$M" "$F")
    fi
    if [[ $B != none ]]; then
      ids=$(sed -n 's/.*: Submitted batch job \([0-9][0-9]*\)$/\1/p' "$D/baseline/execution-$B.log" 2>/dev/null)
      bw=$(model_wall "$D/baseline/model-$(sed -n 1p <<<"$ids").out" "$D/baseline/finalize-$(sed -n 2p <<<"$ids").out")
    fi
    [[ $bw == NA ]] || t=$(( bw * 3 / 100 > 60 ? bw * 3 / 100 : 60 ))
    if [[ $w != NA && $bw != NA ]]; then
      if (( w - bw > t )); then flag=ABOVE; else flag=OK; fi
    fi
    echo "WALL model_wall_s=$w baseline_wall_s=$bw tolerance_s=$t flag=$flag"
  fi
} | tee -a "$D/readings.txt"
```

- `K1 status=<executionStatus> diagnosis=<run.json diagnosis.status> relay=<PASS|FAIL|NA> telemetry_lines=<n|NA> telemetry_in_reason=<n> telemetry_upload_failed=<n>`.
  `relay` is the last `relay:` word of `check_relay`, whose full output is
  `relay-<execution-id>.txt`. `telemetry_lines` counts the lines matching
  `hpcmu-telemetry|infra\.telemetry` in the model and finalize job logs.
  `telemetry_in_reason` counts `telemetry` in `run.json`'s
  `diagnosis.reason`. `telemetry_upload_failed` counts
  `upload of saidas/telemetry/` in `execution-<id>.log`. On a run without
  Slurm jobs, `relay` and `telemetry_lines` are `NA`.
- `ARTIFACTS telemetry_files=<n> hooks=<n> export_failed=<n>`.
  `telemetry_files` counts the three `saidas/telemetry/` objects in
  `saidas.txt`. `hooks` counts the stored `telemetry.` metadata keys.
  `export_failed` counts `telemetry export failed:` in `execution-<id>.log`.
- `UPLOAD duration_s=<x> baseline_s=<x|NA> growth_s=<x|NA> flag=<OK|ABOVE|NA>`,
  from the stored `duration_seconds.result_upload` of the run and of
  `<baseline-execution-id>`. `flag=ABOVE` when the growth is above 150.
- `WALL model_wall_s=<n|NA> baseline_wall_s=<n|NA> tolerance_s=<n|NA> flag=<OK|ABOVE|NA>`.
  The interval runs from the model job log's last `HPCMU_START … at=` to the
  finalize job log's last `HPCMU_START … at=`, for the run and for the
  baseline. The baseline's job ids are its exported log's two
  `Submitted batch job` lines. `tolerance_s` is max(3 % of the baseline, 60).
  `flag=ABOVE` when the run's interval exceeds the baseline's by more than the
  tolerance. `model_wall_s` is `NA` unless the status is `Success`, so a
  cancelled or timed-out run reads `NA`. This is a recorded reading, never a
  verdict, because production decks differ.

### What each reading means

- **K1:** trigger 4 when `telemetry_lines`, `telemetry_in_reason` or
  `telemetry_upload_failed` is above 0, or on `relay=FAIL` when
  `relay-<execution-id>.txt` shows a divergence on a line naming telemetry. A
  non-`Success` status with every count at 0 is not trigger 4: it is read
  against triggers 1–3.
- **Budgets:** trigger 5 when a `NODE` line has a `cost` or `rss` reason with
  a number. A reason whose value is `NA` is recorded with the node's `rows=`,
  and is no trigger.
- **Job end:** trigger 6 when `model_s` or `finalize_s` is above 30. `NA` is
  allowed only on a production run that was not polled.
- **Artifacts:** on a Slurm run, `telemetry_files=3` and `hooks` at least 1.
  Otherwise, or with `export_failed` above 0, the announcement is held, and
  there is no rollback.
- **Null fractions:** flag ADR-093 when a `NODE` line has a `cpu_util_null`
  or `cpu_iowait_null` reason. Quote its `rows=`, because a node with fewer
  than 100 rows breaches 1 % with a single null row.
- **Export:** flag ADR-073 on `UPLOAD … flag=ABOVE`. The export's peak RSS
  (ADR-073's 1 GB) is not read on a ModelOps run, because the staging is
  removed with the workspace.
- **Page cache:** flag ADR-077 on any `mem=PAGE-CACHE`.
- **Model wall time:** flag A5-wall on `WALL … flag=ABOVE`.
- **Affinity:** flag ADR-062 when a `cpus_allowed_list` is a single CPU.
  Together with an A5-wall flag on the same run, it points to ADR-062's
  affinity rework.
- **Flags never act during the session.** Each fired flag is recorded, and
  the operator decides it at ticket-062.

## First production runs

- **Which runs.** The first execution of each of `NEWAVE - PEM`,
  `DECOMP - PEM`, `Upload NEWAVE` and `cobre` that starts after the apply and
  reaches a terminal status, whoever starts it. Its directory is
  `prod-<slug>`: `prod-newave-pem`, `prod-decomp-pem`, `prod-upload-newave`
  and `prod-cobre`.
  - A run ended by a cause the release did not introduce, such as a deck
    error or a capacity hold, is recorded, and the workflow's next run is
    watched.
  - A production run that is cancelled or hits its time limit is watched like
    any other. Its `WALL` reading is then `NA`, and its `COMPLETING` reading,
    when polled, is the only prd evidence of that job-end path.
- **How.** Collect it as a smoke run's steps 1–3, with the copy list of the
  matching smoke run plus `saidas/run.json`, then take `## Telemetry readings`,
  its head-node parts before step 3's copy as for a smoke run. Polling the job
  end and the affinity reading are taken when the operator can watch the run
  live, and read `NA` or `not taken` otherwise. `prod-upload-newave` takes
  only the `K1` and `ARTIFACTS` lines.
- **Record** each run's `nodes=` from the `HPCMU_SHAPE` line of its model job
  log, with
  `sed -n 's/^HPCMU_SHAPE nodes=\([^ ]*\) .*/\1/p' "$D/model-<model-id>.out" | tail -n 1`.
  Hydra and `srun --mpi=pmix` across nodes are seen only on a run with more
  than one node.
- **The watch ends** when all four `prod-<slug>` directories hold a
  `readings.txt`. Then hand back, with the copy of `$EV/repin/` at the top of
  this runbook.

## Rollback triggers

Roll back when any one of these holds:

1. a smoke run fails its pass criterion for a reason the release introduced;
2. a chained DECOMP run ends with the stage-mismatch `DATA_ERROR`
   (`FC stage mismatch: NEWCUT …`) on a deck the operator confirms correct;
3. `check_relay` prints `relay: FAIL` for a smoke run;
4. a smoke or production run whose status, exit code, relayed log or
   diagnosis is changed by telemetry (K1). The evidence is the `K1` line of
   `## Telemetry readings`, `check_relay` `relay: FAIL` on lines naming
   `hpcmu-telemetry` or `infra.telemetry`, or a diagnosis that names
   telemetry. A `result_upload` that ends with exit 4 on a failed telemetry
   upload (ADR-084, `upload of saidas/telemetry/`) is also trigger 4;
5. a `NODE` line with a numeric `cost` or `rss` reason (R57, R58);
6. a `COMPLETING` value above 30 s (R60, KillWait).

Triggers 4–6 go to level 1 of `## Rollback`. Triggers 1–3 go to level 2,
unless the evidence names telemetry, in which case level 1 comes first.

Record the trigger with its evidence, then follow the procedure below. A
failure the release did not introduce (a deck error, a capacity hold) is
recorded and the run repeated; it is not a trigger.

Recorded, not triggers. Each is recorded with its evidence, and the operator
decides it at ticket-062:

- the Artifacts hold, `hold the announcement`, which also covers a run that
  misses only the telemetry files or the hooks;
- `flag ADR-093`, the null fractions;
- `flag ADR-073`, the export;
- `flag ADR-077`, the page cache;
- `flag A5-wall`, the model wall time;
- `flag ADR-062`, the affinity.

## Rollback

The rollback has two levels. Level 1 switches telemetry off and keeps
`v2.3.1`. Level 2 puts the pre-apply pins back. Each apply of either level
runs the whole of `## Preview` and `## Apply`: the snapshot, the dry run and
the live guard with its `SAME`, then the fresh token, the report check, and
the post-apply dry run and snapshot. Its `<reference-snapshot>` is the last
apply's `snapshot-post-apply-<TS>`, so a user edit made after the last apply
stops it, and is folded by hand into the commit being applied, never
overwritten.

### Level 1: the telemetry off lever

Apply `## Telemetry off lever`. Then:

- **Trigger 4:** re-run the failed smoke run, or watch the workflow's next
  production run. If K1 then holds, the lever stays on and the cause goes to a
  fix release (`v2.3.2`, ADR-097). If it still fails, go to level 2.
- **Triggers 5 and 6:** the lever stays on, and the cause goes to a fix
  release (ADR-097).
- **A trigger 6 that persists with the lever on** is not telemetry. With
  telemetry off, a `v2.3.1` job script is v2.2.3's, except for the cobre
  binary name `novomodelo-mpi` (ticket-060c's one golden line). Revert the
  lever, and record the delay for the operator.

### Level 2: the pin rollback, re-applying `<fold-commit>`

It restores the pre-apply state of every document that the re-pin chain
changed: the `ensure-utils`, `ensure-tools` and `cobre-run` Tasks, the
`cobre` workflow with its pre-apply bridge pin and options, and the four
utils pairs. `<fold-commit>` holds every live edit that ticket-060a folded,
and live held git's utils and bridge pins at the fold, so it renders the
pre-apply state. The class (a) changes and every folded live edit stay
applied.

#### Step 1: the rollback tree

In a clean checkout, run `git switch --detach <fold-commit>`. Re-apply it
whole, `apply.py` included. The chain's `apply.py` cannot verify the
pre-apply cobre pins, so a commit that restored only `deploy/modelops/tasks`,
`scripts` and `workflows` would fail its dry run with 2 failures, and
`sync --apply` refuses a dry run with failures.

#### Step 2: the rollback check

From the rollback tree's root, with the `EV` of the applies:

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
uv run python - <env-file> "$EV/repin/snapshot-pre-apply-<first-apply-TS>" \
  <<'ROLLBACK_CHECK' | tee "$EV/repin/rollback-check-$TS.txt"
import json
import os
import sys
from pathlib import Path

import deploy.modelops.apply as apply_module
from deploy.modelops.modelops_api import redact
from deploy.modelops.render import canonical, comparison_keys, load_env

env_file, pre_dir = Path(sys.argv[1]), Path(sys.argv[2])
FILES = {"/api/Task/all": "tasks.json", "/api/Workflow/all": "workflows.json"}


class Snapshot:
    def get_json(self, path: str):
        return json.loads((pre_dir / FILES[path]).read_text("utf-8"))


def labels(rendered, live) -> list[str]:
    found = []
    for key in sorted(comparison_keys(rendered)):
        if canonical(rendered, [key]) == canonical(live, [key]):
            continue
        if key == "parameters" and isinstance(live.get(key), list):
            old = {str(p.get("name")): p for p in live[key]}
            new = {str(p.get("name")): p for p in rendered[key]}
            names = sorted(n for n in old.keys() | new.keys() if old.get(n) != new.get(n))
            found += [f"parameters:{n}" for n in names] or ["parameters (order)"]
        else:
            found.append(key)
    return found


token = os.environ.get("MODELOPS_TOKEN", "")
root = Path(apply_module.__file__).resolve().parent
report = apply_module.plan(root, load_env(env_file), Snapshot(), repos={})
lines = [f"ROLLBACK render {redact(problem, token)}" for problem in report.problems]
for entry in report.entries:
    name = f"{entry.kind} {entry.slug}"
    if entry.status == "CHANGED":
        live = (report.live_tasks if entry.kind == "task" else report.live_workflows)[entry.doc_id]
        lines += [f"ROLLBACK {name} {label}" for label in labels(entry.rendered, live)]
    elif entry.status != "UNCHANGED":
        lines.append(f"ROLLBACK {name} status {entry.status}")
for line in lines:
    print(line)
print(f"ROLLBACK {len(lines)} differences")
ROLLBACK_CHECK
```

- It plans the tree against `$EV/repin/snapshot-pre-apply-<first-apply-TS>`,
  the live state right before the first writing apply. It uses `repos={}`,
  makes no network call, and tees `rollback-check-$TS.txt`. It always exits 0,
  because the expected result is the class (a) rows, not zero.
- It prints one `ROLLBACK <kind> <slug> <field>` line per difference, with a
  parameter written `parameters:<name>`, `ROLLBACK <kind> <slug> status <status>`
  for a document missing from the snapshot, and `ROLLBACK render …` for a
  render problem. Then it prints `ROLLBACK <n> differences`.
- **Expected:** exactly one `ROLLBACK <kind> <slug> <field>` line per class
  (a) document and field of the private live-edits record, then the count.
  Today that is:

  ```text
  ROLLBACK task ensure-tools script
  ROLLBACK task ensure-utils script
  ROLLBACK 2 differences
  ```

- **Any other line** names a value that the rollback would not restore, such
  as a live utils pair that differed from git. Commit the snapshot's value on
  top of `<fold-commit>`, as `<rollback-commit>`, and run the check again from
  that tree. Otherwise, `<rollback-commit>` is `<fold-commit>`. A rollback
  never applies while its check names such a value.
- After another ticket-060a round, `<fold-commit>` stays the first round's
  commit, because a later round's commit already holds the chain. The check
  then also names the fields that the later round folded, and the rollback
  commits them on top of `<fold-commit>`.

#### Step 3: the Preview

The Preview of `## Preview` runs from `<rollback-commit>`, with
`<reference-snapshot>` the last apply's `snapshot-post-apply-<TS>`. Its guard
must print `SAME`. So a user edit made after the last apply stops the
rollback, and is folded into `<rollback-commit>` by hand, never overwritten.
The dry run expects:

- `CHANGED` for exactly the documents that the chain changed after
  `<fold-commit>`, in the apply's order:

  ```text
  CHANGED task cobre-run
  CHANGED task ensure-tools
  CHANGED task ensure-utils
  CHANGED workflow cobre
  CHANGED workflow decomp-pem
  CHANGED workflow newave-pem
  CHANGED workflow upload-newave
  ```

  With the lever on, the Task `run` is `CHANGED` too.
- every `PIN` line ending in `ok`. These are the pins of `<fold-commit>`,
  which its own `apply.py` verifies, and this runbook does not quote them.
- the summary line, with m = 7, the block's count:

  ```text
  summary: 19 unchanged, 7 changed, 0 new, 0 failures
  ```

  With the lever on, it is one more changed and one fewer unchanged.

#### Step 4: the apply

The apply of `## Apply`, with its fresh-token step and report check: m
`WRITTEN PUT` lines, the Tasks first, then the workflows. A workflow PUT that
returns 422 means that workflow is executing: `## Apply` step 6.

#### Step 5: the post dry run

It expects `summary: 26 unchanged, 0 changed, 0 new, 0 failures`, and its
snapshot is the next `<reference-snapshot>`.

#### Step 6: what cobre gets back

cobre returns to its pre-apply release, which accepts only a `cobre-mpi`
build and runs the cobre-bridge pin of `<fold-commit>`. A `modelVersion`
whose versions-bucket upload holds only `novomodelo-mpi` then fails at
`fetch-executables`. That is why level 2 is the last resort. A trigger traced
to a class (a) change is escalated, not rolled back here.

#### Step 7: the record

Record the trigger, the time and the affected executions in the evidence.

The installed `hpc-model-utils` `v2.3.1` and novomodelo-bridge directories
under `<tools-root>` stay, and so do the tag records under
`<tools-root>/.tags/`. Each directory is immutable, keyed by its commit, and
harmless after a rollback: no definition references it any more. Do not
remove them.

## Telemetry off lever

The lever is ADR-066's (A4). It switches job telemetry off in the `run` and
`cobre-run` Tasks, and keeps `v2.3.1` and everything else. Its files are
ticket-061's `deploy/modelops/variants/telemetry-off/run.sh` and
`deploy/modelops/variants/telemetry-off/cobre-run.sh`: each is its `tasks/`
script plus `export HPCMU_TELEMETRY=off`.

1. **The lever commit.** From a clean checkout of the last applied commit:

   ```bash
   cp deploy/modelops/variants/telemetry-off/run.sh deploy/modelops/tasks/run.sh
   cp deploy/modelops/variants/telemetry-off/cobre-run.sh deploy/modelops/tasks/cobre-run.sh
   git commit -m "chore: switch prd job telemetry off (ADR-066)" deploy/modelops/tasks/run.sh deploy/modelops/tasks/cobre-run.sh
   ```

   The new `HEAD` is `<lever-commit>`.
2. **The Preview** of `## Preview` runs first, with `<reference-snapshot>`
   the last apply's `snapshot-post-apply-<TS>`, and its guard must print
   `SAME`. The guard is what protects a user's edit of these two scripts,
   because each whole script is one diff line. The dry run expects:

   ```text
   CHANGED task cobre-run
   CHANGED task run
   ```

   Each diff is one `-`/`+` `"script"` line pair whose `-` line holds
   `export HPCMU_TELEMETRY=off`: `-` is the rendered lever commit, and live
   does not have the line yet. It also expects the same `PIN` lines, and:

   ```text
   summary: 24 unchanged, 2 changed, 0 new, 0 failures
   ```

   Any other result stops.
3. **The apply.** One `sync --apply`, with the fresh-token step and the report
   check of `## Apply`, prints `WRITTEN PUT task cobre-run <task-id>` and
   `WRITTEN PUT task run <task-id>`. Then a post dry run with
   `summary: 26 unchanged, 0 changed, 0 new, 0 failures`, and its snapshot.
4. **Its reach.** The lever affects every workflow that uses those Tasks
   (`newave-pem`, `decomp-pem`, `upload-newave`, `cobre`), and only from their
   next run.
5. **Revert:** re-apply `<repin-commit>` (`git switch --detach <repin-commit>`).
   The same Preview, guard and report check run. The dry run shows the same
   two Tasks `CHANGED`, now with the off line on the `+` side, and the same
   `24 unchanged, 2 changed` summary. Apply, then dry run with `26 unchanged`,
   and take its snapshot.
6. Whether to push a lever or rollback commit is the operator's choice. The
   apply needs only a clean tree.
