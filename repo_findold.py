import asyncio
import os
import pathlib
import sys


async def main():
    python38 = set(pathlib.Path('python38.pkgs').read_text().splitlines())

    kojirepo = set(pathlib.Path('koji.repoquery').read_text().splitlines())
    py39repo = set(pathlib.Path('python39.repoquery').read_text().splitlines())

    kojidict = {pkg.rsplit('-', 2)[0]: "{0}-{1}".format(*pkg.rsplit('-', 2)[1:]) for pkg in kojirepo}
    py39dict = {pkg.rsplit('-', 2)[0]: "{0}-{1}".format(*pkg.rsplit('-', 2)[1:]) for pkg in py39repo}

    todo = set()
    tasks = []

    semaphore = asyncio.Semaphore(os.cpu_count())

    for pkg in sorted(python38):
        if pkg not in py39dict:
            continue
        tasks.append(compare(pkg, kojidict[pkg], py39dict[pkg], todo, semaphore))
    await asyncio.gather(*tasks)

    print()

    for pkg in sorted(todo):
        print(pkg)


async def compare(pkg, kojiver, py39ver, todo, semaphore):
    cmd = ('rpmdev-vercmp', kojiver, py39ver)
    async with semaphore:
        proc = await asyncio.create_subprocess_exec(*cmd,
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)

        stdout, stderr = await proc.communicate()

    print(f'{pkg: <30} {stdout.decode().strip()}')
    if stderr:
        print(stderr.decode().strip(), file=sys.stderr)

    if proc.returncode == 11:
        todo.add(pkg)


if __name__ == '__main__':
    asyncio.run(main())
