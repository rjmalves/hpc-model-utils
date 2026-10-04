cd {{path}}
hpc-model-utils/venv/bin/hpc-model-utils run {{modelName}} {{queue}} {{coreCount}} --max-job-time-hours {{jobTimeoutHours}} --mpich-path {{mpichPath}} --slurm-path {{slurmPath}}