set -o pipefail  # make tee preserve the exit code
echo "${1} ..."
copr edit-package-scm --clone-url https://src.fedoraproject.org/rpms/${1}.git --name ${1} --webhook-rebuild on --commit master @python/python3.8 && echo "${1} OK" || echo "${1} fail"
