import pathlib
import subprocess

python38 = set(pathlib.Path('python38.pkgs').read_text().splitlines())

kojirepo = set(pathlib.Path('koji.repoquery').read_text().splitlines())
py39repo = set(pathlib.Path('python39.repoquery').read_text().splitlines())

kojidict = {pkg.rsplit('-', 2)[0]: "{0}-{1}".format(*pkg.rsplit('-', 2)[1:]) for pkg in kojirepo}
py39dict = {pkg.rsplit('-', 2)[0]: "{0}-{1}".format(*pkg.rsplit('-', 2)[1:]) for pkg in py39repo}

todo = set()
for pkg in sorted(python38):
    if pkg not in py39dict:
        continue
    print(f'{pkg: <30}', end=' ')
    e = subprocess.call(('rpmdev-vercmp', kojidict[pkg], py39dict[pkg]))
    if e == 11:
        todo.add(pkg)


print()

for pkg in sorted(todo):
    print(pkg)
