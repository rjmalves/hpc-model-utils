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

WORKDIR=$(mktemp -d -p "$ROOT_PATH" -t "${MODEL}_XXXXXX")
[[ "$WORKDIR" =~ ^/[A-Za-z0-9._/-]+/(newave|decomp|cobre)_[A-Za-z0-9]{6}$ ]] || fail 'invalid path'
[[ "${WORKDIR%/*}" == "${ROOT_PATH%/}" ]] || fail 'invalid path'
[[ "${WORKDIR##*/}" == "${MODEL}_"* ]] || fail 'invalid path'
printf 'Created temporary dir %s\n' "$WORKDIR"
