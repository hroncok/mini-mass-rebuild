#!/usr/bin/python3
import json
import subprocess
import sys

pkg = sys.argv[1]


def drop_release(version):
    return '.'.join(version.split('.')[:-1])


print(f'Checking {pkg}')
cmd = 'copr get-package @python/python3.10 --with-all-builds --name'.split()
pkg_detail = json.loads(subprocess.check_output(cmd + [pkg], text=True))

succeeded = [build for build in pkg_detail['builds']
             if build['state'] == 'succeeded'
             and build['project_dirname'] == 'python3.10']

versions = dict((build['id'], drop_release(build['source_package']['version']))
                for build in succeeded)

newest = sorted(versions.keys())[-1]
newest_version = versions[newest]
print(f'Newest build is {newest}, {newest_version}')
del versions[newest]

for buildid, version in versions.items():
    e = subprocess.call(('rpmdev-vercmp', newest_version, version))
    if e in [0, 12]:
        print(f'Will delete {buildid}, {version}')
        subprocess.check_call(('copr', 'delete-build', str(buildid)))

print()
print()
