cd {{path}}
export AWS_ACCESS_KEY_ID={{awsKeyId}}
export AWS_SECRET_ACCESS_KEY={{awsSecretKey}}
export AWS_DEFAULT_REGION={{awsRegion}}
hpc-model-utils/venv/bin/hpc-model-utils result_upload {{modelName}} "s3://{{outputsBucket}}/artifacts/{{CurrentExecution.ExecutionHash}}"