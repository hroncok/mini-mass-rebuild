# https://koji.fedoraproject.org/koji/taskinfo?taskID=34580080
taskid=$(grep 'Task info' ${1}.log | tail -n1 | cut -d= -f2)
koji download-task --arch noarch --arch x86_64 ${taskid}
koji cancel ${taskid}
