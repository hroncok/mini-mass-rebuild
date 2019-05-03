set -o pipefail  # make tee preserve the exit code

build=$(grep 'https://copr.fedorainfracloud.org/coprs/build/' ${1}.log | tail -n1 | cut -d/ -f6)
buildlog_link="https://copr-be.cloud.fedoraproject.org/results/@python/python3.8/fedora-rawhide-x86_64/00${build}-${1}/build.log.gz"

echo ${1}$(http --headers HEAD "${buildlog_link}" | grep Content-Length | cut -d: -f2)
