cd {{path}}
git clone -c advice.detachedHead=false --quiet --branch {{simulprospecAppVersion}} https://github.com/lkhenayfis/simulprospec.git
cd simulprospec
python3 -m venv ./venv
venv/bin/pip install --quiet -r requirements.txt