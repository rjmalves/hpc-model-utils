readonly HPCMU_EXECUTION_ID='{{CurrentExecution.ExecutionId}}'
set -euo pipefail
fail() { printf 'hpcmu-task: %s\n' "$1" >&2; exit 2; }
[[ "$HPCMU_EXECUTION_ID" =~ ^[A-Za-z0-9-]{1,64}$ ]] || fail 'invalid CurrentExecution.ExecutionId'

IFS= read -r -d '' MODEL <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{modelName}}
HPCMU_{{CurrentExecution.ExecutionId}}
MODEL=${MODEL%$'\n'}
[[ "$MODEL" =~ ^(newave|decomp)$ ]] || fail 'invalid modelName'

IFS= read -r -d '' ROOT_PATH <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{rootPath}}
HPCMU_{{CurrentExecution.ExecutionId}}
ROOT_PATH=${ROOT_PATH%$'\n'}
[[ "$ROOT_PATH" == '@@env:rootPath@@' ]] || fail 'invalid rootPath'

IFS= read -r -d '' WORKDIR <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{path}}
HPCMU_{{CurrentExecution.ExecutionId}}
WORKDIR=${WORKDIR%$'\n'}
[[ "$WORKDIR" =~ ^/[A-Za-z0-9._/-]+/(newave|decomp)_[A-Za-z0-9]{6}$ ]] || fail 'invalid path'
[[ "${WORKDIR%/*}" == "${ROOT_PATH%/}" ]] || fail 'invalid path'
[[ "${WORKDIR##*/}" == "${MODEL}_"* ]] || fail 'invalid path'

IFS= read -r -d '' OUTPUTS_BUCKET <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{outputsBucket}}
HPCMU_{{CurrentExecution.ExecutionId}}
OUTPUTS_BUCKET=${OUTPUTS_BUCKET%$'\n'}
[[ "$OUTPUTS_BUCKET" == '@@env:outputsBucket@@' ]] || fail 'invalid outputsBucket'

IFS= read -r -d '' AWS_REGION <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{awsRegion}}
HPCMU_{{CurrentExecution.ExecutionId}}
AWS_REGION=${AWS_REGION%$'\n'}
[[ "$AWS_REGION" == '@@env:awsRegion@@' ]] || fail 'invalid awsRegion'

IFS= read -r -d '' EXECUTION_HASH <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{CurrentExecution.ExecutionHash}}
HPCMU_{{CurrentExecution.ExecutionId}}
EXECUTION_HASH=${EXECUTION_HASH%$'\n'}
[[ "$EXECUTION_HASH" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid CurrentExecution.ExecutionHash'

IFS= read -r -d '' UTILS_DIR <<'HPCMU_{{CurrentExecution.ExecutionId}}' || :
{{utilsToolDir}}
HPCMU_{{CurrentExecution.ExecutionId}}
UTILS_DIR=${UTILS_DIR%$'\n'}
[[ "$UTILS_DIR" =~ ^/[A-Za-z0-9._/-]+/hpc-model-utils/[0-9a-f]{40}$ ]] || fail 'invalid utilsToolDir'
[[ "${UTILS_DIR%/hpc-model-utils/*}" == '@@env:toolsRoot@@' ]] || fail 'invalid utilsToolDir'

export AWS_DEFAULT_REGION="$AWS_REGION"
cd -- "$WORKDIR"
exec "$UTILS_DIR/.venv/bin/hpc-model-utils" result_upload "$MODEL" "s3://${OUTPUTS_BUCKET}/artifacts/${EXECUTION_HASH}"
