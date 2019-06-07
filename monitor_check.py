import aiohttp
import asyncio
import bugzilla
import logging
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
LOGLEVEL = logging.WARNING

EXPLANATION = {
    'red': 'probably FTBFS',
    'blue': 'probably blocked',
    'yellow': 'reported',
    'green': 'retired',
}

logger = logging.getLogger('monitor_check')


def _bugzillas():
    bzapi = bugzilla.Bugzilla(BUGZILLA)
    query = bzapi.build_query(product='Fedora')
    query['blocks'] = TRACKER
    return sorted(bzapi.query(query), key=lambda b: -b.id)


async def bugzillas():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _bugzillas)


async def fetch(session, url, http_semaphore, *, json=False):
    async with http_semaphore:
        logger.debug('fetch %s', url)
        try:
            async with session.get(url) as response:
                if json:
                    return await response.json()
                return await response.text()
        except aiohttp.client_exceptions.ServerDisconnectedError:
            await asyncio.sleep(1)
            return await fetch(session, url, http_semaphore, json=json)


async def length(session, url, http_semaphore):
    async with http_semaphore:
        logger.debug('length %s', url)
        async with session.head(url) as response:
            return int(response.headers.get('content-length'))


def buildlog_link(package, build):
    return BUILDLOG.format(package=package, build=build)


class KojiError (Exception):
    pass


async def is_retired(package, command_semaphore):
    cmd = ('koji', 'list-pkgs', '--show-blocked',
           '--tag', TAG, '--package', package)
    async with command_semaphore:
        try:
            proc = await asyncio.create_subprocess_exec(*cmd,
                                                        stdout=asyncio.subprocess.PIPE)
        except Exception as e:
            raise KojiError(f'Failed to run koji: {e!r}') from None
        stdout, _ = await proc.communicate()
        return b'[BLOCKED]' in stdout


async def is_critpath(session, package, http_semaphore):
    json = await fetch(session, PDC.format(package=package), http_semaphore, json=True)
    for result in json['results']:
        if result['type'] == 'rpm':
            return result['critical_path']
    else:
        print(f'Could not check if {package} is \N{FIRE}', file=sys.stderr)
        return False


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


async def process(session, bugs, package, build, status, http_semaphore, command_semaphore):
    if status != 'failed':
        return

    retired = await is_retired(package, command_semaphore)

    if retired:
        p(f'{package} is retired', fg='green')
        return

    content_length, critpath = await gather_or_cancel(
        length(session, buildlog_link(package, build), http_semaphore),
        is_critpath(session, package, http_semaphore),
    )

    message = f'{package} failed len={content_length}'

    bz = bug(bugs, package)
    if bz:
        message += f' bz{bz.id} {bz.status}'
        fg = 'yellow'

    if not bz or bz.status == "CLOSED":
        fg = 'red' if content_length > LIMIT else 'blue'

    if critpath:
        message += ' \N{FIRE}'
    p(message, fg=fg)


async def gather_or_cancel(*tasks):
    '''
    Like asyncio.gather, but if one task fails, others are cancelled
    '''
    tasks = [t if asyncio.isfuture(t) else asyncio.create_task(t) for t in tasks]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    logging.basicConfig(
        format='%(asctime)s %(name)s %(levelname)s: %(message)s',
        level=LOGLEVEL)

    http_semaphore = asyncio.Semaphore(20)
    command_semaphore = asyncio.Semaphore(10)

    async with aiohttp.ClientSession() as session:
        # we could stream the content, but meh, get it all, it's not that long
        monitor = fetch(session, MONITOR, http_semaphore)
        bugs = bugzillas()
        monitor, bugs = await asyncio.gather(monitor, bugs)

        package = build = status = None
        lasthit = 'status'
        jobs = []

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
                        process(session, bugs, package, build, status, http_semaphore, command_semaphore)))

            if 'Possible build states:' in line:
                break

        try:
            await gather_or_cancel(*jobs)
        except KojiError as e:
            sys.exit(str(e))

        p(file=sys.stderr)
        for fg, count in counter.most_common():
            p(f'There are {count} {fg} lines ({EXPLANATION[fg]})',
              file=sys.stderr, fg=fg)


asyncio.run(main())
