#!/bin/bash
set -euo pipefail

USER_DOMAIN="gui/$(id -u)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

launchctl bootout "$USER_DOMAIN/com.majiang.app" 2>/dev/null || true
launchctl bootout "$USER_DOMAIN/com.ngrok.app" 2>/dev/null || true
rm -f "$LAUNCH_AGENTS_DIR/com.majiang.app.plist" "$LAUNCH_AGENTS_DIR/com.ngrok.app.plist"

echo "后台服务已停止并移除，不会在下次登录时自动启动。"
