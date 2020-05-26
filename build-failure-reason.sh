task=$(grep 'Task info' $1.log | cut -d' ' -f3)
subtask=$(http $task | grep -E 'taskID=[0-9]+" class="taskfailed"' | head -n1 | sed -E 's/.+=([0-9]+)".+/\1/')
buildlog=https://kojipkgs.fedoraproject.org//work/tasks/${subtask: -4}/${subtask}/build.log
len=$(http HEAD $buildlog -h | grep content-length | cut -d" " -f2 | tr -d '[:space:]')
if (( $len > 1300 )); then
    echo $1 build
else
    echo $1 root
fi
