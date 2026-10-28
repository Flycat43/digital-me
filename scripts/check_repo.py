#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""digital-me 仓库闸门：一条命令跑完发布前必过的检查。

用法：
    python3 scripts/check_repo.py            # 静态闸门（CI 默认，无网络、无模型）
    python3 scripts/check_repo.py --live     # 追加运行时闸门（需本机 ollama + 8765 服务）
    python3 scripts/check_repo.py --list     # 只列检查项

检查项与 PRD §8 的 ID 一一对应：
    size-gate / media-gate / secret-scan / pii-scan / ignore-selfcheck /
    syntax / ollama-param / system-stable          —— 静态
    thinking-leak / latency-smoke                  —— 运行时（--live）

设计要点：公开仓里**不放任何真实姓名/手机号/单位**做匹配模式，
私有词表由 --extra-file 或环境变量 PII_EXTRA_TERMS 注入（CI 用 repository secret）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SIZE_LIMIT = 5 * 1024 * 1024  # 单文件 5 MB 硬闸门

MEDIA_EXT = {
    ".gguf", ".pth", ".ckpt", ".onnx", ".safetensors", ".bin",
    ".wav", ".m4a", ".mp3", ".flac", ".aac", ".ogg",
}

SECRET_PATTERNS = [
    (r"ghp_[A-Za-z0-9]{36}", "GitHub PAT"),
    (r"github_pat_[A-Za-z0-9_]{50,}", "GitHub fine-grained PAT"),
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI 风格 key"),
    (r"AKIA[0-9A-Z]{16}", "AWS AK"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"-----BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY-----", "私钥"),
    (r"""(?i)(api[_-]?key|secret|passwd|password|access[_-]?token)\s*[:=]\s*["'][^"'<>{}$\s]{16,}["']""",
     "硬编码密钥赋值"),
]

PII_PATTERNS = [
    (r"[\w.+-]+@(?!example\.(com|org)|test\.local)[\w-]+\.[A-Za-z]{2,}", "邮箱"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号"),
    (r"(?<!\d)\d{17}[\dXx](?!\d)", "身份证号"),
    (r"/Users/[A-Za-z0-9._-]+/", "macOS 家目录绝对路径"),
    (r"[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9._-]+", "Windows 家目录绝对路径"),
]

IGNORE_REQUIRED = ["models/", "*.gguf", "*.pth", "voice/", "data/", ".env"]

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "bin"}

# 允许出现在仓里的二进制（演示图，非运行依赖）
MEDIA_ALLOWLIST = {"app/shots/pet-screenshot.png", "app/shots/pet-demo.gif"}

LIVE_QUESTIONS = [
    "在吗",
    "今天几号",
    "我最讨厌吃什么？",
    "推荐个三部曲电影",
    "给我讲讲你自己",
]


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, cid: str, ok: bool, detail: str = "") -> None:
        self.rows.append((cid, ok, detail))

    def report(self, title: str) -> bool:
        print(f"\n== {title} ==")
        width = max(len(c) for c, _, _ in self.rows)
        for cid, ok, detail in self.rows:
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {cid.ljust(width)}  {detail}")
        bad = [r for r in self.rows if not r[1]]
        print(f"  -> {len(self.rows) - len(bad)}/{len(self.rows)} 通过")
        return not bad


def tracked_files() -> list[Path]:
    """要进仓的文件 = 已跟踪 + 未跟踪但未被 ignore（即 git add 会收进来的那批）。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "-c", "-o", "--exclude-standard"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout.decode()
        files = [ROOT / p for p in out.split("\0") if p]
        if files:
            return files
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    files = []
    for p in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file():
            files.append(p)
    return files


def text_files(files: list[Path]) -> list[Path]:
    out = []
    for p in files:
        if p.suffix.lower() in MEDIA_EXT or p.suffix.lower() in {".png", ".gif", ".jpg", ".zip"}:
            continue
        try:
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            p.read_text(encoding="utf-8")
            out.append(p)
        except (UnicodeDecodeError, OSError):
            continue
    return out


def load_extra_terms(args) -> list[str]:
    terms: list[str] = []
    if args.extra_file:
        p = Path(args.extra_file).expanduser()
        if p.exists():
            terms += [t.strip() for t in p.read_text(encoding="utf-8").splitlines() if t.strip()]
        else:
            print(f"  ! 词表不存在，跳过: {p}")
    env = os.environ.get("PII_EXTRA_TERMS", "")
    terms += [t.strip() for t in re.split(r"[,;|\s]+", env) if t.strip()]
    return [t for t in dict.fromkeys(terms) if t]


def static_gates(r: Result, args) -> None:
    files = tracked_files()
    rel = [p.relative_to(ROOT).as_posix() for p in files]

    # 1 size-gate
    big = [(p, p.stat().st_size) for p in files if p.stat().st_size > SIZE_LIMIT]
    r.add("size-gate", not big,
          "无 >5MB 文件" if not big else "; ".join(f"{p.relative_to(ROOT)} {s // 1024 // 1024}MB" for p, s in big))

    # 2 media-gate（顺带拦软链接：ln -s ../models models 能绕过 models/ 规则）
    media = [p.relative_to(ROOT).as_posix() for p in files
             if p.suffix.lower() in MEDIA_EXT and p.relative_to(ROOT).as_posix() not in MEDIA_ALLOWLIST]
    links = [p.relative_to(ROOT).as_posix() for p in files if p.is_symlink()]
    if links:
        media += [f"{x}(软链接)" for x in links]
    r.add("media-gate", not media, "无模型/音频文件、无软链接" if not media else ", ".join(media[:6]))

    # 3 secret-scan
    hits = []
    for p in text_files(files):
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for pat, name in SECRET_PATTERNS:
            for m in re.finditer(pat, body):
                hits.append(f"{p.relative_to(ROOT)}:{body[:m.start()].count(chr(10)) + 1} {name}")
    dotenv = [x for x in rel if Path(x).name == ".env" or Path(x).name.startswith(".env.") and Path(x).name != ".env.example"]
    r.add("secret-scan", not hits and not dotenv,
          "无密钥" if not hits and not dotenv else "; ".join((hits + [f"真 .env 入仓: {d}" for d in dotenv])[:6]))

    # 4 pii-scan
    terms = load_extra_terms(args)
    pii = []
    for p in text_files(files):
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for pat, name in PII_PATTERNS:
            for m in re.finditer(pat, body):
                pii.append(f"{p.relative_to(ROOT)}:{body[:m.start()].count(chr(10)) + 1} {name}")
        low = body.lower()
        for t in terms:
            if t.lower() in low:
                pii.append(f"{p.relative_to(ROOT)} 私有词命中")
    detail = "无个人信息" if not pii else "; ".join(pii[:8])
    if not terms:
        detail += "（未注入私有词表，仅通用模式）"
    r.add("pii-scan", not pii, detail)

    # 5 ignore-selfcheck
    gi = (ROOT / ".gitignore")
    body = gi.read_text(encoding="utf-8") if gi.exists() else ""
    missing = [x for x in IGNORE_REQUIRED if x not in body]
    r.add("ignore-selfcheck", not missing,
          ".gitignore 覆盖 models/ *.gguf *.pth voice/ data/ .env" if not missing else "缺: " + ", ".join(missing))

    # 6 syntax
    bad = []
    for p in files:
        s = p.suffix.lower()
        try:
            if s == ".py":
                subprocess.run([sys.executable, "-m", "py_compile", str(p)], check=True,
                               capture_output=True, timeout=60)
            elif s == ".sh":
                subprocess.run(["bash", "-n", str(p)], check=True, capture_output=True, timeout=30)
            elif s == ".json":
                json.loads(p.read_text(encoding="utf-8"))
            elif s == ".yaml":
                try:
                    import yaml
                    yaml.safe_load(p.read_text(encoding="utf-8"))
                except ImportError:
                    pass
        except Exception as e:  # noqa: BLE001
            bad.append(f"{p.relative_to(ROOT)}: {type(e).__name__}")
    node = subprocess.run(["bash", "-lc", "command -v node"], capture_output=True).returncode == 0
    if node:
        for p in files:
            if p.suffix.lower() == ".js":
                if subprocess.run(["node", "--check", str(p)], capture_output=True).returncode != 0:
                    bad.append(f"{p.relative_to(ROOT)}: node --check 失败")
    r.add("syntax", not bad,
          f"py/sh/json/yaml{'/js' if node else '（无 node，跳过 js）'} 全部可解析"
          if not bad else "; ".join(bad[:6]))

    # 7 ollama-param：参数不仅要写进 example，还要真的被 server.py 发出去
    cfg = ROOT / "app" / "config.example.yaml"
    problems = []
    if not cfg.exists():
        problems.append("缺 app/config.example.yaml")
    else:
        try:
            import yaml
            c = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            llm = c.get("llm", {}) or {}
            checks = {
                "llm.num_ctx == 4096": llm.get("num_ctx") == 4096,
                "llm.repeat_penalty >= 1.2": float(llm.get("repeat_penalty", 0) or 0) >= 1.2,
                "llm.repeat_last_n >= 512": int(llm.get("repeat_last_n", 0) or 0) >= 512,
                "llm.inject_time_policy == on_demand": llm.get("inject_time_policy") == "on_demand",
            }
            problems += [k for k, ok in checks.items() if not ok]
        except Exception as e:  # noqa: BLE001
            problems.append(f"解析失败 {e}")
    sp_src = (ROOT / "app" / "server.py")
    if sp_src.exists():
        src = sp_src.read_text(encoding="utf-8")
        wired = {
            'ollama 分支发 "think": False': re.search(r'"think"\s*:\s*False', src) is not None,
            'openai 分支发 reasoning_effort="none"': re.search(r'"reasoning_effort"\s*:\s*"none"', src) is not None,
            "options 里带 repeat_last_n": re.search(r'"repeat_last_n"\s*:', src) is not None,
            "options 里带 num_ctx": re.search(r'"num_ctx"\s*:', src) is not None,
        }
        problems += [k for k, ok in wired.items() if not ok]
    else:
        problems.append("缺 app/server.py，无法验证参数是否真的下发")
    r.add("ollama-param", not problems,
          "ctx 4096 / repeat 1.25+512 / think=false / reasoning_effort=none 均已接线"
          if not problems else "; ".join(problems))

    # 8 system-stable：system 必须恒定（时间只贴用户消息尾部），否则 KV 前缀缓存整段失效
    sp = ROOT / "app" / "server.py"
    problems = []
    if not sp.exists():
        problems.append("缺 app/server.py")
    else:
        srcl = sp.read_text(encoding="utf-8").splitlines()
        start = next((i for i, l in enumerate(srcl) if l.startswith("def build_messages")), None)
        if start is None:
            problems.append("找不到 build_messages")
        else:
            blk = []
            for line in srcl[start + 1:]:
                if line.strip() and not line[:1].isspace():
                    break          # 回到顶层缩进 = 函数结束
                blk.append(line)
            text = "\n".join(blk)
            sys_lines = [l for l in blk if '"role": "system"' in l]
            if len(sys_lines) != 1:
                problems.append(f"build_messages 里 system 消息有 {len(sys_lines)} 处，应恰为 1 处")
            elif "system_static()" not in sys_lines[0]:
                problems.append("system 不是 system_static()（system 会随轮次变化）")
            for l in sys_lines:
                if "stamp(" in l or "datetime" in l:
                    problems.append("system 里出现时间戳")
        allowed = 1 if any("def system_static" in l for l in srcl) else 0
        uses = sum(l.count("runtime_now") for l in srcl)
        if uses > allowed:
            problems.append(f"runtime_now 出现 {uses} 次，仅允许 {allowed} 次（system_static 内替换成固定说明）")
    r.add("system-stable", not problems,
          "system 恒定（system_static），时间只贴用户消息尾部" if not problems else "; ".join(problems))


def http_json(path: str, port: int, timeout: float = 30.0):
    import time
    t0 = time.time()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return resp.status, raw, (time.time() - t0) * 1000


def live_gates(r: Result, args) -> None:
    port = args.port
    # latency-smoke: health 必须 200 且 .ok == true
    try:
        code, raw, ms = http_json("/api/health", port)
        ok = code == 200 and json.loads(raw).get("ok") is True
        r.add("latency-smoke", ok, f"/api/health {code} {ms:.0f} ms ok={ok}")
    except (urllib.error.URLError, TimeoutError) as e:
        r.add("latency-smoke", False, f"/api/health 不可达: {e}")
        r.add("thinking-leak", False, "服务不可达，未测")
        return

    # thinking-leak: 可见文本不得含 think 标签、不得为空、不得回 error 事件
    leaks = []
    for q in LIVE_QUESTIONS:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/chat",
                data=json.dumps({"text": q}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            leaks.append(f"{q}: 请求失败 {type(e).__name__}")
            continue
        visible, errors, done, cur = [], [], False, ""
        for line in body.splitlines():
            if line.startswith("event: "):
                cur = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    obj = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if cur == "token":
                    visible.append(obj.get("text", ""))
                elif cur == "error":
                    errors.append(obj.get("message", "")[:60])
                elif cur == "done":
                    done = True
        text = "".join(visible)
        if not done:
            leaks.append(f"{q}: 没收到 done 事件（流中断）")
        if errors:
            leaks.append(f"{q}: error 事件 {errors[0]}")
        if "think>" in text or "think<" in text:
            leaks.append(f"{q}: 可见文本含 think 标签")
        if not text.strip():
            leaks.append(f"{q}: 可见文本为空")
    r.add("thinking-leak", not leaks,
          f"{len(LIVE_QUESTIONS)} 条问句 0 标签 / 0 空回复 / 均含 done" if not leaks else "; ".join(leaks[:5]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="追加运行时检查（需 ollama + 8765）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--extra-file", default=os.environ.get("PII_EXTRA_FILE", ""),
                    help="私有词表路径（一行一个词），不进公开仓")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("静态: size-gate media-gate secret-scan pii-scan ignore-selfcheck syntax ollama-param system-stable")
        print("运行时(--live): latency-smoke thinking-leak")
        return 0

    r = Result()
    static_gates(r, args)
    ok = r.report("静态闸门")
    if args.live:
        live_gates(r, args)
        ok = r.report("含运行时闸门") and ok
    print("\n结论:", "通过，可发布" if ok else "未通过，禁止打 tag")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
