cd {{path}}
export AWS_ACCESS_KEY_ID={{awsKeyId}}
export AWS_SECRET_ACCESS_KEY={{awsSecretKey}}
export AWS_DEFAULT_REGION={{awsRegion}}
hpc-model-utils/venv/bin/hpc-model-utils check_and_fetch_inputs {{modelName}} {{inputFile}} --parent-path {{parentPath}} --delete 