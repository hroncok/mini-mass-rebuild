import dnf
import re
import subprocess
import sys
from collections import defaultdict

TARGETVER = int(sys.argv[1]) if len(sys.argv) == 2 else None
RAWHIDEVER = 43
SUPPORTED_UPGRADE_VERS = (41, 42)
OBSOLETE_PYTHON_VER = '3.13'

DNF_CACHEDIR = '_dnf_cache_dir'
ARCH = 'x86_64'

sacks = {}

def rawhide_sack():
    try:
        return sacks[None]
    except KeyError:
        pass
    base = dnf.Base()
    conf = base.conf
    conf.cachedir = DNF_CACHEDIR
    conf.substitutions['releasever'] = str(RAWHIDEVER)
    conf.substitutions['basearch'] = ARCH
    base.repos.add_new_repo('rawhide', conf,
        baseurl=['http://kojipkgs.fedoraproject.org/repos/rawhide/latest/$basearch/'],
        skip_if_unavailable=False)
    base.fill_sack(load_system_repo=False, load_available_repos=True)
    sacks[None] = base.sack
    return base.sack


def fedora_sack(version):
    try:
        return sacks[version]
    except KeyError:
        pass
    base = dnf.Base()
    conf = base.conf
    conf.cachedir = DNF_CACHEDIR
    conf.substitutions['releasever'] = str(version)
    conf.substitutions['basearch'] = ARCH
    base.repos.add_new_repo(f'koji{version}', conf,
        baseurl=[f'http://kojipkgs.fedoraproject.org/repos/f{version}-build/latest/$basearch/'],
        skip_if_unavailable=False,
        enabled=True)
    base.repos.add_new_repo(f'fedora{version}', conf,
        metalink='https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever&arch=$basearch',
        skip_if_unavailable=False,
        enabled=True)
    base.repos.add_new_repo(f'updates{version}',
        conf,
        metalink='https://mirrors.fedoraproject.org/metalink?repo=updates-released-f$releasever&arch=$basearch',
        skip_if_unavailable=False,
        enabled=True)
    base.repos.add_new_repo(f'updates-testing{version}', conf,
        metalink='https://mirrors.fedoraproject.org/metalink?repo=updates-testing-f$releasever&arch=$basearch',
        skip_if_unavailable=False,
        enabled=True)
    base.fill_sack(load_system_repo=False, load_available_repos=True)
    sacks[version] = base.sack
    return base.sack


def repoquery(*args, **kwargs):
    version = kwargs.pop('version', None)
    if version is None:
        sack = rawhide_sack()
    else:
        sack = fedora_sack(version)
    if 'whatrequires' in kwargs:
        package_name = kwargs['whatrequires']
        package = None
        for pkg in sack.query().available().filter(name=package_name,
                                                   latest=1).run():
            if pkg.arch != 'i686':
                return sack.query().available().filter(requires=[pkg])
        return []
    if 'whatrequires_exact' in kwargs:
        return sack.query().available().filter(requires=kwargs['whatrequires_exact'])
    if 'whatobsoletes' in kwargs:
        return sack.query().filter(obsoletes=kwargs['whatobsoletes'], latest=1)
    if 'requires' in kwargs:
        pkgs = sack.query().filter(obsoletes=kwargs['requires'], latest=1).run()
        return pkgs[0].requires
    if 'all' in kwargs and kwargs['all']:
        return sack.query()
    raise RuntimeError('unknown query')


def get_old_pkgs():
    r = set()
    for version in SUPPORTED_UPGRADE_VERS:
        for dependency in (f'python(abi) = {OBSOLETE_PYTHON_VER}',
                           f'libpython{OBSOLETE_PYTHON_VER}.so.1.0()(64bit)',
                           f'libpython{OBSOLETE_PYTHON_VER}d.so.1.0()(64bit)'):
            pkgs = repoquery(version=version,
                    whatrequires_exact=dependency)
            for pkg in pkgs:
                r.add(pkg)
    return r


class SortableEVR:
    def __init__(self, evr):
        self.evr = evr

    def __repr__(self):
        return f"evr'{self.evr}'"

    def __eq__(self, other):
        return self.evr == other.evr

    def __lt__(self, other):
        return subprocess.call(('rpmdev-vercmp', self.evr, other.evr),
                               stdout=subprocess.DEVNULL) == 12


def removed_pkgs(version=None):
    name_versions = defaultdict(set)
    old_pkgs = get_old_pkgs()
    new = set()
    for pkg in repoquery(all=True, version=version):
        new.add(pkg.name)
    seen = set()
    while old_pkgs:
        old_pkg = old_pkgs.pop()
        if old_pkg.name not in new:
            name_versions[old_pkg.name].add(f'{old_pkg.epoch}:{old_pkg.version}-{old_pkg.release}')
            for dependent in what_required(old_pkg.name):
                if dependent.name not in seen:
                    old_pkgs.add(dependent)
        seen.add(old_pkg.name)
    return {name: max(versions, key=SortableEVR)
            for name, versions in name_versions.items()}

def what_required(dependency):
    r = []
    for version in SUPPORTED_UPGRADE_VERS:
        pkgs = repoquery(version=version, whatrequires=dependency)
        for pkg in pkgs:
            r.append(pkg)
    return r


def drop_dist(evr):
    ev, _, release = evr.rpartition('-')
    parts = (part for part in release.split('.') if not part.startswith('fc'))
    release = '.'.join(parts)
    return f'{ev}-{release}'


def drop_0epoch(evr):
    epoch, _, vr = evr.partition(':')
    return vr if epoch == '0' else evr


def bump_release(evr):
    ev, _, release = evr.rpartition('-')
    parts = release.split('.')
    release = []
    for part in parts:
        if part == '0':
            release.append(part)
        else:
            try:
                release.append(str(int(part) + 1))
            except ValueError:
                release.append(part)
                release.append("MANUAL")
            release = '.'.join(release)
            return f'{ev}-{release}'
    else:
        raise RuntimeError(f'Cannot bump {evr}')


def format_obsolete(pkg, evr):
    evr = bump_release(evr)
    return f'%obsolete {pkg} {evr}'


rp = removed_pkgs(version=TARGETVER)
for pkg in sorted(rp):
    version = drop_0epoch(drop_dist(rp[pkg]))
    whatobsoletes = set()
    obsoleters = repoquery(version=TARGETVER, whatobsoletes=f'{pkg} = {version}')
    for obsoleter in obsoleters:
        whatobsoletes.add(f'{obsoleter.name}')
    if not whatobsoletes or whatobsoletes == {'fedora-obsolete-packages'}:
        print(format_obsolete(pkg, version))
    else:
        obs = ', '.join(sorted(whatobsoletes))
        #print(f'# {pkg} {version} obsoleted by {obs}', file=sys.stderr)
