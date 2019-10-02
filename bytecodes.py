import json
import subprocess
from click import progressbar

repoquery = 'repoquery --repo=koji --refresh -f *.cpython-38.pyc --source'.split()
py38_pkgs = subprocess.run(repoquery, stdout=subprocess.PIPE, text=True).stdout.splitlines()

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


try:
    with progressbar(py38_pkgs, item_show_func=isf) as bar:
        for pkg in bar:
            nevr = '.'.join(pkg.split('.')[:-2])
            name = '-'.join(nevr.split('-')[:-2])
            if name in processed:
                continue
            if nevr not in after(name, '2019-08-31 23:59:59'):
                if nevr not in after(name, '2019-08-31 16:11:41'):
                    torebuild.add(name)
                else:
                    inspection.add(name)
            else:
                done.add(name)
            processed.add(name)
except KeyboardInterrupt:
    print('Interrupted.\n')

print(f'Processed {len(processed)} packages.\n')
print(f'{len(done)} packages were build with b4+')
print(f'{len(inspection)} packages were built on 2019-08-31 and need manual inspection')
print(f'{len(torebuild)} packages need to be rebuilt with b4+')

with open('bytecodes.json', 'w') as f:
    json.dump({'done': sorted(done),
               'inspection': sorted(inspection),
               'torebuild': sorted(torebuild)}, f, indent=4)
