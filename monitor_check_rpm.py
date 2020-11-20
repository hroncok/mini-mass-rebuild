import aiohttp
import asyncio
import bugzilla
import logging
import re
import sys
from urllib.parse import urlencode, quote, unquote
from textwrap import dedent
import webbrowser

import click
from click import secho
from collections import Counter


MONITOR = 'https://copr.fedorainfracloud.org/coprs/churchyard/rpm-exclude-change/monitor/'
INDEX = 'https://copr-be.cloud.fedoraproject.org/results/churchyard/rpm-exclude-change/fedora-rawhide-x86_64/{build:08d}-{package}/'  # keep the slash
PDC = 'https://pdc.fedoraproject.org/rest_api/v1/component-branches/?name=master&global_component={package}'
PACKAGE = re.compile(r'<a href="/coprs/churchyard/rpm-exclude-change/package/([^/]+)/">')
BUILD = re.compile(r'<a href="/coprs/churchyard/rpm-exclude-change/build/([^/]+)/">')
RESULT = re.compile(r'<span class="build-([^"]+)"')
RPM_FILE = "<td class='t'>RPM File</td>"
TAG = 'f34'
LIMIT = 1200
BUGZILLA = 'bugzilla.redhat.com'
TRACKER = 1868278  # F34FTBFS
LOGLEVEL = logging.WARNING

EXPLANATION = {
    'red': 'probably affected by %exclude',
    'blue': 'probably not affected by %exclude',
    'yellow': 'reported FTBFS bug',
    'green': 'retired package',
    'cyan': 'excluded from bug filing',
}

# FTBS packages for which we don't open bugs (yet)
EXCLUDE = {
    'pyxattr': 'fails in Copr only',
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
                return await response.text('utf-8')
        except aiohttp.client_exceptions.ServerDisconnectedError:
            await asyncio.sleep(1)
            return await fetch(session, url, http_semaphore, json=json)


async def length(session, url, http_semaphore):
    async with http_semaphore:
        logger.debug('length %s', url)
        async with session.head(url) as response:
            return int(response.headers.get('content-length'))


async def has_unpackaged_files(session, url, http_semaphore):
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    return 'error: Installed (but unpackaged) file(s) found:' in content


async def failed_but_built(session, url, http_semaphore):
    """
    Sometimes, the package actually built, but is only marked as failed:
    https://pagure.io/copr/copr/issue/1209

    The build.log would be long, so we would attempt to open bugzillas.
    Here we get the index page of the results directory and we determine that:

     - failed builds only have 1 SRPM
     - succeeded builds have 1 SRPM and at least 1 built RPM
    """
    async with http_semaphore:
        logger.debug('failed_but_built %s', url)
        async with session.get(url) as response:
            text = await response.text()
            rpm_count = text.count(RPM_FILE)
            if rpm_count > 1:
                with open('failed_but_built.lst', 'a') as f:
                    print(url, file=f)
                return True
            return False


def index_link(package, build):
    return INDEX.format(package=package, build=build)


def buildlog_link(package, build):
    return index_link(package, build) + 'build.log.gz'


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
    try:
        json = await fetch(session, PDC.format(package=quote(package)), http_semaphore, json=True)
        for result in json['results']:
            if result['type'] == 'rpm':
                return result['critical_path']
        else:
            raise ValueError()
    except (aiohttp.ContentTypeError, ValueError):
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


async def process(
    session, bugs, package, build, status, http_semaphore, command_semaphore,
    *, browser_lock=None, blues_file=None
):
    if status != 'failed':
        return

    retired = await is_retired(package, command_semaphore)

    if retired:
        p(f'{package} is retired', fg='green')
        return

    critpath = await is_critpath(session, package, http_semaphore)

    message = f'{package} failed'

    unpackaged = await has_unpackaged_files(session, buildlog_link(package, build), http_semaphore)

    if package in EXCLUDE:
        bz = None
        fg = 'cyan'
        message += f' (excluded: {EXCLUDE[package]})'
    else:
        bz = bug(bugs, package)
        if bz:
            message += f' bz{bz.id} {bz.status}'
            fg = 'yellow'

        if not bz or bz.status == "CLOSED":
            if unpackaged:
                fg = 'red'
                message += ' with unpackaged files'
            else:
                fg = 'blue'

    if critpath:
        message += ' \N{FIRE}'
    p(message, fg=fg)

    if (
        browser_lock
        and (not bz or bz.status == "CLOSED")
        and (str(package) not in EXCLUDE)
    ):
        if not await failed_but_built(session, index_link(package, build), http_semaphore):
            await open_bz(package, build, status, browser_lock)


async def open_bz(package, build, status, browser_lock):
    summary = f"{package} fails to build with ..."

    description = dedent(f"""
        {package} fails to build with ...
    """)

    url_prefix = 'https://bugzilla.redhat.com/enter_bug.cgi?'
    params = {
        'short_desc': summary,
        'comment': description,
        'component': str(package),
        'blocked': TRACKER,
        'product': 'Fedora',
        'version': 'rawhide',
        #'bug_severity': 'high',
        #'cc': 'mhroncok@redhat.com,thrnciar@redhat.com'
    }

    # Rate-limit opening browser tabs
    async with browser_lock:
        webbrowser.open(url_prefix + urlencode(params))
        await asyncio.sleep(1)


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


async def main(pkgs=None, open_bug_reports=False, blues_file=None):
    logging.basicConfig(
        format='%(asctime)s %(name)s %(levelname)s: %(message)s',
        level=LOGLEVEL)

    http_semaphore = asyncio.Semaphore(20)
    command_semaphore = asyncio.Semaphore(10)

    # A lock to rate-limit opening browser tabs. If None, tabs aren't opened.
    if open_bug_reports:
        browser_lock = asyncio.Lock()
    else:
        browser_lock = None

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
                package = unquote(hit.group(1))

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
                if pkgs and package not in pkgs:
                    continue
                jobs.append(asyncio.ensure_future(process(
                    session, bugs, package, build, status,
                    http_semaphore, command_semaphore,
                    browser_lock=browser_lock,
                    blues_file=blues_file
                )))

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


@click.command()
@click.argument(
    'pkgs',
    nargs=-1,
)
@click.option(
    '--open-bug-reports/--no-open-bug-reports',
    help='Open a browser page (!) with a bug report template for each '
        + 'package that seems to need a bug report'
)
@click.option(
    '--blues-file',
    type=click.File('w'),
    help='Dump blue-ish packages to a given file'
)
def run(pkgs, open_bug_reports, blues_file=None):
    asyncio.run(main(pkgs, open_bug_reports, blues_file))

if __name__ == '__main__':
    run()
