set -eu
set -o pipefail

pkg="$1"

running_build="$(koji list-builds --quiet --package=$pkg --after='2025-08-12 14:00' --state=BUILDING  --sort-key=build_id | tail -n1 | cut -f1 -d' ' | grep '.fc44$' || true)"
if [[ ! -z "$running_build" ]]; then
  echo "$pkg f44 build running: $running_build" >> ${pkg}.log
  exit 0
fi

complete_build="$(koji list-builds --quiet --package=$pkg --after='2025-08-12 14:00' --state=COMPLETE  --sort-key=build_id | tail -n1 | cut -f1 -d' ' | grep '.fc44$' || true)"
if [[ ! -z "$complete_build" ]]; then
  if ! (koji buildinfo "$complete_build" | grep '^Tags:' | grep -qE ' f44(-updates-candidate)?( |$)'); then
    echo "$pkg f44 build complete but not tagged: $complete_build" >> ${pkg}.log
    exit 0
  fi
fi


fedpkg clone "$pkg" -- --branch rawhide 2>&1 | tee ${pkg}.log
cd "$pkg"

head="$(git rev-parse rawhide)"
f43="$(git rev-parse origin/f43)"


if [[ "$head" == "$f43" ]]; then
  ff="yes"
  running_build="$(koji list-builds --quiet --package=$pkg --after='2025-08-12 14:00' --state=BUILDING  --sort-key=build_id | tail -n1 | cut -f1 -d' ' | grep '.fc43$' || true)"
  if [[ ! -z "$running_build" ]]; then
    echo "$pkg f43 build running: $running_build" >> ../${pkg}.log
    ff="no"
  else
    complete_build="$(koji list-builds --quiet --package=$pkg --after='2025-08-12 14:00' --state=COMPLETE  --sort-key=build_id | tail -n1 | cut -f1 -d' ' | grep '.fc43$' || true)"
    if [[ ! -z "$complete_build" ]]; then
      if ! (koji buildinfo "$complete_build" | grep '^Tags:' | grep -qE ' f43(-updates-candidate)?( |$)'); then
        echo "$pkg f43 build complete but not tagged: $complete_build" >> ../${pkg}.log
        ff="no"
      fi
    fi
  fi
else
  echo "$pkg f43 and rawhide branches differ" >> ../${pkg}.log
  ff="no"
fi

if ! git show --name-only | grep -F "Python 3.14.0rc2"; then
  rpmdev-bumpspec -c "Rebuilt for Python 3.14.0rc2 bytecode" --userstring="Python Maint <python-maint@redhat.com>" *.spec | tee -a ../${pkg}.log
  git commit -am "Rebuilt for Python 3.14.0rc2 bytecode" --author="Python Maint <python-maint@redhat.com>" --allow-empty | tee -a ../${pkg}.log
  git push
  if [[ "$ff" == "yes" ]]; then
    git switch f43
    git merge rawhide
    git push
  fi
fi
fedpkg --release rawhide build --fail-fast --nowait --background 2>&1 | tee -a ../${pkg}.log
if [[ "$ff" == "yes" ]]; then
  fedpkg --release f43 build --fail-fast --nowait --background 2>&1 | tee -a ../${pkg}.log
fi

cd ..
rm -rf "$pkg"
