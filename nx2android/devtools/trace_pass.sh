#!/bin/sh
# Trace one pass's draws in the running app (DM_VK_TRACE_PASS through DM_CTL) and pull that part of the log.   trace_pass.sh <pass> <local out file>
eval "$(python "$(dirname "$0")/device.py" --sh)"; A=$ADB   # devtools/device.json: the device and the app
MSYS_NO_PATHCONV=1 $A shell "N=\$(wc -l < $F/log.txt); echo 'env DM_VK_TRACE_PASS=$1' >> $F/ctl.txt; sleep 1; echo 'env DM_VK_TRACE_PASS=-1' >> $F/ctl.txt; sleep 1; tail -n +\$N $F/log.txt > /data/local/tmp/tr.txt"
MSYS_NO_PATHCONV=1 $A pull /data/local/tmp/tr.txt "$2" >/dev/null && wc -l "$2"
