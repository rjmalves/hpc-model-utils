# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
