import subprocess
import rpm

MAIN = 'f43'
SIDE = 'f43-python'

SIGNS = {
    1: '>',
    0: '==',
    -1: '<',
}


def split(nevr):
    nev, _, r = nevr.rpartition('-')
    n, _, ev = nev.rpartition('-')
    e, _, v = ev.rpartition(':')
    e = e or '0'
    return n, (e, v, r)


def main():
    procs = {}
    evrs = {}
    for tag in SIDE, MAIN:
        procs[tag] = subprocess.Popen(('koji', 'list-tagged', tag, '--quiet', '--latest'), text=True, stdout=subprocess.PIPE)
    for tag in SIDE, MAIN:
        stdout, _ = procs[tag].communicate()
        assert procs[tag].returncode == 0
        evrs[tag] = dict(split(pkg.partition(' ')[0]) for pkg in stdout.splitlines())

    todo = set()

    for pkg in sorted(evrs[SIDE]):
        if pkg not in evrs[MAIN]:
            continue
        sign = SIGNS[rpm.labelCompare(evrs[MAIN][pkg], evrs[SIDE][pkg])]
        print(f'{pkg: <30} {"-".join(evrs[MAIN][pkg])} {sign} {"-".join(evrs[SIDE][pkg])}')

        if sign == '>':
            todo.add(pkg)

    print()

    for pkg in sorted(todo):
        print(pkg)


if __name__ == '__main__':
    main()
