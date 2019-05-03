set -o pipefail  # make tee preserve the exit code
fedpkg clone $1  2>&1 | tee ./${1}.log || exit $?

cd $1
  fedpkg switch-branch epel7 | tee -a ../${1}.log
cd -

grep python_provide ${1}/${1}.spec || rm -rf $1
