#!/bin/bash

# ONS - ModelOPS
#
# .sh script for running the GEVAZP model in a Linux Shell.
#

# This .sh is meant to be used together with the hpc-model-utils
# app and may expect some patterns and business rules to be matched

# Inputs and important variables
MODEL_NAME="gevazp"
GEVAZP="./assets/gevazp"
STATUS_DIAGNOSIS_FILE="status.modelops"
UTILS_APP="./hpc-model-utils/venv/bin/hpc-model-utils"

# Runs the model
$GEVAZP > modelops.stdout 2> modelops.stderr

$UTILS_APP generate_execution_status $MODEL_NAME --job-id $SLURM_JOB_ID

# Only does heavy post-processing on successful runs
if grep -q "SUCCESS" "$STATUS_DIAGNOSIS_FILE"; then
    $UTILS_APP postprocess $MODEL_NAME
fi

$UTILS_APP output_compression_and_cleanup $MODEL_NAME $NUM_CPUS