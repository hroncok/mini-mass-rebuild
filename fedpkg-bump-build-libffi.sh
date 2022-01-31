set -o pipefail  # make tee preserve the exit code
fedpkg clone $1 -- --branch rawhide 2>&1 | tee ./${1}.log || exit $?

cd $1
  if ! git show --name-only | grep -F "https://fedoraproject.org/wiki/Changes/LIBFFI34"; then
    rpmdev-bumpspec -c "Rebuilt for https://fedoraproject.org/wiki/Changes/LIBFFI34" *.spec | tee -a ../${1}.log || exit $?
    git commit -a --allow-empty -m "Rebuilt for https://fedoraproject.org/wiki/Changes/LIBFFI34" | tee -a ../${1}.log
    git push
  fi
  fedpkg build --target=f36-build-side-49318 --fail-fast --nowait 2>&1 | tee -a ../${1}.log
cd -

rm -rf $1
