echo $1 $(http $(grep 'Task info' $1.log | cut -d' ' -f3) | grep 'mock exited with status' | cut -d';' -f2 | sed -Er 's@see ([^\.]+)\.log for more information</pre>@\1@')
