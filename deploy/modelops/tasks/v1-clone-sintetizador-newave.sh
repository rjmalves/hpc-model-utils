cd {{path}}
mkdir sintetizador-newave
cd sintetizador-newave
python3 -m venv ./venv
./venv/bin/pip install --quiet git+https://github.com/rjmalves/sintetizador-newave.git@{{synthesisAppVersion}}