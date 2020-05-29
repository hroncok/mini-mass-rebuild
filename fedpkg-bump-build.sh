set -o pipefail  # make tee preserve the exit code
fedpkg clone $1  2>&1 | tee ./${1}.log || exit $?

cd $1
  if ! git show --name-only | grep -F "Python 3.9"; then
    rpmdev-bumpspec -c "Rebuilt for Python 3.9" *.spec | tee -a ../${1}.log
    git commit *.spec -m "Rebuilt for Python 3.9" | tee -a ../${1}.log
    git push
  fi
  fedpkg build --fail-fast --nowait --background 2>&1 | tee -a ../${1}.log
cd -

rm -rf $1
