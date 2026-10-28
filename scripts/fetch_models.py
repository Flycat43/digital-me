#!/usr/bin/env python3
"""下载运行所需的模型。支持断点续传，可以重复运行。

用法:
    python3 scripts/fetch_models.py            # 下载全部
    python3 scripts/fetch_models.py --only llm # 只下大模型
    python3 scripts/fetch_models.py --check    # 只检查本地文件是否完整
"""
import argparse
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODELS = {
    "llm": {
        "path": "models/Qwen3-4B-Q4_K_M.gguf",
        "url": "https://modelscope.cn/models/Qwen/Qwen3-4B-GGUF/resolve/master/Qwen3-4B-Q4_K_M.gguf",
        "size": 2497280256,
        "note": "对话模型，Qwen3-4B 的 Q4_K_M 量化",
    },
}

# 这些需要按各自官方说明单独获取，脚本不代劳
MANUAL = [
    ("ASR", "sherpa-onnx 中文模型（SenseVoice / Paraformer）",
     "https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models"),
    ("TTS 底模", "GPT-SoVITS v2Pro 预训练权重",
     "https://github.com/RVC-Boss/GPT-SoVITS"),
    ("Live2D", "官方样例模型（有独立授权，需自行下载）",
     "https://www.live2d.com/download/sample-data/"),
]


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def download(item, dest):
    tmp = dest + ".part"
    have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    if have and have >= item["size"]:
        os.replace(tmp, dest)
        return True
    req = urllib.request.Request(item["url"], headers={"User-Agent": "digital-me/1.0"})
    if have:
        req.add_header("Range", f"bytes={have}-")
        print(f"  从 {human(have)} 处续传")
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "ab") as f:
            total = item["size"]
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                have += len(chunk)
                pct = have * 100 / total if total else 0
                sys.stdout.write(f"\r  {pct:5.1f}%  {human(have)} / {human(total)}   ")
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n  已中断，下次运行会续传")
        return False
    except Exception as e:
        print(f"\n  失败: {e}")
        print("  换个网络重跑本脚本即可，已下载的部分会保留")
        return False
    print()
    if item["size"] and os.path.getsize(tmp) != item["size"]:
        print(f"  大小不符：期望 {item['size']}，实际 {os.path.getsize(tmp)}，文件已删，请重下")
        os.remove(tmp)
        return False
    os.replace(tmp, dest)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=sorted(MODELS), help="只处理某一类")
    ap.add_argument("--check", action="store_true", help="只检查不下载")
    args = ap.parse_args()

    keys = [args.only] if args.only else list(MODELS)
    ok = True
    for k in keys:
        item = MODELS[k]
        dest = os.path.join(ROOT, item["path"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        good = os.path.exists(dest) and (not item["size"] or os.path.getsize(dest) == item["size"])
        print(f"[{k}] {item['note']}")
        if good:
            print(f"  已就绪 {human(os.path.getsize(dest))}")
            continue
        if args.check:
            print("  缺失或不完整")
            ok = False
            continue
        ok = download(item, dest) and ok

    print("\n需要手动获取的：")
    for name, desc, url in MANUAL:
        print(f"  - {name}: {desc}\n    {url}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
