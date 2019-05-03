set -o pipefail  # make tee preserve the exit code
fedpkg clone $1  2>&1 | tee ./${1}.log || exit $?

cd $1
  rpmdev-bumpspec -c "Rebuilt for Python 3.8" *.spec | tee -a ../${1}.log
  fedpkg srpm | tee -a ../${1}.log
  # get API token from https://copr.fedorainfracloud.org/api
  copr build --nowait @python/python3.8 *.src.rpm | tee -a ../${1}.log
cd -

rm -rf $1
