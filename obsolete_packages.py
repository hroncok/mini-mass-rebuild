import subprocess
import sys


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
    r = set()
    for version in 30, 31:
        for dependency in 'python(abi) = 3.7', 'libpython3.7m.so.1.0()(64bit)':
            r |= set(repoquery(version=version,
                               whatrequires=dependency,
                               qf='%{NAME}'))
    return r


def removed_pkgs():
    old = old_pkgs()
    new = set(repoquery(all=True, qf='%{NAME}'))
    return old - new


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


def newest_version(pkg):
    qf = '%{EPOCH}:%{VERSION}-%{RELEASE}'
    versions = (repoquery(pkg, version=30, qf=qf) +
                repoquery(pkg, version=31, qf=qf))
    return max(versions, key=SortableEVR)


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
            release.append(str(int(part) + 1))
            release = '.'.join(release)
            return f'{ev}-{release}'
    else:
        raise RuntimeError(f'Cannot bump {evr}')


def format_obsolete(pkg, evr):
    evr = bump_release(evr)
    return f'%obsolete {pkg} {evr}'


for pkg in sorted(removed_pkgs()):
    version = drop_0epoch(drop_dist(newest_version(pkg)))
    whatobsoletes = repoquery(whatobsoletes=f'{pkg} = {version}', qf='%{NAME}')
    if not whatobsoletes or whatobsoletes == ['fedora-obsolete-packages']:
        print(format_obsolete(pkg, version))
    else:
        obs = ', '.join(whatobsoletes)
        print(f'# {pkg} {version} obsoleted by {obs}', file=sys.stderr)
