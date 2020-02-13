To see and report failures:

    $ python -u monitor_check.py --blues-file blues --open-bug-reports

Use https://etherpad.gnome.org/p/python39-bugz to enrich the bug reports.


To mass build all blues (resolve failures):

    $ parallel -j 12 copr build-package --nowait @python/python3.9 --background --name -- $(cat blues)


Monster repoquery to count packages:

    $ wc -l *.pkgs && mv python38.pkgs python38.pkgs_ && (repoquery --repo=koji --source --whatrequires 'libpython3.8.so.1.0()(64bit)'; repoquery --repo=koji --source --whatrequires 'python(abi) = 3.8') | pkgname | sort | uniq | egrep -v '^python3$' > python38.pkgs && (repoquery --refresh --repo=python39 --source --whatrequires 'libpython3.9.so.1.0()(64bit)'; repoquery --repo=python39 --source --whatrequires 'python(abi) = 3.9') | pkgname | sort | uniq | egrep -v '^python3$' > python39.pkgs && python remove_closed.py python38.pkgs python39.pkgs todo.pkgs && wc -l *.pkgs


Diff python38.pkgs_ against python38.pkgs and add new Fedora Python 3 packages to copr:

    $ for pkg in pkg1 pkg2 ...; do echo $pkg; copr add-package-scm --clone-url https://src.fedoraproject.org/rpms/${pkg}.git --name $pkg --webhook-rebuild on --commit master @python/python3.9 && copr build-package --nowait --name $pkg @python/python3.9; done



See what packages are outdated in Copr:

    $ repoquery --repo=koji-source --latest=1 | tee koji.repoquery
    $ repoquery --repo=python39 --latest=1 | grep src$ | tee python39.repoquery
    $ python -u repo_findold.py

Build them:

    $ parallel -j 12 copr build-package --nowait @python/python3.9 --name -- pkg1 pkg2 ...

(The koji-source repo is broken ATM, I use rawhide-source for instead (not so up to date))
