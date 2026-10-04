cd {{path}}
git clone -c advice.detachedHead=false --quiet --branch {{utilsAppVersion}} https://github.com/rjmalves/hpc-model-utils.git
cd hpc-model-utils
python3 -m venv ./venv
venv/bin/pip install --quiet .