#!/usr/bin/env bash
# 启动前预检 + 拉起本地推理服务
set -euo pipefail
cd "$(dirname "$0")/.."

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

# 推理引擎：llama-server 优先（HTTP 接口），否则用 ollama
if command -v llama-server >/dev/null 2>&1; then
  ok "llama-server 已安装"
  echo
  echo "启动 llama-server（Ctrl+C 停止）..."
  exec llama-server -m "$MODEL" -c 4096 --port 8080 --host 127.0.0.1
elif [ -x third_party/llama.cpp/build/bin/llama-server ]; then
  ok "使用 third_party 里的 llama-server"
  echo
  exec third_party/llama.cpp/build/bin/llama-server -m "$MODEL" -c 4096 --port 8080 --host 127.0.0.1
elif command -v ollama >/dev/null 2>&1; then
  ok "检测到 ollama（内核同样是 llama.cpp）"
  NAME="digital-me-local"
  MODEL_ABS="$(cd "$(dirname "$MODEL")" && pwd)/$(basename "$MODEL")"
  if ! ollama list | grep -q "^$NAME"; then
    echo "  首次导入模型（约 2.4 GB，只做一次）..."
    printf 'FROM %s\n' "$MODEL_ABS" > models/Modelfile
    ollama create "$NAME" -f models/Modelfile
  fi
  ok "模型已导入为 $NAME"
  echo
  echo "ollama 已在后台提供 HTTP 接口：http://127.0.0.1:11434"
  echo "直接对话： ollama run $NAME"
  echo "对话时记得在 prompt 末尾加 /no_think，否则 Qwen3 会先生成几百 token 思维链"
  exit 0
else
  echo
  echo "没找到推理引擎。三选一："
  echo "  ollama（最省事）: https://ollama.com/download"
  echo "  Homebrew:         brew install llama.cpp"
  echo "  源码编译（需要 cmake）:"
  echo "    git clone https://github.com/ggml-org/llama.cpp third_party/llama.cpp"
  echo "    cd third_party/llama.cpp && cmake -B build && cmake --build build -j"
  exit 1
fi
