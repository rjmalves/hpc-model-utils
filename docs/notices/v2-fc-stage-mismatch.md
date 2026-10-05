# Notice: hpc-model-utils DECOMP FC stage mismatch becomes a data error

## Who is affected

encadeador-pem users, and anyone chaining `DECOMP - PEM` runs to a NEWAVE
parent and acting on ModelOps run statuses.

## When

When the prd workflows move to hpc-model-utils v2.2.0 or later; the date is
announced with this notice. The workflow ids and parameter names do not change:
encadeador keeps sending `inputFile`, `coreCount`, `modelVersion` and
`parentPath` exactly as today.

## What changes

A chained DECOMP run couples the end of its horizon to the parent NEWAVE
future-cost function through the dadger `FC NEWV21` and `FC NEWCUT` registers.
`FC NEWCUT` must name the `cortes-NNN.dat` file of the NEWAVE stage that the
DECOMP horizon end needs. In v2.0.0 to v2.1.0 a `NEWCUT` naming another stage
only appended a note to the run's annotation: the run kept the status it would
have had anyway, typically `SUCCESS`, although DECOMP had read the wrong cuts.
From v2.2.0 the run fails in `preprocess`, before any Slurm job is submitted.

| Outcome | Before (v2.0.0 to v2.1.0) status → hook | After (v2.2.0) status → hook | Effect on encadeador |
| --- | --- | --- | --- |
| Chained DECOMP whose FC NEWCUT names the wrong NEWAVE stage | The run's own status, typically `SUCCESS` → `SetSuccess`, with `; FC stage mismatch: …` appended to the annotation | `DATA_ERROR` → `SetDataError`, raised in `preprocess`; no job is submitted | A run that would have used the wrong cuts is no longer reported as successful: it ends as a data error before the model runs. |

The annotation has the form:

```
DATA_ERROR: preprocess: <dadger>: FC stage mismatch: NEWCUT <actual>, expected <expected> from the parent start <YYYY-MM-DD> and the DECOMP horizon end <YYYY-MM-DD>
```

For example:

```
DATA_ERROR: preprocess: dadger.rv0: FC stage mismatch: NEWCUT cortes-012.dat, expected cortes-024.dat from the parent start 2024-11-01 and the DECOMP horizon end 2026-01-01
```

The annotation format and every other row of the table in
[`v2-status-semantics.md`](v2-status-semantics.md) are unchanged.

## How the expected file is computed

`end` is the DECOMP horizon end: the `DT` start date plus the durations, in
hours, of the load blocks of the first `DP` register of each stage, summed over
the stages. `parent` is the start date of the parent NEWAVE study. The expected
file is `cortes-NNN.dat`, with `NNN` zero-padded to three digits:

```
NNN = 12 * (end.year - parent.year) + end.month - 1
```

That is the number of calendar months from January of the parent's start year
to the month of the horizon end.

Worked example, with the fixture dates: a deck whose horizon ends on
2026-01-01, chained to a parent that starts on 2024-11-01, gives
`NNN = 12 * (2026 - 2024) + 1 - 1 = 24`, so the expected file is
`cortes-024.dat`. The deck's `FC NEWCUT` names `cortes-012.dat`, so the run
fails with the message above. The same deck chained to a parent that starts on
2025-11-01 expects `cortes-012.dat` and passes.

The formula assumes a parent deck with no pre-study years (`No. DE ANOS PRE 0`
and `MES INICIO PRE-EST 1` in the parent's `dger.dat`, the PMO convention);
whether pre-study years shift `NNN` is not known, and DECOMP cannot detect them
from the parent start date.

## What does not change

- Runs without a parent (no `parentPath`): no stage check.
- Decks without `FC NEWV21` and `FC NEWCUT` registers: no stage check.
- A `NEWCUT` whose file name is not of the form `cortes-NNN.dat`: no stage
  check.
- A `NEWCUT` that already names the expected file: the run is not affected.
- An expectation that cannot be computed (a missing or incomplete `DT`
  register, unusable `DP` durations, a parent start date that is not ISO 8601):
  the check is skipped with a warning in the log, as before, and the run keeps
  its own status.

## Checklist for encadeador developers

Before the switch date, check each point against encadeador's code:

1. **Reaction to `SetDataError`.** For a chained DECOMP run, `SetDataError` now
   also arrives for a stage mismatch, from `preprocess`, before any job has
   run. Resubmitting the same deck against the same parent fails the same way,
   so a retry on `SetDataError` only repeats the failure. If encadeador stops,
   retries or flexibilizes on `SetDataError` for a chained DECOMP run, confirm
   that this is the intended reaction to this case.
2. **Annotation text.** If encadeador reads or displays the run's annotation,
   expect the `DATA_ERROR: preprocess: <dadger>: FC stage mismatch: …` form
   above. It names the `NEWCUT` found, the expected file and the two dates.
   Before, the same text arrived as a `; FC stage mismatch: …` suffix on an
   otherwise normal annotation.
3. **Fixing the deck.** A user fixes the deck by pointing the dadger
   `FC NEWCUT` register at the expected file named in the message
   (`cortes-024.dat` in the example above) and resubmitting. Check first that
   the parent start in the message is the parent the run was meant to chain to.
