#!/bin/sh
# Small service manager: start / stop / restart / status / log / check
set -eu
# 工作目录一律取**脚本所在目录的上一级**，与调用时所在的目录无关：
# 相对路径先解析成绝对路径，再往上一级，从哪个目录调用都不会跑偏。
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE/.."
PIDFILE="data/service.pid"
LOG="data/service.log"
PORT=$(python3 -c "import json;print(json.load(open('config.json')).get('port', 8931))" 2>/dev/null || echo 8931)

pid() {
    [ -f "$PIDFILE" ] || return 1
    p=$(cat "$PIDFILE" 2>/dev/null || true)
    [ -n "${p:-}" ] || return 1
    kill -0 "$p" 2>/dev/null || return 1
    echo "$p"
}

running() {
    pid >/dev/null 2>&1
}

case "${1:-status}" in
start)
    if running; then
        echo "already running, pid $(pid)"
        exit 0
    fi
    mkdir -p data
    setsid nohup ./bin/run.sh >/dev/null 2>&1 &
    i=0
    while [ $i -lt 40 ]; do
        sleep 0.25
        if running; then
            echo "started, pid $(pid) on port $PORT"
            exit 0
        fi
        i=$((i + 1))
    done
    echo "FAILED to start; tail of $LOG:" >&2
    tail -n 20 "$LOG" >&2 || true
    exit 1
    ;;
stop)
    if ! running; then
        echo "not running"
        rm -f "$PIDFILE"
        exit 0
    fi
    p=$(pid)
    kill "$p" 2>/dev/null || true
    i=0
    while [ $i -lt 40 ] && kill -0 "$p" 2>/dev/null; do
        sleep 0.25
        i=$((i + 1))
    done
    kill -9 "$p" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped"
    ;;
restart)
    "$0" stop
    "$0" start
    ;;
status)
    if running; then
        echo "running, pid $(pid)"
        python3 - <<'PY'
import json, os, sqlite3
try:
    cfg = json.load(open("config.json", encoding="utf-8"))
except Exception:
    cfg = {}
prefix = (cfg.get("public_base_prefix") or "").rstrip("/")
db_path = os.path.join(os.environ.get("LOTTERY_DATA_DIR") or "data", "panel.sqlite3")
try:
    conn = sqlite3.connect(db_path, timeout=5.0)
    rows = conn.execute("SELECT seq, token, title, archived_at FROM events ORDER BY seq").fetchall()
except Exception as exc:
    print("  (读不到 %s：%s)" % (db_path, exc))
    rows = []
live = [r for r in rows if not r[3]]
if not live:
    print("  activities: (还没有活动，去面板里创建)")
for seq, token, title, _archived in live:
    base = prefix + "/" + token
    print("  [%d] %s" % (seq, title or "未命名"))
    print("      board : " + base + "/")
    print("      screen: " + base + "/screen")
    print("      admin : " + base + "/admin")
PY
    else
        echo "not running"
        exit 1
    fi
    ;;
reset)
    # Wipe the event data and start from an empty database.  config.json (the
    # access id) is untouched; the admin password is regenerated and printed.
    # A backup of the old database is kept next to it.
    if running; then
        echo "服务正在运行，先停止？"
    fi
    printf '这会删除全部名单、签到、奖票与抽奖记录，只保留访问 id。输入 yes 确认: '
    read -r answer || answer=""
    if [ "$answer" != "yes" ]; then
        echo "已取消"
        exit 1
    fi
    "$0" stop >/dev/null 2>&1 || true
    stamp=$(date +%Y%m%d-%H%M%S)
    # 一个活动一个文件夹：整个目录打一份备份再删掉
    for d in data/events/*/; do
        [ -d "$d" ] || continue
        seq=$(basename "$d")
        tar -czf "data/backup-$stamp-event-$seq.tgz" -C data/events "$seq" 2>/dev/null || true
        rm -rf "$d"
    done
    rm -f data/service.pid
    echo "已清空（备份在 data/backup-$stamp-*）"
    "$0" start
    ;;
log)
    tail -n "${2:-40}" "$LOG"
    ;;
check)
    python3 tools/smoke_test.py "http://127.0.0.1:$PORT"
    ;;
*)
    echo "usage: $0 {start|stop|restart|status|log [n]|check|reset}" >&2
    exit 2
    ;;
esac
