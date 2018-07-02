set -o pipefail  # make tee preserve the exit code
fedpkg clone $1  2>&1 | tee ./${1}.log || exit $?

cd $1
  #rpmdev-bumpspec -c "Rebuilt for Python 3.7" *.spec | tee -a ../${1}.log
  #git commit *.spec -m "Rebuilt for Python 3.7" | tee -a ../${1}.log
  #git push
  fedpkg build --nowait --background 2>&1 | tee -a ../${1}.log
cd -

rm -rf $1
