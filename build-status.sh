echo $1 $(http --timeout 300 $(grep 'Task info' $1.log | cut -d' ' -f3) | grep '<td class="task' | cut -d'>' -f2)
