import pathlib
import sys

original = pathlib.Path(sys.argv[1])
closed = pathlib.Path(sys.argv[2])
new = pathlib.Path(sys.argv[3])

originals = set(original.read_text().splitlines())
closeds = set(closed.read_text().splitlines())
news = originals - closeds
new.write_text('\n'.join(sorted(news)) + '\n')
