set -o pipefail  # make tee preserve the exit code
fedpkg clone $1  2>&1 | tee ./${1}.log || exit $?

cd $1
  fedpkg switch-branch epel7 | tee -a ../${1}.log
  rpmdev-bumpspec -c "Rebuilt for Python 3.4 <-> 3.6 switch" *.spec | tee -a ../${1}.log
  # DANGER. When fedpkg srpm python3-xxx on Fedora, you'll get:
  #   %package -n python3-xxx: package python3-xxx already exists
  # Workaround this by placing:
  #   %python3_pkgversion 36
  #   %python3_other_pkgversion 34
  # into your .rpmmacros file
  # Don't forget to remove this later if you build packages locally.
  fedpkg srpm | tee -a ../${1}.log
  # get API token from https://copr.fedorainfracloud.org/api
  copr build --nowait @python/epel-python3 *.src.rpm | tee -a ../${1}.log
cd -

rm -rf $1
