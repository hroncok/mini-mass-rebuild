import aiohttp
import asyncio
import bugzilla
import concurrent.futures
import re
import sys

from click import secho
from collections import Counter


MONITOR = 'https://copr.fedorainfracloud.org/coprs/g/python/python3.8/monitor/'
BUILDLOG = 'https://copr-be.cloud.fedoraproject.org/results/@python/python3.8/fedora-rawhide-x86_64/{build:08d}-{package}/build.log.gz'
PDC = 'https://pdc.fedoraproject.org/rest_api/v1/component-branches/?name=master&global_component={package}'
PACKAGE = re.compile(r'<a href="/coprs/g/python/python3.8/package/([^/]+)/">')
BUILD = re.compile(r'<a href="/coprs/g/python/python3.8/build/([^/]+)/">')
RESULT = re.compile(r'<span class="build-([^"]+)"')
TAG = 'f31'
LIMIT = 1000
BUGZILLA = 'bugzilla.redhat.com'
TRACKER = 1686977  # PYTHON38

EXPLANATION = {
    'red': 'probably FTBFS',
    'blue': 'probably blocked',
    'yellow': 'reported',
    'green': 'retired',
}


def _bugzillas():
    bzapi = bugzilla.Bugzilla(BUGZILLA)
    query = bzapi.build_query(product='Fedora')
    query['blocks'] = TRACKER
    return sorted(bzapi.query(query), key=lambda b: -b.id)


async def bugzillas():
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _bugzillas)


async def fetch(session, url, *, json=False):
    try:
        async with session.get(url) as response:
            if json:
                return await response.json()
            return await response.text()
    except aiohttp.client_exceptions.ServerDisconnectedError:
        await asyncio.sleep(1)
        return await fetch(session, url, json=json)


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


async def is_critpath(session, package):
    json = await fetch(session, PDC.format(package=package), json=True)
    for result in json['results']:
        if result['type'] == 'rpm':
            return result['critical_path']
    else:
        assert False


def bug(bugs, package):
    for b in bugs:
        if b.component == package:
            return b
    return None


counter = Counter()


def p(*args, **kwargs):
    if 'fg' in kwargs:
        counter[kwargs['fg']] += 1
    secho(*args, **kwargs)


async def process(session, bugs, package, build, status):
    if status != 'failed':
        return

    # by querying this all the time, we slow down the koji command
    content_length, critpath = await asyncio.gather(
        length(session, buildlog_link(package, build)),
        is_critpath(session, package),
    )

    end = ' \N{FIRE}' if critpath else ''

    # this should be semaphored really, but the above prevents fuckups
    retired = await is_retired(package)

    if retired:
        p(f'{package} is retired', fg='green')
        return

    bz = bug(bugs, package)
    if bz and bz.status != "CLOSED":
        p(f'{package} failed len={content_length} bz{bz.id} {bz.status}{end}',
          fg='yellow')
        return

    fg = 'red' if content_length > LIMIT else 'blue'
    if not bz:
        p(f'{package} failed len={content_length}{end}', fg=fg)
    else:
        p(f'{package} failed len={content_length} bz{bz.id} CLOSED{end}',
          fg=fg)


async def main():
    jobs = []

    async with aiohttp.ClientSession() as session:
        # we could stream the content, but meh, get it all, it's not that long
        monitor = fetch(session, MONITOR)
        bugs = bugzillas()
        monitor, bugs = await asyncio.gather(monitor, bugs)

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
                        process(session, bugs, package, build, status)))

            if 'Possible build states:' in line:
                break

        await asyncio.gather(*jobs)

        p(file=sys.stderr)
        for fg, count in counter.most_common():
            p(f'There are {count} {fg} lines ({EXPLANATION[fg]})',
              file=sys.stderr, fg=fg)


asyncio.run(main())
