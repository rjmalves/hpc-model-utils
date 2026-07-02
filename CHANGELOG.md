# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
