import bugzilla
import pathlib
import time
import sys
import webbrowser
from urllib.parse import urlencode
from textwrap import dedent


BUGZILLA = 'bugzilla.redhat.com'
TRACKER = 1686977  # PYTHON38


def bugzillas():
    bzapi = bugzilla.Bugzilla(BUGZILLA)
    query = bzapi.build_query(product='Fedora')
    query['blocks'] = TRACKER
    return sorted(bzapi.query(query), key=lambda b: -b.id)


def bug(bugs, package):
    for b in bugs:
        if b.component == package:
            return b
    return None


def open_bz(package):
    summary = f"{package} fails to build with Python 3.8 on Fedora 32+"

    description = dedent(f"""
        {package} fails to build with Python 3.8.0b3 in Fedora 32.

        See the build failures at https://koji.fedoraproject.org/koji/search?match=glob&type=package&terms={package}

        ...

        It is not important whether the problem is relevant to Python 3.8, this issue is blocking the Python 3.8 rebuilds.
        If this package won't build with 3.8, it won't be installable, along with all its dependent packages, in Fedora 32 and further.

        Furthermore, as it fails to install, its dependent packages will fail to install and/or build as well.

        Please rebuild the package in Fedora 32 (rawhide).

        Let us know here if you have any questions. Thank You!
    """)

    url_prefix = 'https://bugzilla.redhat.com/enter_bug.cgi?'
    params = {
        'short_desc': summary,
        'comment': description,
        'component': package,
        'blocked': 'PYTHON38',
        'product': 'Fedora',
        'version': 'rawhide',
        'bug_severity': 'high',
    }

    webbrowser.open(url_prefix + urlencode(params))
    time.sleep(1)
    webbrowser.open(f'https://koji.fedoraproject.org/koji/search?match=glob&type=package&terms={package}')
    time.sleep(1)


pkgs = pathlib.Path(sys.argv[1]).read_text().splitlines()
print('Getting bugzillas...', end=' ', flush=True)
bugs = bugzillas()
print('..done.')

for pkg in pkgs:
    bz = bug(bugs, pkg)
    if bz:
        print(f'{pkg} bz{bz.id} {bz.status}')
    if not bz or bz.status == 'CLOSED':
        open_bz(pkg)
