# Notice: hpc-model-utils v2 status semantics change

## Who is affected

encadeador-pem users, and anyone acting on ModelOps run statuses for the
NEWAVE and DECOMP workflows.

## When

When the prd workflows switch to hpc-model-utils v2; the date is announced
with this notice.

## What changes

v1 reported only three outcomes to ModelOps: `SetSuccess`, `SetDataError`,
and `SetModelError` for everything else — including a non-converged run, a
negative optimality gap, and a crashed run, which v1 flexibilized as if they
were an infeasible DECOMP run. v2 keeps `SetSuccess` and `SetDataError` where
v1 already used them, but reports most other outcomes as `SetRuntimeError`
instead of `SetModelError`, and adds new, narrower status tokens so each
outcome is reported on its own terms instead of being folded into whichever
of the three v1 buckets happened to fit best.

| Outcome | v1 status → hook | v2 status → hook | Effect on encadeador |
| --- | --- | --- | --- |
| DECOMP infeasible | `INFEASIBLE` → `SetModelError` | `INFEASIBLE` → `SetModelError` (`decomp.infeasible`) | Unchanged: still flexibilized as a model error. |
| DECOMP max iterations | `RUNTIME_ERROR` → `SetModelError` | `RUNTIME_ERROR` → `SetRuntimeError` (`decomp.max_iterations`) | No longer flexibilized as a model error; a run that did not converge now reports as a runtime error. |
| DECOMP negative gap | `RUNTIME_ERROR` → `SetModelError` | `RUNTIME_ERROR` → `SetRuntimeError` (`decomp.negative_gap`) | No longer flexibilized as a model error; a negative optimality gap now reports as a runtime error. |
| DECOMP crash or missing `relato` | `UNKNOWN` → `SetModelError` | `RUNTIME_ERROR` → `SetRuntimeError` (`core.missing_output` or `core.diagnosis_exception`) | No longer flexibilized as if it were infeasible; a crashed run (or one that never produced `relato`) now reports as a runtime error. |
| DECOMP data error | `DATA_ERROR` → `SetDataError` | `DATA_ERROR` → `SetDataError` (`decomp.data_error`) | Unchanged. |
| DECOMP no CMO | `DATA_ERROR` → `SetDataError` | `DATA_ERROR` → `SetDataError` (`decomp.no_cmo`) | Unchanged. |
| NEWAVE final simulation incomplete | `SUCCESS` → `SetSuccess` | `RUNTIME_ERROR` → `SetRuntimeError` (`newave.final_simulation_incomplete`) | A full run whose final simulation never produced the simulated-series cost table is no longer reported as successful. |
| NEWAVE missing `pmo.dat` | `DATA_ERROR` → `SetDataError` (the absent file read as empty, so its tables were missing); `SUCCESS` → `SetSuccess` for a consistency run (TIPO SIMUL. FINAL 3 override) | `RUNTIME_ERROR` → `SetRuntimeError` (`core.missing_output`) | A run with no `pmo.dat` output is a runtime error whatever its type: no longer a data error, and a consistency run without it is no longer reported as successful. |
| A job ended by timeout, node failure, licence failure or cancellation | No dedicated v1 token — v1 never branched the ModelOps hook on job/scheduler state; the outcome depended entirely on whatever `generate_execution_status` could (or could not) parse from the run's possibly-incomplete outputs | `TIMEOUT`, `INFRA_ERROR`, `LICENSE_ERROR` or `CANCELLED` → `SetRuntimeError` | These job-level outcomes are now reported distinctly and consistently as a runtime error, instead of depending on incidental output parsing. |
| SUCCESS with a failed sintetizador or postprocess step | `SUCCESS` → `SetSuccess`, with no record that the step failed | `SUCCESS` → `SetSuccess`, reason prefixed `synthesis failed:` or `postprocess failed:`, and `synthesis_status=failed` | The hook is unchanged, but the failure is now visible in the run's annotation and metadata instead of being silent. |
| SUCCESS with a missing sintetizador | `SUCCESS` → `SetSuccess` (the missing program ran as a failed shell command and the job script continued) | `RUNTIME_ERROR` → `SetRuntimeError` (`core.synthesis_missing`) | A run whose sintetizador never executed is no longer reported as successful. |

## New status tokens and the annotation format

v2 adds four status tokens that v1 never emitted: `TIMEOUT`, `INFRA_ERROR`,
`LICENSE_ERROR`, and `CANCELLED`. They are purely additive — no non-success
status token contains `SUCCESS` as a substring, so any consumer that matches
on that substring is unaffected.

Every v2 diagnosis also carries a human-readable annotation, in the form:

```
<STATUS>: <reason> [<rule_id>]
```

For example: `RUNTIME_ERROR: convergence not reached (CONVERGENCIA NAO
ALCANCADA) at iteration 30: ZINF 123.45, ZSUP 150.00, gap 3.2% [decomp.max_iterations]`

v1 carried no equivalent reason or rule identifier. encadeador does not read
the `status` metadata key itself and is not affected by this addition; it
acts only on the hook methods (`SetSuccess`, `SetDataError`,
`SetModelError`, `SetRuntimeError`) described in the table above.

## Artifact changes

These change the files uploaded for a run. See
[`tests/goldens/v1_1_2/README.md`](../../tests/goldens/v1_1_2/README.md),
section "Intended differences", for the full list; the headline changes are:

- `saidas/logs/<phase>-<jobid>.out` replaces `stdout.modelops` and `stderr.modelops`.
- `saidas/relgnl.<ext>` is added to the uploaded DECOMP outputs.
- `saidas/run.json` is added.
- The `entradas/<dadger>` echo is no longer uploaded.
- The residual NEWAVE deck `.dat` files are no longer uploaded directly under `saidas/`; they are inside `deck_processado.zip`.

## What does not change

- Model names, archive layouts, and the existing `metadata.modelops` keys are unchanged.
- Parent-run chaining still compares only `status == "SUCCESS"`.
- The `Submitted batch job <id>` line printed to the job log is unchanged.

## Coming later

The DECOMP FC stage-mismatch check is currently a warning only, appended to
the annotation. Its promotion to a `DATA_ERROR` outcome is **not** part of
this v2.0.0 change; it ships in a later change and will be announced in its
own, separate notice.
