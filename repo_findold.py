import pathlib
import rpm

SIGNS = {
    1: '>',
    0: '==',
    -1: '<',
}


def split(nevra):
    nev, _, ra = nevra.rpartition('-')
    n, _, ev = nev.rpartition('-')
    e, _, v = ev.rpartition(':')
    e = e or '0'
    r, _, a = ra.rpartition('.')
    if r.endswith('.src'):
        r = r[:-4]
    return n, (e, v, r)


def main():
    python38 = set(pathlib.Path('python38.pkgs').read_text().splitlines())

    kojirepo = set(pathlib.Path('koji.repoquery').read_text().splitlines())
    py39repo = set(pathlib.Path('python39koji.repoquery').read_text().splitlines())

    kojidict = dict(split(pkg) for pkg in kojirepo)
    py39dict = dict(split(pkg) for pkg in py39repo)

    todo = set()

    for pkg in sorted(python38):
        if pkg not in py39dict:
            continue
        sign = SIGNS[rpm.labelCompare(kojidict[pkg], py39dict[pkg])]
        print(f'{pkg: <30} {"-".join(kojidict[pkg])} {sign} {"-".join(py39dict[pkg])}')

        if sign == '>':
            todo.add(pkg)

    print()

    for pkg in sorted(todo):
        print(pkg)


if __name__ == '__main__':
    main()
