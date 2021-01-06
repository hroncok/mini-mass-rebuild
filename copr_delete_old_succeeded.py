#!/usr/bin/python3
import json
import subprocess
import sys

import rpm

copr = sys.argv[1]


def drop_dist(version):
    *_, release = parse_evr(version)
    if '.fc' in release:
        return '.'.join(version.split('.')[:-1])
    return version


def parse_evr(evr):
    e, _, vr = evr.rpartition(':')
    if e == '':
        e = None
    v, _, r = vr.rpartition('-')
    return e, v, r


def delete_builds(builds):
    to_delete = [str(i) for i in sorted(builds)]
    print(f'Will delete {", ".join(to_delete)}')
    subprocess.check_call(('copr', 'delete-build', *to_delete))
    builds.clear()
    print()


to_delete = set()


cmd = f'copr list-packages {copr}'.split()
packages = json.loads(subprocess.check_output(cmd, text=True))
packages = [p['name'] for p in packages]


for pkg in packages:
    print(f'Checking {pkg}')
    cmd = f'copr get-package {copr} --with-all-builds --name'.split()
    pkg_detail = json.loads(subprocess.check_output(cmd + [pkg], text=True))

    succeeded = [build for build in pkg_detail['builds']
                 if build['state'] == 'succeeded'
                 and build['project_dirname'] == copr.partition('/')[-1]]

    versions = dict((build['id'], drop_dist(build['source_package']['version']))
                    for build in succeeded)

    newest = sorted(versions.keys())[-1]
    newest_version = versions[newest]
    print(f'Newest {pkg} build is {newest}, {newest_version}')
    del versions[newest]

    for buildid, version in versions.items():
        e = rpm.labelCompare(parse_evr(newest_version), parse_evr(version))
        if e in [0, -1]:
            print(f'Will delete {buildid}, {pkg} {version}')
            to_delete.add(buildid)

    print()

    if len(to_delete) >= 1000:
        delete_builds(to_delete)

if to_delete:
    delete_builds(to_delete)
