cd {{path}}
export AWS_ACCESS_KEY_ID={{awsKeyId}}
export AWS_SECRET_ACCESS_KEY={{awsSecretKey}}
export AWS_DEFAULT_REGION={{awsRegion}}
hpc-model-utils/venv/bin/hpc-model-utils ingest_offline_run {{modelName}} {{inputFile}} {{outputFile}} {{cutFile}}