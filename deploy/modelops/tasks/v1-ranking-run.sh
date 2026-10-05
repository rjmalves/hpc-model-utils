readonly HPCMU_EXECUTION_ID='{{CurrentExecution.ExecutionId}}'
fail() { printf 'hpcmu-task: %s\n' "$1" >&2; exit 2; }
[[ "$HPCMU_EXECUTION_ID" =~ ^[A-Za-z0-9-]{1,64}$ ]] || fail 'invalid CurrentExecution.ExecutionId'

IFS= read -r -d '' UTILS_DIR <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{utilsToolDir}}
HPCMU_{{CurrentExecution.ExecutionId}}
UTILS_DIR=${UTILS_DIR%$'\n'}
[[ "$UTILS_DIR" =~ ^/[A-Za-z0-9._/-]+/hpc-model-utils/[0-9a-f]{40}$ ]] || fail 'invalid utilsToolDir'
[[ "${UTILS_DIR%/hpc-model-utils/*}" == '@@env:toolsRoot@@' ]] || fail 'invalid utilsToolDir'

IFS= read -r -d '' SYNTHESIS_DIR <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{synthesisToolDir}}
HPCMU_{{CurrentExecution.ExecutionId}}
SYNTHESIS_DIR=${SYNTHESIS_DIR%$'\n'}
[[ "$SYNTHESIS_DIR" =~ ^/[A-Za-z0-9._/-]+/sintetizador-newave/[0-9a-f]{40}$ ]] || fail 'invalid synthesisToolDir'
[[ "${SYNTHESIS_DIR%/sintetizador-newave/*}" == '@@env:toolsRoot@@' ]] || fail 'invalid synthesisToolDir'

export HPCMU_UTILS_APP="$UTILS_DIR/.venv/bin/hpc-model-utils"
export HPCMU_SYNTHESIS_APP="$SYNTHESIS_DIR/.venv/bin/sintetizador-newave"
cd {{path}}
ranqueamento-prospectivo-utils/venv/bin/ranqueamento-prospectivo-utils run {{queue}} {{coreCount}} --max-job-time-hours {{jobTimeoutHours}} --mpich-path {{mpichPath}} --slurm-path {{slurmPath}}
ranqueamento-prospectivo-utils/venv/bin/ranqueamento-prospectivo-utils result_upload "s3://{{outputsBucket}}/artifacts/{{CurrentExecution.ExecutionHash}}"