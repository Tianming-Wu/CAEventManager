#!/bin/sh
# Foreground runner for the lottery service.
# Use ./service.sh start to run it detached.
#
# flock --no-fork guarantees exactly one instance and keeps the pid stable,
# which is what service.sh relies on.
set -eu
# 工作目录一律取**脚本所在目录的上一级**，与调用时所在的目录无关：
# 相对路径先解析成绝对路径，再往上一级，从哪个目录调用都不会跑偏。
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE/.."
umask 077
mkdir -p data
# host/port come from config.json (LOTTERY_HOST / LOTTERY_PORT still win).
exec flock --nonblock --no-fork ./data/service.lock \
    python3 -u app.py >>data/service.log 2>&1
