#!/usr/bin/python3
import asyncio
import json
import sys

import rpm

BATCH_SIZE = 1000

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


async def proc_output(*cmd, check=True):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE)

    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError('subprocess failed')

    return stdout.decode('utf-8')


async def process_package(pkg, to_delete, command_semaphore):
    async with command_semaphore:
        print(f'Checking {pkg}')
        pkg_detail = json.loads(await proc_output('copr', 'get-package', copr,
                                                  '--with-all-builds', '--name', pkg))

        succeeded = [build for build in pkg_detail['builds']
                     if build['state'] == 'succeeded'
                     and build['project_dirname'] == copr.partition('/')[-1]]

        if not succeeded:
            print()
            return

        versions = dict((build['id'], drop_dist(build['source_package']['version']))
                        for build in succeeded)

        newest = sorted(versions.keys())[-1]
        newest_version = versions[newest]
        print(f'Newest {pkg} build is {newest}, {newest_version}')
        del versions[newest]

        for buildid, version in versions.items():
            e = rpm.labelCompare(parse_evr(newest_version), parse_evr(version))
            if e in [0, -1]:
                to_delete.add(buildid)
                print(f'Will delete {buildid}, '
                      f'{pkg} {version} ({len(to_delete)}/{BATCH_SIZE})')

        print()

        await delete_builds(to_delete)


async def delete_builds(builds, *, force=False):
    if not force and len(builds) < BATCH_SIZE:
        return
    to_delete = [str(i) for i in sorted(builds)]
    builds.clear()
    proc = await asyncio.create_subprocess_exec('copr', 'delete-build', *to_delete)
    await proc.wait()


async def gather_or_cancel(*tasks):
    '''
    Like asyncio.gather, but if one task fails, others are cancelled
    '''
    try:
        return await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    packages = json.loads(await proc_output('copr', 'list-packages', copr))
    packages = [p['name'] for p in packages]

    command_semaphore = asyncio.Semaphore(8)
    to_delete = set()
    tasks = []

    for pkg in packages:
        task = asyncio.create_task(process_package(pkg, to_delete, command_semaphore))
        tasks.append(task)

    try:
        await gather_or_cancel(*tasks)
    finally:
        if to_delete:
            await delete_builds(to_delete, force=True)


if __name__ == '__main__':
    asyncio.run(main())
