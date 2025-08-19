import json
import subprocess
from click import progressbar

repoquery = 'repoquery --repo=koji43 --refresh -f *.cpython-314.pyc --source'.split()
py314_pkgs = subprocess.run(repoquery, stdout=subprocess.PIPE, text=True).stdout.splitlines()

try:
    with open('bytecodes.json', 'r') as f:
        done = set(json.load(f)['done'])
except (FileNotFoundError, KeyError):
    done = set()

torebuild = set()
inspection = set()
processed = torebuild | inspection | done


def after(name, time):
    cmd = ('koji', 'list-builds', '--package', name, '--after', time, '--state=COMPLETE', '--quiet')
    return [a.split()[0] for a in subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout.splitlines() if a]


def isf(item):
    return f'[+{len(done)}/{len(inspection)}/-{len(torebuild)}] {item}'


def built_by(nevr, username):
    cmd = ('koji', 'buildinfo', nevr)
    lines = subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout.splitlines()
    for line in lines:
        if line.startswith('Built by: '):
            return line.strip() == f'Built by: {username}'
    raise RuntimeError('this should never happen')


try:
    with progressbar(py314_pkgs, item_show_func=isf) as bar:
        for pkg in bar:
            nevr = '.'.join(pkg.split('.')[:-2])
            name = '-'.join(nevr.split('-')[:-2])
            if name in processed:
                continue
            if nevr not in after(name, '2025-08-15 23:59:59'):
                # https://koji.fedoraproject.org/koji/buildinfo?buildID=2791425
                # https://bodhi.fedoraproject.org/updates/FEDORA-2025-2fe07c73d5
                # https://bodhi.fedoraproject.org/overrides/python3.14-3.14.0~rc2-1.fc43
                if nevr not in after(name, '2025-08-15 12:39:34'):
                    torebuild.add(name)
                elif built_by(nevr, 'churchyard'):
                    done.add(name)
                else:
                    inspection.add(name)
            else:
                done.add(name)
            processed.add(name)
except KeyboardInterrupt:
    print('Interrupted.\n')

print(f'Processed {len(processed)} packages.\n')
print(f'{len(done)} packages were build with rc2+')
print(f'{len(inspection)} packages were built on 2025-06-18 and need manual inspection')
print(f'{len(torebuild)} packages need to be rebuilt with rc2+')

with open('bytecodes.json', 'w') as f:
    json.dump({'done': sorted(done),
               'inspection': sorted(inspection),
               'torebuild': sorted(torebuild)}, f, indent=4)
