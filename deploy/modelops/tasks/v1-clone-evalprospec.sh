cd {{path}}
git clone -c advice.detachedHead=false --quiet --branch {{evalprospecAppVersion}} https://github.com/lkhenayfis/evalprospec.git
cd evalprospec
Rscript -e "renv::restore()"