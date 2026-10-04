cd {{path}}
mkdir sintetizador-decomp
cd sintetizador-decomp
python3 -m venv ./venv
./venv/bin/pip install --quiet git+https://github.com/rjmalves/sintetizador-decomp.git@{{synthesisAppVersion}}