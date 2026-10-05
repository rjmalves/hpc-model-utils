cd {{path}}
export AWS_ACCESS_KEY_ID={{awsKeyId}}
export AWS_SECRET_ACCESS_KEY={{awsSecretKey}}
export AWS_DEFAULT_REGION={{awsRegion}}
ranqueamento-prospectivo-utils/venv/bin/ranqueamento-prospectivo-utils check_and_fetch_inputs {{inputFile}} --parent-path {{parentPath}} --delete 
ranqueamento-prospectivo-utils/venv/bin/ranqueamento-prospectivo-utils extract_sanitize_inputs