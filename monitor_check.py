import aiohttp
import asyncio
import re

from click import secho


MONITOR = 'https://copr.fedorainfracloud.org/coprs/g/python/python3.8/monitor/'
BUILDLOG = 'https://copr-be.cloud.fedoraproject.org/results/@python/python3.8/fedora-rawhide-x86_64/{build:08d}-{package}/build.log.gz'
PACKAGE = re.compile(r'<a href="/coprs/g/python/python3.8/package/([^/]+)/">')
BUILD = re.compile(r'<a href="/coprs/g/python/python3.8/build/([^/]+)/">')
RESULT = re.compile(r'<span class="build-([^"]+)"')
TAG = 'f31'
LIMIT = 1000


async def fetch(session, url):
    async with session.get(url) as response:
        return await response.text()


async def length(session, url):
    async with session.head(url) as response:
        return int(response.headers.get('content-length'))


def buildlog_link(package, build):
    return BUILDLOG.format(package=package, build=build)


async def is_retired(package):
    cmd = ('koji', 'list-pkgs', '--show-blocked',
           '--tag', TAG, '--package', package)
    proc = await asyncio.create_subprocess_exec(*cmd,
                                                stdout=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    return b'[BLOCKED]' in stdout


async def process(session, package, build, status):
    if status != 'failed':
        return

    # by querying this all the time, we slow down the koji command
    content_length = await length(session, buildlog_link(package, build))

    # this should be semaphored really, but the above prevents fuckups
    retired = await is_retired(package)

    if retired:
        secho(f'{package} is retired', fg='green')

    fg = 'red' if content_length > LIMIT else 'blue'
    secho(f'{package} failed len={content_length}', fg=fg)


async def main():
    jobs = []

    async with aiohttp.ClientSession() as session:
        # we could stream the content, but meh, get it all, it's not that long
        monitor = await fetch(session, MONITOR)

        package = build = status = None
        lasthit = 'status'

        for line in monitor.splitlines():
            hit = PACKAGE.search(line)
            if hit:
                assert lasthit == 'status'
                lasthit = 'package'
                package = hit.group(1)

            hit = BUILD.search(line)
            if hit:
                assert lasthit == 'package'
                lasthit = 'build'
                build = int(hit.group(1))

            hit = RESULT.search(line)
            if hit:
                assert lasthit == 'build'
                lasthit = 'status'
                status = hit.group(1)
                jobs.append(
                    asyncio.ensure_future(
                        process(session, package, build, status)))

            if 'Possible build states:' in line:
                break

        await asyncio.gather(*jobs)

asyncio.run(main())
