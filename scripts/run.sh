#!/usr/bin/env bash
# 一条命令跑通：预检 → 确保 ollama 里有模型 → 拉起桌宠服务端（app/server.py）
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "✗ $1"; exit 1; }
ok()   { echo "✓ $1"; }

PORT="${PET_PORT:-8765}"

echo "== digital-me 启动预检 =="

# 1. 模型
MODEL="models/Qwen3-4B-Q4_K_M.gguf"
if [ ! -f "$MODEL" ]; then
  fail "缺少模型。先跑 python3 scripts/fetch_models.py"
fi
SIZE=$(wc -c < "$MODEL" | tr -d ' ')
if [ "$SIZE" -lt 2000000000 ]; then
  fail "模型文件只有 $((SIZE/1024/1024)) MB，应该是 2381 MB——大概率下载不完整，删掉重下"
fi
ok "模型 $(($SIZE/1024/1024)) MB"

# 2. 虚拟环境
if [ ! -x .venv/bin/python ]; then
  fail "缺少 .venv，先跑 ./install.sh"
fi
PY=".venv/bin/python"
ok "Python 环境就绪 ($($PY -V 2>&1))"

# 3. 服务端配置（真配置不进仓，首次从模板生成）
if [ ! -f app/config.yaml ]; then
  cp app/config.example.yaml app/config.yaml
  echo "!  已从 app/config.example.yaml 生成 app/config.yaml（占位符版本）"
  echo "   换成自己的音色：改 tts.gpt_sovits.ref_audio；换成自己的人格：改 persona.character_card"
fi
ok "配置 app/config.yaml"

# 4. 推理引擎：ollama 优先（本机没有 cmake，llama.cpp 不走源码编译）
if command -v ollama >/dev/null 2>&1; then
  NAME="digital-me-local"
  if ! ollama list | grep -q "^${NAME}"; then
    echo "  首次导入模型（约 2.4 GB，只做一次）..."
    printf 'FROM %s\n' "$(cd "$(dirname "$MODEL")" && pwd)/$(basename "$MODEL")" > models/Modelfile
    ollama create "$NAME" -f models/Modelfile
  fi
  ok "ollama 模型就绪：$NAME"
elif command -v llama-server >/dev/null 2>&1; then
  echo "!  检测到 llama-server，但它不提供 /api/chat 的 think 参数"
  echo "   请把 app/config.yaml 的 llm.host 改成 http://127.0.0.1:8080、llm.api_style 改成 openai"
  echo "   （openai 分支会带 reasoning_effort=none，否则整段跑思维链、可见文本为空）"
  echo "   然后另开一个终端：llama-server -m $MODEL -c 4096 --port 8080 --host 127.0.0.1"
else
  echo
  echo "没找到推理引擎。两条路："
  echo "  ollama（最省事）: https://ollama.com/download"
  echo "  源码编译 llama.cpp（需要 cmake）: 见 docs/ARCHITECTURE.md"
  exit 1
fi

# 5. 起服务
echo
echo "== 启动桌宠服务端 =="
echo "  http://127.0.0.1:${PORT}      健康检查 /api/health"
echo "  原生窗口（macOS）：cd app && ./run_mac.sh"
echo "  Ctrl+C 停止"
echo
exec env PET_PORT="$PORT" "$PY" app/server.py --port "$PORT"
