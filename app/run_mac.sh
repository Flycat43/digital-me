#!/usr/bin/env bash
# digital-me 桌宠一键启动（macOS）
# 用法： ./run_mac.sh            正常启动（透明、常驻最前）
#        DM_QUERY="bg=1" ./run_mac.sh   调试用：铺背景色
set -euo pipefail
cd "$(dirname "$0")"

# ---------- 1. 找 Python（需要 pyyaml） ----------
PY="${DM_PYTHON:-}"
if [ -z "$PY" ]; then
  for c in "$HOME/Documents/digital-me/.venv/bin/python" "$PWD/.venv/bin/python" "$(command -v python3 || true)"; do
    if [ -n "$c" ] && [ -x "$c" ] && "$c" -c "import yaml" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "✗ 没找到带 pyyaml 的 python3。二选一："
  echo "   python3 -m venv .venv && .venv/bin/pip install pyyaml"
  echo "   或装 uv： uv venv .venv && uv pip install pyyaml"
  exit 1
fi

# ---------- 2. 起服务（已起来就复用） ----------
health() { curl -s -o /dev/null -w '%{http_code}' -m 2 http://127.0.0.1:8765/api/health || true; }
if [ "$(health)" = "000" ]; then
  mkdir -p logs
  "$PY" server.py --port 8765 > logs/server.log 2>&1 &
  for _ in $(seq 1 40); do [ "$(health)" != "000" ] && break; sleep 0.25; done
fi
CODE="$(health)"
case "$CODE" in
  200) echo "✓ 服务 + 模型就绪  http://127.0.0.1:8765" ;;
  503) echo "! 服务起来了，但 ollama 或模型没就绪（看 http://127.0.0.1:8765/api/health）" ;;
  *)   echo "✗ 服务没起来，看 logs/server.log"; exit 1 ;;
esac

# ---------- 3. 编译窗口壳（首次或源码变更时） ----------
if [ ! -x bin/digital-me-pet ] || [ shell_mac.swift -nt bin/digital-me-pet ]; then
  echo "编译窗口壳（约 8 秒）…"
  swiftc -O -framework Cocoa -framework WebKit shell_mac.swift -o bin/digital-me-pet
fi

if pgrep -f "bin/digital-me-pet" >/dev/null 2>&1; then
  echo "! 已经有一个桌宠在跑了。要重开先执行： pkill -f bin/digital-me-pet"
  exit 0
fi

echo "启动桌宠（⌘Q 退出）"
exec ./bin/digital-me-pet
