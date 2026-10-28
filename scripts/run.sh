#!/usr/bin/env bash
# 启动前预检 + 拉起本地推理服务
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "✗ $1"; exit 1; }
ok()   { echo "✓ $1"; }

echo "== digital-me 启动预检 =="

# 模型
MODEL="models/Qwen3-4B-Q4_K_M.gguf"
if [ ! -f "$MODEL" ]; then
  fail "缺少模型。先跑 python3 scripts/fetch_models.py"
fi
SIZE=$(wc -c < "$MODEL" | tr -d ' ')
if [ "$SIZE" -lt 2000000000 ]; then
  fail "模型文件只有 $((SIZE/1024/1024)) MB，应该是 2381 MB——大概率下载不完整，删掉重下"
fi
ok "模型 $(($SIZE/1024/1024)) MB"

# 虚拟环境
if [ ! -x .venv/bin/python ]; then
  fail "缺少 .venv，先跑 ./install.sh"
fi
ok "Python 环境就绪"

# 配置
if [ ! -f config/character.yaml ]; then
  echo "!  没有 config/character.yaml，将使用示例人格"
  echo "   cp config/character.example.yaml config/character.yaml"
fi

# 推理引擎
if command -v llama-server >/dev/null 2>&1; then
  ok "llama-server 已安装"
  echo
  echo "启动 llama-server（Ctrl+C 停止）..."
  exec llama-server -m "$MODEL" -c 4096 --port 8080 --host 127.0.0.1
elif [ -x third_party/llama.cpp/build/bin/llama-server ]; then
  ok "使用 third_party 里的 llama-server"
  echo
  exec third_party/llama.cpp/build/bin/llama-server -m "$MODEL" -c 4096 --port 8080 --host 127.0.0.1
else
  echo
  echo "还没装推理引擎 llama.cpp，二选一："
  echo "  macOS:   brew install llama.cpp"
  echo "  源码:    git clone https://github.com/ggml-org/llama.cpp third_party/llama.cpp"
  echo "           cd third_party/llama.cpp && cmake -B build && cmake --build build -j"
  exit 1
fi
