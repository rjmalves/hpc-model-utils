readonly HPCMU_EXECUTION_ID='{{CurrentExecution.ExecutionId}}'
set -euo pipefail
fail() { printf 'hpcmu-task: %s\n' "$1" >&2; exit 2; }
[[ "$HPCMU_EXECUTION_ID" =~ ^[A-Za-z0-9-]{1,64}$ ]] || fail 'invalid CurrentExecution.ExecutionId'

IFS= read -r -d '' MODEL <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{modelName}}
HPCMU_{{CurrentExecution.ExecutionId}}
MODEL=${MODEL%$'\n'}
[[ "$MODEL" =~ ^(newave|decomp|cobre)$ ]] || fail 'invalid modelName'

IFS= read -r -d '' ROOT_PATH <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{rootPath}}
HPCMU_{{CurrentExecution.ExecutionId}}
ROOT_PATH=${ROOT_PATH%$'\n'}
[[ "$ROOT_PATH" == '@@env:rootPath@@' ]] || fail 'invalid rootPath'

IFS= read -r -d '' WORKDIR <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{path}}
HPCMU_{{CurrentExecution.ExecutionId}}
WORKDIR=${WORKDIR%$'\n'}
[[ "$WORKDIR" =~ ^/[A-Za-z0-9._/-]+/(newave|decomp|cobre)_[A-Za-z0-9]{6}$ ]] || fail 'invalid path'
[[ "${WORKDIR%/*}" == "${ROOT_PATH%/}" ]] || fail 'invalid path'
[[ "${WORKDIR##*/}" == "${MODEL}_"* ]] || fail 'invalid path'

IFS= read -r -d '' JOB_ID <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{jobId}}
HPCMU_{{CurrentExecution.ExecutionId}}
JOB_ID=${JOB_ID%$'\n'}
[[ "$JOB_ID" =~ ^[0-9]*$ ]] || fail 'invalid jobId'

IFS= read -r -d '' SLURM_PATH <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{slurmPath}}
HPCMU_{{CurrentExecution.ExecutionId}}
SLURM_PATH=${SLURM_PATH%$'\n'}
[[ "$SLURM_PATH" == '@@env:slurmPath@@' ]] || fail 'invalid slurmPath'

IFS= read -r -d '' UTILS_DIR <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{utilsToolDir}}
HPCMU_{{CurrentExecution.ExecutionId}}
UTILS_DIR=${UTILS_DIR%$'\n'}
[[ "$UTILS_DIR" =~ ^/[A-Za-z0-9._/-]+/hpc-model-utils/[0-9a-f]{40}$ ]] || fail 'invalid utilsToolDir'
[[ "${UTILS_DIR%/hpc-model-utils/*}" == '@@env:toolsRoot@@' ]] || fail 'invalid utilsToolDir'

cd -- "$WORKDIR"
exec "$UTILS_DIR/.venv/bin/hpc-model-utils" cancel_run "$MODEL" --job-id "$JOB_ID" --slurm-path "$SLURM_PATH"
