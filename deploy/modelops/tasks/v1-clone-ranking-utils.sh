cd {{path}}
git clone -c advice.detachedHead=false --quiet --branch {{rankingAppVersion}} https://github.com/rjmalves/ranqueamento-prospectivo-utils.git
cd ranqueamento-prospectivo-utils
python3 -m venv ./venv
venv/bin/pip install --quiet .