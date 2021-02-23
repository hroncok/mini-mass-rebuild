import subprocess
import sys
from collections import defaultdict


def repoquery(*args, **kwargs):
    cmd = ['repoquery']
    version = kwargs.pop('version', None)
    if version is None:
        cmd.append('--repo=rawhide')
    else:
        cmd.extend(['--repo=fedora', '--repo=updates',
                    '--repo=updates-testing', f'--releasever={version}'])
    if args:
        cmd.extend(args)
    for option, value in kwargs.items():
        cmd.append(f'--{option}')
        if value is not True:
            cmd.append(value)
    proc = subprocess.run(cmd,
                          text=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL,
                          check=True)
    return proc.stdout.splitlines()


def old_pkgs():
    r = []
    for version in (32,):
        for dependency in ('python(abi) = 3.8',
                           'libpython3.8.so.1.0()(64bit)',
                           'libpython3.8d.so.1.0()(64bit)'):
            r.extend(repoquery(version=version,
                               whatrequires=dependency,
                               qf='%{NAME} %{EPOCH}:%{VERSION}-%{RELEASE}'))
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


def removed_pkgs():
    name_versions = defaultdict(set)
    old_name_evrs = old_pkgs()
    new = set(repoquery(all=True, qf='%{NAME}', version=34))
    for name_evr in old_name_evrs:
        name, _, evr = name_evr.partition(' ')
        if name not in new:
            name_versions[name].add(evr)
    return {name: max(versions, key=SortableEVR)
            for name, versions in name_versions.items()}


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


rp = removed_pkgs()
for pkg in sorted(rp):
    version = drop_0epoch(drop_dist(rp[pkg]))
    whatobsoletes = repoquery(whatobsoletes=f'{pkg} = {version}', qf='%{NAME}', version=34)
    if not whatobsoletes or whatobsoletes == ['fedora-obsolete-packages']:
        print(format_obsolete(pkg, version))
    else:
        obs = ', '.join(whatobsoletes)
        print(f'# {pkg} {version} obsoleted by {obs}', file=sys.stderr)
