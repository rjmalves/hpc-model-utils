export PATH=$PATH:@@env:userLocalBin@@
cd {{path}}
git clone -c advice.detachedHead=false --quiet --branch {{uploadCliVersion}} https://github.com/rjmalves/upload-versoes-cli.git
cd upload-versoes-cli
python3 -m venv ./venv
venv/bin/pip install --quiet .