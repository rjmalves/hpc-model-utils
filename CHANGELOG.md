# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [2.0.0] - 2026-10-03

### Behavior changes

- DECOMP max iterations (no convergence) now calls `SetRuntimeError` instead of `SetModelError`.
- DECOMP negative optimality gap now calls `SetRuntimeError` instead of `SetModelError`.
- A crashed DECOMP run, or one missing `relato`, now calls `SetRuntimeError` instead of `SetModelError`.
- A NEWAVE full run whose final simulation lacks the simulated-series cost table now calls `SetRuntimeError` instead of `SetSuccess`.
- A NEWAVE run with no `pmo.dat` now calls `SetRuntimeError`, where v1 called `SetDataError`
  (or `SetSuccess` for a consistency run).
- A job ended by timeout, node failure, licence failure or cancellation now calls `SetRuntimeError` (`TIMEOUT`/`INFRA_ERROR`/`LICENSE_ERROR`/`CANCELLED`) instead of depending on incidental output parsing.
- A successful run whose sintetizador or postprocess step failed still calls `SetSuccess`, but the reason is now prefixed `synthesis failed:`/`postprocess failed:` and `synthesis_status=failed` is recorded.
- A successful run with a missing sintetizador now calls `SetRuntimeError` (`core.synthesis_missing`) instead of `SetSuccess`.
- See [`docs/notices/v2-status-semantics.md`](docs/notices/v2-status-semantics.md) for the full comparison and the operator delivery checklist.

### Added

- The v2 `hpc_model_utils` engine with typed run state (`.hpcmu/state.json`).
- Kebab-case command aliases.
- `saidas/run.json`.
- Per-phase job logs under `saidas/logs/`.
- `saidas/relgnl.<ext>` for DECOMP.
- `run --synthesis-bin`.
- `HPCMU_*` engine tunables.
- Signal-driven cancellation of submitted jobs.

### Changed

- The `hpc-model-utils` console script now runs `hpc_model_utils.cli:main`.
- A missing sintetizador on a successful run is an annotated `RUNTIME_ERROR`.
- `botocore` and `cfinterface` are now declared direct dependencies.
- Production workflows adopt 2.0.0 only after a pre-rollout validation of the new workflow copies; until then they keep their current pins.

### Removed

- DESSEM and GEVAZP.
- The `output_compression_and_cleanup`, `download_executed_run` and `fetch_extract_raw_outputs` commands.
- `assets/jobs/*`.
- The `idessem` and `pytz` dependencies.

## [1.1.2] - 2026-07-22

### Fixed

- Offline-ingested NEWAVE runs no longer execute the full model. `run` now reads the `execution_source = OFFLINE` flag recorded by `ingest_offline_run` and submits only the post job (status → postprocess → compression), so an offline run driven by the external scheduler skips model execution without requiring an explicit `--skip` flag. The `--skip` flag remains available as an override.

## [1.1.1] - 2026-07-21

### Added

- Added insurance to avoid resource scarcity while creating new threads in `newave_post.job` and `decomp_post.job`

## [1.1.0] - 2026-07-17

### Added

- `ingest_offline_run` command (NEWAVE): ingests a run executed offline (outside the cluster) from three explicit S3 object keys — the inputs, outputs and Benders-cuts archives. The archives may arrive under arbitrary names, so ingestion does not depend on archive names: it downloads all three, extracts them together into the working directory, standardizes filenames and encoding, rebuilds the raw input-deck echo (`eco_deck.zip`) from the deck contents, points the process manager at the executables directory, and records study metadata. This lets an offline run flow through the same downstream steps (`generate_execution_status` → `postprocess` → `output_compression_and_cleanup` → `result_upload`) via `run --skip`, as if it had executed on the cluster.
- Offline runs are marked with an `execution_source = OFFLINE` metadata flag and a ModelOps annotation so they remain distinguishable from cluster executions.

## [1.0.6] - 2026-07-02

### Changed

- Added `qprevs-medio-usina.csv` to the list of NEWAVE files to be processed into `relatorios.zip`

## [1.0.5] - 2026-06-09

### Fixed

- Collect `deco_*.msg` files as DECOMP report outputs (were previously excluded from the uploaded outputs)

### Changed

- Bump dependencies to their latest releases: `boto3` 1.43, `click` 8.4, `idecomp` 1.10, `idessem` 1.2, `inewave` 1.13, `boto3-stubs` 1.43, `mypy` 2.1, `pytest-cov` 7.1, `pytest-timeout` 2.4, `requests` 2.34, `ruff` 0.15 (plus refreshed transitive dependencies in `uv.lock`)

## [1.0.4] - 2026-05-27

### Fixed

- Cap CPU count for `SYNTHESIS_APP` calls (at physical cores via `lscpu`) and `output_compression_and_cleanup` (at vCPUs via `nproc`) in `newave_post.job` and `decomp_post.job` to avoid resource over-subscription on HPC hosts
- Add missing `--processadores` flag to `decomp_post.job` synthesis call so it explicitly limits parallelism

## [1.0.3] - 2026-04-17

### Fixed

- Add support to DECOMP license filename `decomp_trial.cep`

## [1.0.2] - 2026-03-20

### Fixed

- Model version extraction in `check_and_fetch_executables` across all models (NEWAVE, DECOMP, DESSEM, GEVAZP) — was registering empty values due to path ending in '/'

## [1.0.1] - 2026-03-13

### Fixed

- Model version extraction in `check_and_fetch_executables` across all models (NEWAVE, DECOMP, DESSEM, GEVAZP) — was registering model name instead of version due to wrong split index
- Unit tests for NEWAVE and DECOMP repositories updated to match v1.0.0 API signatures (S3 path-based interface)
- Removed obsolete test references to deleted `generate_unique_input_id` method and renamed constants
- Fixed test mocks for `run` (mocking `submit_job`/`follow_submitted_job` directly instead of low-level terminal calls)
- Marked `test_uploads_empty_file` integration test as `xfail` due to LocalStack 3.0 bug with empty PutObject

## [1.0.0] - 2026-03-11

### Added

- Input validation layer with fail-fast semantics for all CLI commands
- Structured error hierarchy (`CLIError`, `ValidationError`, `SlurmError`, `S3Error`, `ModelError`) with distinct exit codes
- SLURM job monitoring redesign with `sacct` fallback for fast-finishing jobs
- Command timing decorator for observability
- Custom Click parameter types (`ModelNameType`, `S3PathType`, `PositiveIntType`)
- Per-command input validators
- Comprehensive unit test suite (validation, error handling, SLURM monitoring, Click types)
- S3 integration tests with LocalStack
- CI/CD pipeline with GitHub Actions (unit tests, type checking, linting, integration tests)
- Professional packaging with `pyproject.toml` and `hatchling` build backend
- Installation script (`setup.sh`) with automatic PATH symlink

### Models

- **NEWAVE**: Full workflow support — input parsing (inewave), status diagnosis from `pmo.dat`, postprocessing with `nwlistcf`/`nwlistop`, parallel output compression
- **DECOMP**: Full workflow support — input parsing (idecomp), status diagnosis from `relato`/`inviab_unic`, parent NEWAVE metadata handling
- **DESSEM**: Full workflow support — input parsing (idessem), status diagnosis from `DES_LOG_RELATO`, core count injection
