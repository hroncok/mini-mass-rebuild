Mini mass rebuild
-----------------

This explains how I do bulk Koji operations in Fedora.
Note that this is not ready to use tool with nice command line API, this is
merely an example.

**Note:** Make sure your `/usr/bin/parallel` comes from `moreutils-parallel`
package (not just `parallel`), or the following example won't work.

Take the `fedpkg-build.sh` script and edit it to suite your needs. And then
invoke it in parallel:

```console
$ cd empty_directory
$ parallel -j 12 bash ../fedpkg-build.sh -- $(cat ../packages.txt)
```

It takes packages as positional arguments, here provided via `cat`. You can
also boost the parallelism by increasing the number `12` given to `-j` :)

The script logs, so you can later analyze the logs with grep.
Note that `fedpkg clone` fails now and then, so you can grep the logs for
`Could not execute clone` and rerun those packages once again.
If it would bother you too much, you can add retrying to the script.

(Consider this repo [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en).)
