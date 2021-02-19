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

import dnf
from anytree import Node, RenderTree, findall_by_attr

MONITOR = 'https://copr.fedorainfracloud.org/coprs/g/python/python3.10/monitor/'
INDEX = 'https://copr-be.cloud.fedoraproject.org/results/@python/python3.10/fedora-rawhide-x86_64/{build:08d}-{package}/'  # keep the slash
PDC = 'https://pdc.fedoraproject.org/rest_api/v1/component-branches/?name=rawhide&global_component={package}'
PACKAGE = re.compile(r'<a href="/coprs/g/python/python3.10/package/([^/]+)/">')
BUILD = re.compile(r'<a href="/coprs/g/python/python3.10/build/([^/]+)/">')
RESULT = re.compile(r'<span class="build-([^"]+)"')
RPM_FILE = "<td class='t'>RPM File</td>"
TAG = 'f34'
LIMIT = 1200
BUGZILLA = 'bugzilla.redhat.com'
TRACKER = 1890881  # PYTHON3.10
LOGLEVEL = logging.WARNING

DNF_CACHEDIR = '_dnf_cache_dir'
ARCH = 'x86_64'

EXPLANATION = {
    'red': 'probably FTBFS',
    'blue': 'probably blocked',
    'yellow': 'reported',
    'green': 'retired',
    'cyan': 'excluded from bug filing',
    'magenta': 'copr timeout or repo 404',
}

# FTBS packages for which we don't open bugs (yet)
EXCLUDE = {
    'pyxattr': 'fails in Copr only',
    'mingw-python3': 'pending update to 3.10',
    'gdb': 'problem in gcc, bz1912913',
    'clang': 'problem in gcc, bz1915437',
    'python-uvicorn': 'problem in websockets, bz1914246',
    'python-webassets': 'problem in scss, bz1914347',
    'python-mock': 'missing six BuildRequires - https://src.fedoraproject.org/rpms/python-mock/pull-request/7#',
    'copr-backend': 'problem in setproctitle, bz1919789',
    # meson, https://bugs.python.org/issue43162
    'apostrophe': 'meson',
    'cozy': 'meson',
    'd-feet': 'meson',
    'gedit': 'meson',
    'gitg': 'meson',
    'glade': 'meson',
    'gnome-abrt': 'meson',
    'gnome-builder': 'meson',
    'gnome-feeds': 'meson',
    'gnome-music': 'meson',
    'gnome-passwordsafe': 'meson',
    'gnome-tweaks': 'meson',
    'gobject-introspection': 'meson',
    'gom': 'meson',
    'gplugin': 'meson',
    'gst': 'meson',
    'gst-editing-services': 'meson',
    'gwe': 'meson',
    'hexchat': 'meson',
    'hydrapaper': 'meson',
    'komikku': 'meson',
    'libaccounts-glib': 'meson',
    'libgexiv2': 'meson',
    'libgit2-glib': 'meson',
    'libmodulemd': 'meson',
    'libpeas': 'meson',
    'lollypop': 'meson',
    'meld': 'meson',
    'nemo-extensions': 'meson',
    'piper': 'meson',
    'pitivi': 'meson',
    'pygobject3': 'meson',
    'python-gstreamer1': 'meson',
    'retrace-server': 'meson',
    'revelation': 'meson',
    'setzer': 'meson',
    'signon-glib': 'meson',
    'theme-switcher': 'meson',
    'xapps': 'meson',
}

REASONS = {
    "collections.abc": {
        "regex": r"(ImportError: cannot import name '(.*?)' from 'collections'|AttributeError: module 'collections' has no attribute '(.*?)')",
        # no short_description, uses the matched regex
        "long_description":
        """{MATCH}
        (/usr/lib64/python3.10/collections/__init__.py)

        The deprecated aliases to Collections Abstract Base Classes were removed from the collections module.
        https://docs.python.org/3.10/whatsnew/changelog.html#python-3-10-0-alpha-5
        https://bugs.python.org/issue37324""",
    },
    "segfault": {
        # Segfault detection is quite noisy, especially if we do not want to report it this way. I temporarily disabled it with X in regex.
        "regex": r"XSegmentation fault",
        "long_description": """ DO NOT REPORT THIS """,
        "short_description": """ DO NOT REPORT THIS """,
    },
}

logger = logging.getLogger('monitor_check')


def _bugzillas():
    bzapi = bugzilla.Bugzilla(BUGZILLA)
    query = bzapi.build_query(product='Fedora')
    query['blocks'] = TRACKER
    return [b for b in sorted(bzapi.query(query), key=lambda b: -b.id)
            if b.resolution != 'DUPLICATE']


async def bugzillas():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _bugzillas)


async def fetch(session, url, http_semaphore, *, json=False):
    async with http_semaphore:
        logger.debug('fetch %s', url)
        try:
            async with session.get(url) as response:
                # copr sometimes does not rename the logs
                # https://pagure.io/copr/copr/issue/1648
                if response.status == 404 and url.endswith('.gz'):
                    url = url[:-3]
                    return await fetch(session, url, http_semaphore, json=json)
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


async def is_cmake(session, url, http_semaphore):
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    make = 'No targets specified and no makefile found.' in content
    cmake = '/usr/bin/cmake' in content
    return make and cmake


async def is_blue(session, url, http_semaphore):
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    return 'but none of the providers can be installed' in content


async def is_repo_404(session, url, http_semaphore):
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    return content.count('Failed to download metadata for repo') >= 3


async def is_timeout(session, url, http_semaphore):
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    return 'Copr timeout => sending INT' in content


async def guess_reason(session, url, http_semaphore):
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    for reason in REASONS.values():
        match = re.search(reason["regex"], content)
        if match:
            return {
                "long_description": reason["long_description"].format(MATCH=match.group()),
                "short_description": reason.get("short_description") or match.group(),
            }
    return None

async def guess_missing_dependency(session, package, build, http_semaphore):
    url = builderlive_link(package, build)
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    match = re.search(r"Problem: package (.*?) requires", content)
    if match:
        pkg = source_name(match.group(1))
        if pkg not in missing_dependencies:
            missing_dependencies[pkg] = []
            missing_dependencies[pkg].append(package)
        else:
            if package not in missing_dependencies[pkg]:
                missing_dependencies[pkg].append(package)
    else:
        missing_dependencies['match_failed'].append(package)

def print_dependency_tree():
    root = Node("/")

    for k, v in missing_dependencies.items():
        # insert keys as root children if they are not alredy in tree
        if len(findall_by_attr(root,k)) == 0:
            Node(k, parent=root)

        for child in v:
            # get parent
            existing_parent = findall_by_attr(root, k)
            # add child
            Node(child, parent=existing_parent[0])
            # get all childs with same name
            existing_child = findall_by_attr(root, child)
            # there never should be more than 2 of them
            if len(existing_child) > 1:
                # assign new parent to the older child (one with whole dependency tree)
                existing_child[0].parent=existing_parent[0]
                # remove the other duplicate child
                existing_child[1].parent=None

    for pre, _, node in RenderTree(root):
        print(f"{pre}{node.name}")

    most_common = Counter({k: len(v) for k, v in missing_dependencies.items()}).most_common(10)
    print("Top 10 of blockers (match_failed contains packages that could not be parsed):", file=sys.stderr)
    for pkg, count in most_common:
        print(pkg + ": " + str(count), file=sys.stderr)

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


def rootlog_link(package, build):
    return index_link(package, build) + 'root.log.gz'


def builderlive_link(package, build):
    return index_link(package, build) + 'builder-live.log.gz'


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

def pkgname(nevra):
    return nevra.rsplit("-", 2)[0]

def source_name(nevra):
    pkgs = repoquery(pkgname(nevra))
    for pkg in pkgs:  # a only gets evaluated here
    #    if pkg.reponame == "fedorarawhide":
        return pkg.source_name
    raise RuntimeError(f"Cannot find source for {name}. Hint: Remove the cache in {DNF_CACHEDIR}")

def rawhide_sack():
    """A DNF sack for rawhide, used for queries, cached"""
    base = dnf.Base()
    conf = base.conf
    conf.cachedir = DNF_CACHEDIR
    conf.substitutions['basearch'] = ARCH
    base.repos.add_new_repo('rawhide', conf,
        baseurl=['http://kojipkgs.fedoraproject.org/repos/rawhide/latest/$basearch/'],
        skip_if_unavailable=False,
        enabled=True)
    base.fill_sack(load_system_repo=False, load_available_repos=True)
    return base.sack

RAWHIDE_SACK = rawhide_sack()

def repoquery(name):
    return RAWHIDE_SACK.query().filter(name=name, latest=1).run()

def p(*args, **kwargs):
    if 'fg' in kwargs:
        counter[kwargs['fg']] += 1
    secho(*args, **kwargs)


async def process(
    session, bugs, package, build, status, http_semaphore, command_semaphore,
    *, browser_lock=None, with_reason=None, blues_file=None, magentas_file=None
):
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

    longlog = content_length > LIMIT

    if longlog and await is_blue(session, rootlog_link(package, build), http_semaphore):
        longlog = False

    repo_404 = False
    if await is_repo_404(session, rootlog_link(package, build), http_semaphore):
        longlog = True
        repo_404 = True

    if blues_file and not longlog:
        print(package, file=blues_file)
        await guess_missing_dependency(session, package, build, http_semaphore)

    bz = None
    if package in EXCLUDE:
        fg = 'cyan'
        message += f' (excluded: {EXCLUDE[package]})'
    elif repo_404:
        fg = 'magenta'
        message += ' (repo 404)'
        if magentas_file:
            print(package, file=magentas_file)
    else:
        bz = bug(bugs, package)
        if bz:
            message += f' bz{bz.id} {bz.status}'
            fg = 'yellow'

        if not bz or bz.status == "CLOSED":
            fg = 'red' if longlog else 'blue'

    if fg == 'red':
        if await is_timeout(session, builderlive_link(package, build), http_semaphore):
            message += ' (copr timeout)'
            fg = 'magenta'

    if critpath:
        message += ' \N{FIRE}'
    p(message, fg=fg)

    if (
        browser_lock
        and (not bz or bz.status == "CLOSED")
        and (longlog)
        and (str(package) not in EXCLUDE)
        and (fg != 'magenta')
    ):
        if not await failed_but_built(session, index_link(package, build), http_semaphore):
            reason = await guess_reason(session, builderlive_link(package, build), http_semaphore)
            if with_reason and not reason:
                return
            await open_bz(package, build, status, browser_lock, reason)


async def open_bz(package, build, status, browser_lock, reason=None):
    if reason == None:
        # General message for packages opened with --without-reason
        reason = {
            "long_description": "This report is automated and not very verbose, but we'll try to get back here with details.",
            "short_description": "",
        }
    summary = f"{package} fails to build with Python 3.10: {reason['short_description']}"

    description = dedent(f"""
        {package} fails to build with Python 3.10.0a5.

        {reason['long_description']}

        For the build logs, see:
        https://copr-be.cloud.fedoraproject.org/results/@python/python3.10/fedora-rawhide-x86_64/{build:08}-{package}/

        For all our attempts to build {package} with Python 3.10, see:
        https://copr.fedorainfracloud.org/coprs/g/python/python3.10/package/{package}/

        Testing and mass rebuild of packages is happening in copr. You can follow these instructions to test locally in mock if your package builds with Python 3.10:
        https://copr.fedorainfracloud.org/coprs/g/python/python3.10/

        Let us know here if you have any questions.

        Python 3.10 will be included in Fedora 35. To make that update smoother, we're building Fedora packages with early pre-releases of Python 3.10.
        A build failure prevents us from testing all dependent packages (transitive [Build]Requires), so if this package is required a lot, it's important for us to get it fixed soon.
        We'd appreciate help from the people who know this package best, but if you don't want to work on this now, let us know so we can try to work around it on our side.
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
        'cc': 'mhroncok@redhat.com,thrnciar@redhat.com'
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

missing_dependencies = {
    'match_failed': []
}

async def main(pkgs=None, open_bug_reports=False, with_reason=False, blues_file=None, magentas_file=None, dependency_tree=None):
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
                    browser_lock=browser_lock, with_reason=with_reason,
                    blues_file=blues_file, magentas_file=magentas_file
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

        if dependency_tree:
            print_dependency_tree()

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
    '--with-reason/--without-reason',
    help='Use in combination with "--open-bug-reports",'
        + 'to open bug if reason was guessed'
)
@click.option(
    '--blues-file',
    type=click.File('w'),
    help='Dump blue-ish packages to a given file'
)
@click.option(
    '--magentas-file',
    type=click.File('w'),
    help='Dump magent-ish packages to a given file'
)
@click.option(
    '--dependency-tree/--no-dependency-tree',
    help='Show dependency tree of blue packages'
)
def run(pkgs, open_bug_reports, with_reason=None, blues_file=None, magentas_file=None, dependency_tree=None):
    asyncio.run(main(pkgs, open_bug_reports, with_reason, blues_file, magentas_file, dependency_tree))

if __name__ == '__main__':
    run()
