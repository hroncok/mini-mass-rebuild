import aiohttp
import asyncio
import bugzilla
import logging
import re
import sys
from urllib.parse import urlencode
from textwrap import dedent
import webbrowser

import click
from click import secho
from collections import Counter

import dnf
from anytree import Node, RenderTree, findall_by_attr, LoopError

from copr.v3 import Client

COPR = '@python', 'python3.14'
COPR_STR = '{}/{}'.format(*COPR)
COPR_STR_G = '{}/{}'.format(COPR[0].replace('@', 'g/'), COPR[1])

MONITOR = f'https://copr.fedorainfracloud.org/coprs/{COPR_STR_G}/monitor/'
INDEX = f'https://copr-be.cloud.fedoraproject.org/results/{COPR_STR}/fedora-rawhide-x86_64/{{build:08d}}-{{package}}/'  # keep the slash
CRITPATH_COMPONENTS = 'https://bodhi.fedoraproject.org/get_critpath_components'
PACKAGE = re.compile(fr'<a href="/coprs/{COPR_STR_G}/package/([^/]+)/">')
BUILD = re.compile(fr'<a href="/coprs/{COPR_STR_G}/build/([^/]+)/">')
RESULT = re.compile(r'<span class="build-([^"]+)"')
RPM_FILE = "<td class='t'>RPM File</td>"
TAG = 'f43'
KOSCHEI = f"https://koschei.fedoraproject.org/api/v1/packages?name={{package}}&collection={TAG}"
# copr bug: build.log isn't properly populated
# TODO: rework to use builder-live.log.gz or wait for https://github.com/fedora-copr/copr/issues/2961
LIMIT = 30
BUGZILLA = 'bugzilla.redhat.com'
BZ_PAGE_SIZE = 20
TRACKER = 2322407  # PYTHON3.14
RAWHIDE = 2339432  # F43FTBFS
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
    'white': 'FTBFS in both Copr and Koschei',
}

# FTBS packages for which we don't open bugs (yet)
EXCLUDE = {
    "python-apsw": "version is set by the set of if-clauses in the spec",
    "python-boto3": "built together with botocore, always fails at the first run",
    "python-pexpect": "very flaky",
}

REASONS = {
    "ast": {
        "regex": r"AttributeError: ('Constant' object has no attribute '(s|n)'|module 'ast' has no attribute '(.*)')",
        "long_description": """
        According to: https://docs.python.org/dev/whatsnew/3.14.html#id2

        Remove the following classes. They were all deprecated since Python 3.8, and have emitted deprecation warnings since Python 3.12:
        ast.Bytes
        ast.Ellipsis
        ast.NameConstant
        ast.Num
        ast.Str

        Use ast.Constant instead. As a consequence of these removals, user-defined visit_Num, visit_Str, visit_Bytes, visit_NameConstant and visit_Ellipsis methods on custom ast.NodeVisitor subclasses will no longer be called when the NodeVisitor subclass is visiting an AST. Define a visit_Constant method instead.

        Also, remove the following deprecated properties on ast.Constant, which were present for compatibility with the now-removed AST classes:
        ast.Constant.n
        ast.Constant.s

        Use ast.Constant.value instead.
        (Contributed by Alex Waygood in gh-119562.)
         """,
        "short_description": "",
    },
    "ByteString": {
        "regex": r"(ImportError: cannot import name 'ByteString' from '(.*)'|AttributeError: module 'typing' has no attribute 'ByteString')",
        "long_description": """
        According to https://docs.python.org/dev/whatsnew/3.14.html#typing

        ByteString has been removed from both typing and collections.abc modules.
        It had previously raised a DeprecationWarning since Python 3.12.
         """,
        "short_description": "",
    },
    "pickle": {
        "regex": r"_pickle.PicklingError: Can't pickle local object (.*)",
        "long_description": """
        According to https://docs.python.org/dev/whatsnew/3.14.html#multiprocessing

        The default start method (see Contexts and start methods) changed from fork to forkserver on platforms other than macOS & Windows where it was already spawn. If you require the threading incompatible fork start method you must explicitly request it using a context from multiprocessing.get_context() (preferred) or change the default via multiprocessing.set_start_method(). (Contributed by Gregory P. Smith in gh-84559.)
         """,
        "short_description": "",
    },
    "pkgutil": {
        "regex": r"AttributeError: module 'pkgutil' has no attribute '(get_loader|find_loader)'",
        "long_description": """
        According to https://docs.python.org/dev/whatsnew/3.14.html#pkgutil

        Remove deprecated pkgutil.get_loader() and pkgutil.find_loader(). These had previously raised a DeprecationWarning since Python 3.12. (Contributed by Bénédikt Tran in gh-97850.)
         """,
        "short_description": "",
    },
    "eventloop": {
        "regex": r"RuntimeError: There is no current event loop in thread 'MainThread'.",
        "long_description": """
        According to https://docs.python.org/dev/whatsnew/3.14.html#id3

        Removed implicit creation of event loop by asyncio.get_event_loop(). It now raises a RuntimeError if there is no current event loop. (Contributed by Kumar Aditya in gh-126353.)
         """,
        "short_description": "",
    },
    "set_event_loop": {
        "regex": r"DeprecationWarning: 'asyncio.(.*)' is deprecated and slated for removal in Python 3.16",
        "long_description": """
        According to https://docs.python.org/dev/whatsnew/3.14.html#id3

        asyncio policy system is deprecated and will be removed in Python 3.16. In particular, the following classes and functions are deprecated:
        asyncio.AbstractEventLoopPolicy
        asyncio.DefaultEventLoopPolicy
        asyncio.WindowsSelectorEventLoopPolicy
        asyncio.WindowsProactorEventLoopPolicy
        asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy()
        asyncio.set_event_loop()
        Users should use asyncio.run() or asyncio.Runner with loop_factory to use the desired event loop implementation.)
        """,
        "short_description": "",
    },
    "segfault": {
        # Segfault detection is quite noisy, especially if we do not want to report it this way. I temporarily disabled it with X in regex.
        "regex": r"XSegmentation fault",
        "long_description": """ DO NOT REPORT THIS """,
        "short_description": """ DO NOT REPORT THIS """,
    },
    "h5py_import_error": {
        "regex": r"ValueError: chr() arg not in range(0x110000)",
        "long_description": """ DO NOT REPORT THIS """,
        "short_description": """ DO NOT REPORT THIS """,
    },
    "exit_in_finally": {
        "regex": r"SyntaxWarning: '(return|break|continue)' in a 'finally' block",
        "long_description": """
        PEP 765: Disallow return/break/continue that exit a finally block
        The compiler emits a SyntaxWarning when a return, break or continue statements appears where it exits a finally block.
        This change is specified in PEP 765: https://peps.python.org/pep-0765/. """,
        "short_description": "",
    },
}

logger = logging.getLogger('monitor_check')

BZAPI = bugzilla.Bugzilla(BUGZILLA)

def _bugzillas():
    query = BZAPI.build_query(product='Fedora')
    query['blocks'] = [TRACKER, RAWHIDE]
    query['limit'] = BZ_PAGE_SIZE
    query['offset'] = 0
    results = []
    while len(partial := BZAPI.query(query)) == BZ_PAGE_SIZE:
        results += partial
        query['offset'] += BZ_PAGE_SIZE
    results += partial
    return [b for b in sorted(results, key=lambda b: -b.id)
            if b.resolution != 'DUPLICATE']


async def bugzillas():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _bugzillas)

def _copr():
    client = Client.create_from_config_file()
    packages = client.package_proxy.get_list(ownername=COPR[0], projectname=COPR[1], with_latest_build=True)
    return packages

async def copr():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _copr)

async def fetch(session, url, http_semaphore, *, json=False):
    retry = False
    async with http_semaphore:
        logger.debug('fetch %s', url)
        try:
            async with session.get(url) as response:
                # copr sometimes does not rename the logs
                # https://pagure.io/copr/copr/issue/1648
                if response.status == 404 and url.endswith('.gz'):
                    url = url[:-3]
                    retry = True
                elif json:
                    return await response.json()
                else:
                    return await response.text('utf-8')
        except (aiohttp.client_exceptions.ServerDisconnectedError, aiohttp.client_exceptions.ClientConnectorError):
            await asyncio.sleep(1)
            retry = True
    if retry:
        return await fetch(session, url, http_semaphore, json=json)


async def length(session, url, http_semaphore):
    retry = False
    async with http_semaphore:
        logger.debug('length %s', url)
        try:
            async with session.head(url) as response:
                return int(response.headers.get('content-length'))
        except (aiohttp.client_exceptions.ClientConnectorError, aiohttp.client_exceptions.ServerDisconnectedError):
            await asyncio.sleep(1)
            retry = True
    if retry:
        return await length(session, url, http_semaphore)

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
    # there can be multiple ways to state a missing dep
    return (
        "but none of the providers can be installed" in content
        or "cannot install the best candidate for the job" in content
    )


async def is_white(session, package, http_semaphore):
    url = KOSCHEI.format(package=package)
    try:
        content = await fetch(session, url, http_semaphore, json=True)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    if content:
        return content[0]['state'] == "failing"
    return False


async def is_repo_404(session, url, http_semaphore):
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    return content.count('Librepo error: Yum repo downloading error') >= 3


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

async def guess_missing_dependency(session, package, build, http_semaphore, fg, bugs):
    url = builderlive_link(package, build)
    try:
        content = await fetch(session, url, http_semaphore)
    except aiohttp.client_exceptions.ClientPayloadError:
        logger.debug('broken content %s', url)
        return False
    patterns = [
        r"Problem.*?: package (.*?) requires python\(abi\) = 3\.13",
        r"package (.*?) requires .*?, but none of the providers can be installed",
        r"Status code: (.*?) for",
    ]
    if fg == 'yellow':
        yellow_pkgs.append(package)
    elif fg == 'blue':
        blue_pkgs.append(package)
    match_found = False
    for pattern in patterns:
        match = re.findall(pattern, content)
        if match:
            match_found == True
            match = list(set(match))
            for broken_pkg in match:
                broken_srpm = "404"
                if broken_pkg != "404":
                    broken_srpm = source_name(broken_pkg)
                if broken_srpm not in missing_dependencies:
                    missing_dependencies[broken_srpm] = []
                    missing_dependencies[broken_srpm].append(package)
                    # Uncomment this if you want to set bugzilla blockers
                    # bugzilla_set_blockers(bugs, broken_srpm, package)
                else:
                    if package not in missing_dependencies[broken_srpm]:
                        missing_dependencies[broken_srpm].append(package)
                        # Uncomment this if you want to set bugzilla blockers
                        # bugzilla_set_blockers(bugs, broken_srpm, package)
    if not match_found:
        missing_dependencies['match_failed'].append(package)

def bugzilla_set_blockers(bugs, broken_srpm, package):
    parent_bugzilla = bug_opened(bugs, broken_srpm)
    child_bugzilla = bug_opened(bugs, package)
    if parent_bugzilla and child_bugzilla:
        if child_bugzilla.id not in parent_bugzilla.blocks:
            bz_update = BZAPI.build_update(blocks_add=child_bugzilla.id)
            BZAPI.update_bugs([parent_bugzilla.id], bz_update)
            print(f"Bugzilla updated, {child_bugzilla.component} {child_bugzilla.id} now depends on {parent_bugzilla.component} {parent_bugzilla.id}")
        else:
            print(f"Bugzilla already blocked, {child_bugzilla.component} {child_bugzilla.id} depends on {parent_bugzilla.component} {parent_bugzilla.id}")

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
                try:
                    # assign new parent to the older child (one with whole dependency tree)
                    existing_child[0].parent=existing_parent[0]
                except LoopError as e:
                    print("Possible circular dependency.", file=sys.stderr)
                    print(str(e), file=sys.stderr)
                # remove the other duplicate child
                existing_child[1].parent=None

    for pre, _, node in RenderTree(root):
        if node.name in yellow_pkgs:
            p(f"{pre}{node.name}", fg="yellow")
        elif node.name in blue_pkgs:
            p(f"{pre}{node.name}", fg="blue")
        else:
            p(f"{pre}{node.name}", fg="green")


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
        retry = False
        try:
            async with session.get(url) as response:
                text = await response.text()
                rpm_count = text.count(RPM_FILE)
                if rpm_count > 1:
                    with open('failed_but_built.lst', 'a') as f:
                        print(url, file=f)
                    return True
                return False
        except (aiohttp.client_exceptions.ClientConnectorError, aiohttp.client_exceptions.ServerDisconnectedError):
            await asyncio.sleep(1)
            retry = True
    if retry:
        return await failed_but_built(session, url, http_semaphore)


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


def is_critpath(package, critpath_pkgs):
    for critpath_name, packages in critpath_pkgs.items():
        if package in packages:
            return True
    else:
        return False


def bug(bugs, package):
    for b in bugs:
        if b.component == package:
            return b
    return None


def bug_opened(bugs, package):
    for b in bugs:
        if b.component == package and b.status != "CLOSED":
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
    raise RuntimeError(f"Cannot find source for {pkgname(nevra)}. Hint: Remove the cache in {DNF_CACHEDIR}")

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
    session, bugs, package, build, status, http_semaphore, command_semaphore, critpath_pkgs,
    *, browser_lock=None, with_reason=None, blues_file=None, magentas_file=None,
    greens_file=None, dependency_tree=False,
):
    if status != 'failed':
        return

    retired = await is_retired(package, command_semaphore)

    if retired:
        p(f'{package} is retired', fg='green')
        if greens_file:
            print(package, file=greens_file)
        return

    content_length, = await gather_or_cancel(
        length(session, buildlog_link(package, build), http_semaphore)
    )

    critpath = is_critpath(package, critpath_pkgs)

    message = f'{package} failed len={content_length}'

    longlog = content_length > LIMIT

    if longlog and await is_blue(session, builderlive_link(package, build), http_semaphore):
        longlog = False

    repo_404 = False
    if await is_repo_404(session, rootlog_link(package, build), http_semaphore):
        longlog = True
        repo_404 = True

    if blues_file and not longlog:
        print(package, file=blues_file)

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
            if longlog:
                fg = 'red'
            else:
                fg = 'blue'
    if (fg == 'yellow' or fg == 'blue') and dependency_tree:
            await guess_missing_dependency(session, package, build, http_semaphore,
                                      fg, bugs)

    if fg == 'red':
        if await is_timeout(session, builderlive_link(package, build), http_semaphore):
            message += ' (copr timeout)'
            fg = 'magenta'

    if fg == 'red':
        if await is_white(session, package, http_semaphore):
            message += ' (last build failed in Koschei)'
            fg = 'white'

    if critpath:
        message += ' \N{FIRE}'
    p(message, fg=fg)

    if (
        browser_lock
        and (not bz or bz.status == "CLOSED")
        and (longlog)
        and (str(package) not in EXCLUDE)
        and (fg != 'magenta')
        and (fg != 'white')
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
    summary = f"{package} fails to build with Python 3.14: {reason['short_description']}"

    description = dedent(f"""
        {package} fails to build with Python 3.14.0a7.

        {reason['long_description']}

        https://docs.python.org/3.14/whatsnew/3.14.html

        For the build logs, see:
        https://copr-be.cloud.fedoraproject.org/results/{COPR_STR}/fedora-rawhide-x86_64/{build:08}-{package}/

        For all our attempts to build {package} with Python 3.14, see:
        https://copr.fedorainfracloud.org/coprs/{COPR_STR_G}/package/{package}/

        Testing and mass rebuild of packages is happening in copr.
        You can follow these instructions to test locally in mock if your package builds with Python 3.14:
        https://copr.fedorainfracloud.org/coprs/{COPR_STR_G}/

        Let us know here if you have any questions.

        Python 3.14 is planned to be included in Fedora 43.
        To make that update smoother, we're building Fedora packages with all pre-releases of Python 3.14.
        A build failure prevents us from testing all dependent packages (transitive [Build]Requires),
        so if this package is required a lot, it's important for us to get it fixed soon.

        We'd appreciate help from the people who know this package best,
        but if you don't want to work on this now, let us know so we can try to work around it on our side.
    """)

    url_prefix = 'https://bugzilla.redhat.com/enter_bug.cgi?'
    params = {
        'short_desc': summary,
        'comment': description,
        'component': str(package),
        'blocked': f"{TRACKER}",
        'product': 'Fedora',
        'version': 'rawhide',
        #'bug_severity': 'high',
        'cc': 'mhroncok@redhat.com,ksurma@redhat.com'
    }

    # Rate-limit opening browser tabs
    async with browser_lock:
        webbrowser.open(url_prefix + urlencode(params))
        # open the build logs next to bz template, so it's easier to identify issues
        webbrowser.open(builderlive_link(package, build))
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
yellow_pkgs = []
blue_pkgs = []

async def main(pkgs=None, open_bug_reports=False, with_reason=False, blues_file=None, magentas_file=None, greens_file=None, dependency_tree=None):
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

    async with aiohttp.ClientSession(headers={"Connection": "close"}) as session:

        # we could stream the content, but meh, get it all, it's not that long
        packages = copr()
        bugs = bugzillas()
        critpath_pkgs = fetch(session, CRITPATH_COMPONENTS, http_semaphore, json=True)

        packages, bugs, critpath_pkgs = await asyncio.gather(packages, bugs, critpath_pkgs)

        jobs = []

        for package in packages:
            try:
                package_name = package['builds']['latest']['source_package']['name']
                build = package['builds']['latest']['id']
                status = package['builds']['latest']['state']
                if pkgs and package_name not in pkgs:
                    continue
                await jobs.append(asyncio.ensure_future(process(
                    session, bugs, package_name, build, status,
                    http_semaphore, command_semaphore, critpath_pkgs,
                    browser_lock=browser_lock, with_reason=with_reason,
                    blues_file=blues_file, magentas_file=magentas_file,
                    greens_file=greens_file, dependency_tree=dependency_tree,
                )))
            except TypeError:
                pass
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
    '--greens-file',
    type=click.File('w'),
    help='Dump green-ish packages to a given file'
)
@click.option(
    '--dependency-tree/--no-dependency-tree',
    help='Show dependency tree of blue packages'
)
def run(pkgs, open_bug_reports, with_reason=None, blues_file=None, magentas_file=None, greens_file=None, dependency_tree=None):
    asyncio.run(main(pkgs, open_bug_reports, with_reason, blues_file, magentas_file, greens_file, dependency_tree))

if __name__ == '__main__':
    run()
