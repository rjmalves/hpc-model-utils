# hpc-model-utils

CLI tool for running energy planning models (NEWAVE, DECOMP, DESSEM) in HPC clusters with SLURM job scheduling and AWS S3 integration.

## Supported Models

| Model  | Input Parser | Output Diagnosis | Postprocessing     |
| ------ | ------------ | ---------------- | ------------------ |
| NEWAVE | inewave      | pmo.dat          | nwlistcf, nwlistop |
| DECOMP | idecomp      | relato, inviab   | -                  |
| DESSEM | idessem      | DES_LOG_RELATO   | -                  |

## Execution Workflow

The CLI orchestrates each model execution as a sequence of discrete steps, designed to be called by an external scheduler (ModelOps):

1. **check-and-fetch-executables** — Download model binaries from S3
2. **check-and-fetch-inputs** — Download model input deck from S3
3. **extract-sanitize-inputs** — Unzip and encoding-sanitize inputs
4. **preprocess** — Model-specific preprocessing (deck patching, parent data extraction)
5. **run** — Submit job to SLURM and monitor until completion
6. **generate-execution-status** — Diagnose run outcome (SUCCESS, INFEASIBLE, DATA_ERROR, RUNTIME_ERROR, COMMUNICATION_ERROR, UNKNOWN)
7. **postprocess** — Model-specific postprocessing (e.g., nwlistcf/nwlistop for NEWAVE)
8. **output-compression-and-cleanup** — Parallel ZIP compression of outputs
9. **result-upload** — Upload results to S3

Additional commands: `cancel-run`, `download-executed-run`, `fetch-extract-raw-outputs`.

## Installation

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/rjmalves/hpc-model-utils.git
cd hpc-model-utils
./setup.sh
```

The setup script creates a virtual environment, installs dependencies, and symlinks the `hpc-model-utils` command to `~/.local/bin/`.

To force-recreate the environment:

```bash
./setup.sh --force
```

### Manual Installation

```bash
uv sync
uv run hpc-model-utils --version
```

Or with pip:

```bash
pip install .
hpc-model-utils --version
```

## Usage

```bash
# Show available commands
hpc-model-utils --help

# Show version
hpc-model-utils --version

# Example: fetch inputs from S3
hpc-model-utils check-and-fetch-inputs \
  --model-name NEWAVE \
  --s3-inputs-path s3://bucket/inputs/case-001/ \
  --target-dir /scratch/case-001/

# Example: submit a SLURM job
hpc-model-utils run \
  --model-name NEWAVE \
  --target-dir /scratch/case-001/ \
  --queue normal \
  --core-count 64

# Example: diagnose execution status
hpc-model-utils generate-execution-status \
  --model-name NEWAVE \
  --target-dir /scratch/case-001/
```

### Exit Codes

| Code | Meaning          |
| ---- | ---------------- |
| 0    | Success          |
| 1    | Model error      |
| 2    | Validation error |
| 3    | SLURM error      |
| 4    | S3 error         |
| 99   | Unknown error    |

## Development

```bash
# Install with dev dependencies
uv sync --dev

# Run unit tests
uv run pytest tests/ -m "not integration"

# Run with coverage
uv run pytest tests/ -m "not integration" --cov=app --cov-report=html

# Type checking
uv run mypy ./app

# Linting
uv run ruff check ./app
```

### Integration Tests (LocalStack)

Integration tests use [LocalStack](https://localstack.cloud/) for S3 operations without AWS credentials.

```bash
# Start LocalStack
docker compose -f docker-compose.localstack.yml up -d

# Run integration tests
uv run pytest tests/integration/ -v -m integration

# Stop LocalStack
docker compose -f docker-compose.localstack.yml down
```

## License

[MIT](LICENSE)
