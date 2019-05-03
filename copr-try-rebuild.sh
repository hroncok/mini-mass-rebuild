set -o pipefail  # make tee preserve the exit code
echo ${1}

build=$(grep 'https://copr.fedorainfracloud.org/coprs/build/' ${1}.log | tail -n1 | cut -d/ -f6)
srpm_name=$(grep 'Uploading package' ${1}.log | tail -n1 | cut -d" " -f3)
srpm_link="https://copr-be.cloud.fedoraproject.org/results/@python/python3.8/fedora-rawhide-x86_64/00${build}-${1}/${srpm_name}"

http --headers HEAD "${srpm_link}" | grep "200 OK" && copr build --nowait @python/python3.8 "${srpm_link}" | tee -a ${1}.log
