#!/bin/bash
# qq.sh q|look args...   带标题、截断长行
N="$(dirname "$0")"
t=$1; shift
echo "################ $t $*"
python3 "$N/$t.py" "$@" 2>&1 | cut -c1-${W:-320}
