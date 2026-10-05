cd {{path}}
ranqueamento-prospectivo-utils/venv/bin/ranqueamento-prospectivo-utils run {{queue}} {{coreCount}} --max-job-time-hours {{jobTimeoutHours}} --mpich-path {{mpichPath}} --slurm-path {{slurmPath}}
ranqueamento-prospectivo-utils/venv/bin/ranqueamento-prospectivo-utils result_upload "s3://{{outputsBucket}}/artifacts/{{CurrentExecution.ExecutionHash}}"