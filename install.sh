#!/usr/bin/env bash
# digital-me 环境安装（macOS / Linux）
# Windows 用 install.ps1
set -euo pipefail
cd "$(dirname "$0")"

echo "== digital-me 环境检查 =="

# 1. Python 3.10+
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "缺少 Python 3.10+。"
  echo "  macOS: brew install python@3.12   或从 python.org 下载"
  echo "  Linux: sudo apt install python3.12"
  exit 1
fi
echo "  Python: $($PY -V)  ($PY)"

# 2. uv（可选，但强烈推荐：装依赖快 10 倍）
if command -v uv >/dev/null 2>&1; then
  echo "  uv: $(uv --version)"
else
  echo "  未检测到 uv，正在安装..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if command -v uv >/dev/null 2>&1; then
    echo "  uv: $(uv --version)"
  else
    echo "  uv 安装失败，将退回 venv + pip"
  fi
fi

# 3. 虚拟环境
if [ ! -d .venv ]; then
  echo "  创建 .venv ..."
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "$PY" >/dev/null
  else
    "$PY" -m venv .venv
  fi
fi

echo "  安装依赖 ..."
if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -r requirements.txt
else
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi

# 4. 可选工具
command -v ffmpeg >/dev/null 2>&1 || echo "  提示: 未装 ffmpeg（音频转换需要）— macOS: brew install ffmpeg"

echo
echo "完成。下一步："
echo "  1) python3 scripts/fetch_models.py    # 下载模型（约 2.5 GB）"
echo "  2) cp config/character.example.yaml config/character.yaml   # 填你的人格"
echo "  3) ./scripts/run.sh"
