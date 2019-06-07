import aiohttp
import asyncio
import bugzilla
import concurrent.futures
import re
import sys
from urllib.parse import urlencode
from textwrap import dedent
import webbrowser

import click
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
    'cyan': 'excluded from bug filing',
}

# FTBS packages for which we don't open bugs (yet)
EXCLUDE = {
    'brltty': 'filed in alsa',
    'libtevent': 'filed in samba',
    'libtalloc': 'filed in samba',
    'libldb': 'filed in samba',
    'python-parallel-ssh': 'filed elsewhere (???)',
    'python-bashate': 'filed in python-oslo-sphinx, bz 1705932',
    'pysvn': 'filed in python-pycxx, bz 1718318',
    'python-sanic': 'filed in python-uvloop, bz 1718390',
    'gnucash': 'declaration-after-statement',
    'onboard': 'declaration-after-statement',
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


async def process(session, bugs, package, build, status, *, browser_lock=None):
    if status != 'failed':
        return

    # by querying this all the time, we slow down the koji command
    content_length, critpath = await asyncio.gather(
        length(session, buildlog_link(package, build)),
        is_critpath(session, package),
    )

    # this should be semaphored really, but the above prevents fuckups
    retired = await is_retired(package)

    if retired:
        p(f'{package} is retired', fg='green')
        return

    message = f'{package} failed len={content_length}'

    if package in EXCLUDE:
        bz = None
        fg = 'cyan'
        message += f'(excluded: {EXCLUDE[package]})'
    else:
        bz = bug(bugs, package)
        if bz:
            message += f' bz{bz.id} {bz.status}'
            fg = 'yellow'

        if not bz or bz.status == "CLOSED":
            fg = 'red' if content_length > LIMIT else 'blue'

    if critpath:
        message += ' \N{FIRE}'
    p(message, fg=fg)

    if (
        browser_lock
        and (not bz or bz.status == "CLOSED")
        and (content_length > LIMIT)
        and (str(package) not in EXCLUDE)
    ):
        await open_bz(package, build, status, browser_lock)


async def open_bz(package, build, status, browser_lock):
    summary = f"{package} fails to build with Python 3.8"

    description = dedent(f"""
        {package} fails to build with Python 3.8.0b1.

        This report is automated and not very verbose, but we'll try to get back here with details.

        For the build logs, see:
        https://copr-be.cloud.fedoraproject.org/results/@python/python3.8/fedora-rawhide-x86_64/{build:08}-{package}/

        For all our attempts to build {package} with Python 3.8, see:
        https://copr.fedorainfracloud.org/coprs/g/python/python3.8/package/{package}/

        Testing and mass rebuild of packages is happening in copr. You can follow these instructions to test locally in mock if your package builds with Python 3.8:
        https://copr.fedorainfracloud.org/coprs/g/python/python3.8/

        Let us know here if you have any questions.
    """)

    url_prefix = 'https://bugzilla.redhat.com/enter_bug.cgi?'
    params = {
        'short_desc': summary,
        'comment': description,
        'component': str(package),
        'blocked': 'PYTHON38',
        'product': 'Fedora',
        'version': 'rawhide',
        'bug_severity': 'high',
    }

    # Rate-limit opening browser tabs
    async with browser_lock:
        webbrowser.open(url_prefix + urlencode(params))
        await asyncio.sleep(1)


async def main(open_bug_reports=False):

    # A lock to rate-limit opening browser tabs. If None, tabs aren't opened.
    if open_bug_reports:
        browser_lock = asyncio.Lock()
    else:
        browser_lock = None

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
                        process(session, bugs, package, build, status,
                                browser_lock=browser_lock)))

            if 'Possible build states:' in line:
                break

        await asyncio.gather(*jobs)

        p(file=sys.stderr)
        for fg, count in counter.most_common():
            p(f'There are {count} {fg} lines ({EXPLANATION[fg]})',
              file=sys.stderr, fg=fg)


@click.command()
@click.option(
    '--open-bug-reports/--no-open-bug-reports',
    help='Open a browser page (!) with a bug report template for each '
        + 'package that seems to need a bug report'
)
def run(open_bug_reports):
    asyncio.run(main(open_bug_reports))

if __name__ == '__main__':
    run()
