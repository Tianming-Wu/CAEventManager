#!/bin/sh
# Verify the deployed instance: secret routing, assets, and a full API run on an
# isolated throw-away instance so the production database is never touched.
set -u
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PORT=$(python3 -c "import json;print(json.load(open('config.json'))['port'])" 2>/dev/null || echo 8931)
SECRET=$(python3 -c "import json;print(json.load(open('config.json'))['path_secret'])" 2>/dev/null || echo "")
if [ -z "$SECRET" ]; then
    echo "config.json has no path_secret - run the service once first" >&2
    exit 1
fi
BASE="http://127.0.0.1:$PORT/$SECRET"

code() { curl -s -o /dev/null -w '%{http_code}' "$1"; }
size() { curl -s "$1" | wc -c; }

echo "=== pages under the secret id ({$SECRET:0:8}...) ==="
for p in / /admin /login /screen /board /checkin \
         /static/app.css /static/admin.js /static/screen.js /static/board.js \
         /static/qrcode.js /static/favicon.png /static/apple-touch-icon.png; do
    printf '%-30s %s  %s bytes\n' "$p" "$(code "$BASE$p")" "$(size "$BASE$p")"
done

echo
echo "=== secret is mandatory (root paths must 404) ==="
for p in / /admin /screen /login /api/board /static/app.css; do
    c=$(code "http://127.0.0.1:$PORT$p")
    if [ "$c" = "404" ]; then printf '%-30s %s  ok\n' "$p" "$c"; else printf '%-30s %s  LEAK\n' "$p" "$c"; fi
done

echo
echo "=== files present in the deployed tree ==="
for f in tpl/board.html tpl/admin.html tpl/screen.html tpl/login.html \
         static/app.css static/common.js static/admin.js static/board.js \
         static/screen.js static/login.js static/qrcode.js \
         static/favicon.png static/apple-touch-icon.png \
         pinyintable.py localconfig.py config.json; do
    [ -f "$f" ] && printf 'ok   %s (%s bytes)\n' "$f" "$(wc -c <"$f")" || printf 'MISS %s\n' "$f"
done

echo
echo "=== smoke test on an isolated instance (port $((PORT + 1))) ==="
TMPD=$(mktemp -d)
export LOTTERY_DATA_DIR="$TMPD"
export LOTTERY_ADMIN_PASSWORD="smoke-test-pass"
python3 app.py --host 127.0.0.1 --port $((PORT + 1)) >"$TMPD/boot.log" 2>&1 &
BOOTPID=$!
i=0
while [ $i -lt 60 ]; do
    sleep 0.25
    curl -s -o /dev/null "http://127.0.0.1:$((PORT + 1))/$SECRET/api/board" && break
    i=$((i + 1))
done
python3 tools/smoke_test.py "http://127.0.0.1:$((PORT + 1))" "smoke-test-pass"
RC=$?
kill $BOOTPID 2>/dev/null
wait $BOOTPID 2>/dev/null
rm -rf "$TMPD"
echo
echo "smoke test exit code: $RC"
exit $RC
