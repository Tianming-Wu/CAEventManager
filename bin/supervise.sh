#!/bin/sh
# 看护进程：跑 bin/run.sh，等它退出再拉起来。
#
# 面板里的「重启服务 / 进入安全模式 / 退出安全模式」都是让 app 自己退出：
# 安全模式开关在启动时读一次，所以改完必须重起一遍。停服务请用
# ./bin/service.sh stop（它会先停这里），别直接 kill app。
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE/.."
PIDFILE="data/supervise.pid"
APPPID="data/service.pid"
STOP="data/stop.request"
child=""

finish() {
    [ -n "$child" ] && kill "$child" 2>/dev/null
    rm -f "$PIDFILE" "$APPPID"
    exit 0
}
trap finish TERM INT
trap '' HUP

mkdir -p data
echo $$ >"$PIDFILE"

while :; do
    if [ -f "$STOP" ]; then
        rm -f "$STOP"
        finish
    fi
    LOTTERY_SUPERVISED=1 ./bin/run.sh &
    child=$!
    wait "$child"
    child=""
    # 走到这里说明 app 退出了：正常重启或崩溃，都是重新起一遍
    rm -f "$APPPID"
    if [ -f "$STOP" ]; then
        rm -f "$STOP"
        finish
    fi
    # flock 还握在上一份手里时新的一份会立刻退出，隔一秒再试，不空转
    sleep 1
done
