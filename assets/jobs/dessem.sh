#!/bin/bash

# ONS - ModelOPS
#
# .job script for running the DESEM model in HPC environment,
# with the SLURM workload manager.
#
# It is expected that both MPICH and SLURM binary installation
# directories are in $PATH (ex. /opt/slurm/bin and /usr/local/mpich/bin)
#

#
# Command for invoking using the SLURM manager in the current directory:
#
# sbatch --partition=$QUEUE_NAME --contiguous --job-name=`basename $PWD`
# --output="main.stdout" --error="main.stderr" --ntasks $NUM_CPUS
#  --cpus-per-task=1 hpc-model-utils/assets/jobs/dessem.job $NUM_CPUS
#

# This .job is meant to be used together with the hpc-model-utils
# app and may expect some patterns and business rules to be matched

# Inputs and important variables
MODEL_NAME="dessem"
EXEC="mpiexec"
DESSEM="./assets/dessem"
STATUS_DIAGNOSIS_FILE="status.modelops"
UTILS_APP="./hpc-model-utils/venv/bin/hpc-model-utils"
SYNTHESIS_APP="./sintetizador-dessem/venv/bin/sintetizador-dessem"

# Runs the model
$DESSEM

$UTILS_APP generate_execution_status $MODEL_NAME --job-id $SLURM_JOB_ID

# Only does heavy post-processing on successful runs
if grep -q "SUCCESS" "$STATUS_DIAGNOSIS_FILE"; then
    $UTILS_APP postprocess $MODEL_NAME
    $SYNTHESIS_APP completa
fi

$UTILS_APP output_compression_and_cleanup $MODEL_NAME $NUM_CPUS
