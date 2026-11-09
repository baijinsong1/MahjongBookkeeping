#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PYTHON_BIN="${PYTHON_BIN:-$(cd "$PROJECT_DIR/.." && pwd)/.venv/bin/python}"
NGROK_BIN="${NGROK_BIN:-$(command -v ngrok || true)}"
NGROK_STATIC_URL="${NGROK_STATIC_URL:-}"
USER_DOMAIN="gui/$(id -u)"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "未找到虚拟环境 Python：$PYTHON_BIN" >&2
    echo "请先创建虚拟环境，或通过 PYTHON_BIN=/path/to/python 指定解释器。" >&2
    exit 1
fi

if [[ -z "$NGROK_BIN" || ! -x "$NGROK_BIN" ]]; then
    echo "未找到 ngrok，请先安装并通过 NGROK_BIN=/path/to/ngrok 指定路径。" >&2
    exit 1
fi

if [[ ! -s "$PROJECT_DIR/instance/admin.json" ]]; then
    echo "首次安装需要创建管理员账号。"
    (cd "$PROJECT_DIR" && "$PYTHON_BIN" -m flask --app app create-admin)
fi

if lsof -nP -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 8080 已被占用，请先停止当前 PyCharm/终端中运行的 Flask 服务。" >&2
    exit 1
fi

if lsof -nP -iTCP:4040 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 4040 已被占用，请先停止当前终端中运行的 ngrok。" >&2
    exit 1
fi

mkdir -p "$LOG_DIR" "$LAUNCH_AGENTS_DIR"

if [[ -n "$NGROK_STATIC_URL" ]]; then
    NGROK_URL_ARG="        <string>--url=${NGROK_STATIC_URL}</string>"
else
    NGROK_URL_ARG=""
fi

render_template() {
    local template="$1"
    local output="$2"
    sed \
        -e "s|__PYTHON__|$PYTHON_BIN|g" \
        -e "s|__APP__|$PROJECT_DIR/app.py|g" \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__LOG_DIR__|$LOG_DIR|g" \
        -e "s|__NGROK__|$NGROK_BIN|g" \
        -e "s|__NGROK_URL_ARG__|$NGROK_URL_ARG|g" \
        "$template" > "$output"
}

FLASK_PLIST="$LAUNCH_AGENTS_DIR/com.majiang.app.plist"
NGROK_PLIST="$LAUNCH_AGENTS_DIR/com.ngrok.app.plist"

render_template "$PROJECT_DIR/deploy/com.majiang.app.plist.template" "$FLASK_PLIST"
render_template "$PROJECT_DIR/deploy/com.ngrok.app.plist.template" "$NGROK_PLIST"

plutil -lint "$FLASK_PLIST" >/dev/null
plutil -lint "$NGROK_PLIST" >/dev/null

launchctl bootout "$USER_DOMAIN/com.majiang.app" 2>/dev/null || true
launchctl bootout "$USER_DOMAIN/com.ngrok.app" 2>/dev/null || true
launchctl bootstrap "$USER_DOMAIN" "$FLASK_PLIST"
launchctl bootstrap "$USER_DOMAIN" "$NGROK_PLIST"

echo "后台服务已安装并启动。"
echo "Flask 日志：$LOG_DIR/flask.log"
echo "ngrok 日志：$LOG_DIR/ngrok.log"
if [[ -n "$NGROK_STATIC_URL" ]]; then
    echo "公网地址：$NGROK_STATIC_URL"
else
    echo "未配置静态域名，请在 ngrok 日志中查看本次公网地址。"
fi
