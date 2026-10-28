#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""digital-me 桌宠服务端（标准库实现，除 pyyaml 外零依赖）

职责：
  1. 静态页面托管（桌宠窗口内容）
  2. /api/chat   —— ollama 流式对话（SSE），带人格卡 + 真实时间注入 + 输出兜底
  3. /api/tts    —— TTS 适配器（GPT-SoVITS 优先，系统 TTS 兜底）
  4. /api/asr    —— ASR 适配器（M1 未接模型，返回 501 并说明原因）
  5. /api/latency—— 每轮一行 latency.jsonl（R7），四个时间点由客户端+服务端共同提供
  6. /api/health —— CI smoke test 用

启动：python3 server.py --port 8765
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
VERSION = "0.1.0"

# ---------------------------------------------------------------- 配置

def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG = load_config(APP_DIR / "config.yaml")
LOCK = threading.Lock()
HISTORY: list[dict] = []  # [{"role": "user"|"assistant", "content": str}]

# 输出兜底闸门（conf.yaml 的 output_filter）：命中就整段丢弃
DROP_PATTERNS = [
    re.compile(r"<\|im_start\|>"),
    re.compile(r"<think", re.I),
    re.compile(r"</think", re.I),
    re.compile(r"System\s*Prompt", re.I),
    re.compile(r"系统提示词"),
]


class Persona:
    def __init__(self):
        p = CFG["persona"]
        card_path = (APP_DIR / p["character_card"]).resolve()
        self.name = p.get("fallback_name", "颜小二")
        self.system = p.get("fallback_system", "")
        self.source = "fallback"
        try:
            card = load_config(card_path)
            if card.get("system_prompt"):
                self.system = card["system_prompt"]
                self.name = card.get("name", self.name)
                self.source = str(card_path)
        except Exception as e:  # 卡片缺失不能阻塞 demo
            print(f"[persona] 角色卡读取失败，使用兜底：{e}", file=sys.stderr)

    # 关键性能决策（2026-10-29 实测）：
    # 时间如果写进 system，每轮 system 都变 → ollama 的前缀缓存整段失效 → 每轮重新 prefill
    # 500~870 token，首字从 168 ms 一路涨到 1606 ms（20 轮实测）。
    # 改成「system 固定 + 时间贴在用户消息末尾」后，6 轮实测首字稳定在 222~233 ms。
    TIME_NOTE = "见每条用户消息末尾的括注（格式：现在时间：YYYY-MM-DD HH:MM:SS 星期X）"

    def system_static(self) -> str:
        return self.system.replace("{{runtime_now}}", self.TIME_NOTE)

    def stamp(self) -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %A")


PERSONA = Persona()


def epoch_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- LLM

def ollama_headers() -> dict:
    return {"Content-Type": "application/json"}


def llm_stream(messages: list[dict], on_token):
    """向 ollama 发起流式请求，逐块回调可见文本。返回 (full_text, first_token_epoch_ms, usage)"""
    base = CFG["llm"]["host"].rstrip("/")
    style = CFG["llm"].get("api_style", "ollama")
    options = {
        "num_ctx": CFG["llm"]["num_ctx"],
        "num_predict": CFG["llm"]["num_predict"],
        "temperature": CFG["llm"]["temperature"],
        "top_p": CFG["llm"]["top_p"],
        "repeat_penalty": CFG["llm"]["repeat_penalty"],
        "repeat_last_n": CFG["llm"].get("repeat_last_n", 512),
    }
    if style == "openai":
        url = base + "/v1/chat/completions"
        body = {"model": CFG["llm"]["model"], "messages": messages, "stream": True,
                "reasoning_effort": "none"}  # 必须带，否则 content 为空、全跑思维链
        body.update({k: options[k] for k in ("temperature", "top_p")})
    else:
        url = base + "/api/chat"
        body = {"model": CFG["llm"]["model"], "messages": messages, "stream": True,
                "think": False, "options": options, "keep_alive": CFG["llm"].get("keep_alive", "30m")}

    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=ollama_headers())
    out: list[str] = []
    first_ms = None
    usage = {}
    with urllib.request.urlopen(req, timeout=CFG["llm"]["timeout_s"]) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            if style == "openai":
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                obj = json.loads(payload)
                delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                piece = delta.get("content")
            else:
                obj = json.loads(line)
                piece = (obj.get("message") or {}).get("content")
                if obj.get("done"):
                    usage = {k: obj.get(k) for k in
                             ("prompt_eval_count", "eval_count", "total_duration")}
            if piece:
                # 只有可见字符才算首字；思考链不会走到这里（think=false / reasoning_effort=none）
                if first_ms is None:
                    first_ms = epoch_ms()
                out.append(piece)
                on_token(piece)
    return "".join(out), first_ms, usage


# 只在真的问到时间时才把时间塞进消息。
# 每条都塞的副作用（@llm-wu 实测）：模型盯着时间不放，20 轮里 13 轮把日期写进回复；
# 而且历史里有 20 串时间后缀，prompt 更长、首字更慢（237 vs 141 ms）。
# 只在明确问「现在几点/今天几号」时注入。宽松版（含裸「几点」「多久」）会误伤
# 「你平时几点睡」，模型就会把时间括注原样念出来（实测）。
TIME_TRIGGER = re.compile(
    r"现在几点了?|几点了|今天几号|今天是几号|今天星期几|今天周几|明天几号|"
    r"星期几|周几|什么日期|现在时间|当前时间|报一下时间|现在是什么时候")


# ---- 会话内「记住的事」：20 轮 recall 实测 1/3 失败（答成「辣的东西」），
#      4B 在长上下文里抓不住早期事实。这里用正则抽一句，回放时贴在用户消息末尾
#      （不动 system，前缀缓存不受影响）。
FACTS: list[str] = []
FACT_PATTERNS = [
    (re.compile(r"我(?:最)?(?:讨厌|不喜欢|烦)(?:吃|看|听|闻)?([^\s，。！？、]{1,8})"), "讨厌"),
    (re.compile(r"我(?:最)?(?:喜欢|爱)(?:吃|看|听)?([^\s，。！？、]{1,8})"), "喜欢"),
    (re.compile(r"我(?:的名字)?叫([^\s，。！？、]{1,10})"), "叫"),
    (re.compile(r"我住在?([^\s，。！？、]{1,10})"), "住"),
]
RECALL_HINT = re.compile(r"记得|我说过|刚才|我最|我之前|我叫什么|我住|讨厌|喜欢")


QUESTION_LIKE = re.compile(r"[？?]|吗|什么|啥|哪个|哪些|怎么|多少|几点|谁")


def remember(text: str):
    # 疑问句不抽事实：第 20 轮「我最讨厌吃什么？」会被抽成「讨厌：什么」污染记忆（实测）
    if QUESTION_LIKE.search(text):
        return
    for rx, label in FACT_PATTERNS:
        for m in rx.finditer(text):
            fact = f"{label}：{m.group(1)}"
            with LOCK:
                if fact not in FACTS and len(FACTS) < 8:
                    FACTS.append(fact)


def build_messages(user_text: str) -> list[dict]:
    """system 保持静态（前缀缓存可用）；时间只在命中时间问句时贴到用户消息末尾"""
    msgs = [{"role": "system", "content": PERSONA.system_static()}]
    with LOCK:
        hist = list(HISTORY)
    msgs.extend(hist)
    content = user_text
    if CFG["llm"].get("inject_time_policy", "on_demand") != "off" and TIME_TRIGGER.search(user_text):
        content = f"{user_text}\n（现在时间：{PERSONA.stamp()}）"
    with LOCK:
        facts = list(FACTS)
    if facts and RECALL_HINT.search(user_text):
        plain = "，".join(f.replace("：", "") for f in facts)
        content += f"\n（补一句：用户之前提过 {plain}。）"
    msgs.append({"role": "user", "content": content})
    return msgs


CHAT_LOG = APP_DIR / "logs" / f"chat-{datetime.now().strftime('%Y-%m-%d')}.log"


# 「帮我写周报/邮件/方案」这类请求，4B 会 100% 编内容和进度（@llm-wu 实测 3/3），
# 改 prompt 修不好。客户端先拦一道：没素材就不生成，先要素材。
CREATE_RE = re.compile(r"(写|草拟|生成|拟一?[份个]).{0,8}(周报|日报|月报|邮件|汇报|方案|总结|通知|简历|致辞|讲话稿)")
MATERIAL_RE = re.compile(r"[:：\n]|内容是|如下|这是|素材|记录|我做了|本周|上周|\d")


def creation_guard(text: str) -> str | None:
    """命中创作类请求且没给素材 → 返回拦截话术；否则 None 走模型"""
    if not CREATE_RE.search(text):
        return None
    if MATERIAL_RE.search(text) and len(text) >= 40:
        return None            # 给了素材/要点，交给模型
    return ("写这个得给我素材：干了啥、给谁看、要多少字。"
            "空着我只能编，编出来的你也不敢发。")


def commit_turn(user_text: str, reply: str):
    """写入上下文 + 落一份本地对话日志（R2 证据；只在本地，不进任何仓库）"""
    global HISTORY
    with LOCK:
        HISTORY.append({"role": "user", "content": user_text})
        HISTORY.append({"role": "assistant", "content": reply})
        keep = CFG["memory"]["max_turns"] * 2      # 20 轮 = 40 条消息
        if len(HISTORY) > keep:
            HISTORY = HISTORY[-keep:]
        try:
            CHAT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(CHAT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"),
                                    "user": user_text, "assistant": reply}, ensure_ascii=False) + "\n")
        except OSError:
            pass


def reset_history():
    global HISTORY
    with LOCK:
        HISTORY = []


# ---------------------------------------------------------------- TTS

def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


def tts_gpt_sovits(text: str) -> tuple[bytes, dict]:
    c = CFG["tts"]["gpt_sovits"]
    ref = (APP_DIR / c["ref_audio"]).resolve()
    payload = {
        "text": text,
        "text_lang": c.get("text_lang", "zh"),
        "ref_audio_path": str(ref),
        "prompt_lang": c.get("prompt_lang", "zh"),
        "prompt_text": c.get("ref_text", ""),
        "streaming_mode": False,
        "media_type": "wav",
        "text_split_method": "cut5",
    }
    req = urllib.request.Request(c["endpoint"].rstrip("/") + "/tts",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=c.get("timeout_s", 30)) as r:
        return r.read(), {"provider": "gpt_sovits"}


def tts_macos_say(text: str) -> tuple[bytes, dict]:
    c = CFG["tts"]["macos_say"]
    tmp_aiff = Path(tempfile.mkstemp(suffix=".aiff")[1])
    tmp_wav = Path(tempfile.mkstemp(suffix=".wav")[1])
    try:
        subprocess.run(["say", "-v", c.get("voice", "Flo"), "-r", str(c.get("rate", 200)),
                        "-o", str(tmp_aiff), text], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000",
                        str(tmp_aiff), str(tmp_wav)], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        data = tmp_wav.read_bytes()
        return data, {"provider": "macos_say", "voice": c.get("voice", "Flo")}
    finally:
        for p in (tmp_aiff, tmp_wav):
            try:
                p.unlink()
            except OSError:
                pass


def tts_windows_sapi(text: str) -> tuple[bytes, dict]:
    safe = text.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{tempfile.gettempdir()}\\dm_tts.wav');"
        f"$s.Speak('{safe}');$s.Dispose()"
    )
    wav_path = Path(tempfile.gettempdir()) / "dm_tts.wav"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=90,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return wav_path.read_bytes(), {"provider": "windows_sapi"}


def synth(text: str) -> tuple[bytes, dict]:
    provider = CFG["tts"].get("provider", "auto")
    errors = []
    order = [provider] if provider not in ("auto",) else ["gpt_sovits", "macos_say" if sys.platform == "darwin" else "windows_sapi"]
    for name in order:
        try:
            if name == "gpt_sovits":
                return tts_gpt_sovits(text)
            if name == "macos_say" and sys.platform == "darwin":
                return tts_macos_say(text)
            if name == "windows_sapi" and os.name == "nt":
                return tts_windows_sapi(text)
        except Exception as e:
            msg = f"{name}: {e.__class__.__name__}: {e}"
            errors.append(msg)
            # 兜底要留痕：不然「这轮换了音色」只能靠埋点里翻 provider 才发现
            print(f"[tts] fallback {msg}", file=sys.stderr, flush=True)
    raise RuntimeError("; ".join(errors) or "没有可用的 TTS 后端")


TTS_STRIP = re.compile(r"[*#`>_~|]|```.*?```", re.S)


def clean_for_tts(text: str) -> str:
    t = TTS_STRIP.sub("", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:300]


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = f"digital-me-pet/{VERSION}"
    protocol_version = "HTTP/1.1"

    # 静默日志，只留错误
    def log_message(self, fmt, *args):
        if os.environ.get("DM_VERBOSE"):
            super().log_message(fmt, *args)

    # ---- helpers
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict, extra: dict | None = None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", extra)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ---- routes
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        if path.startswith("/web/"):
            fp = (WEB_DIR / path[len("/web/"):]).resolve()
            if not str(fp).startswith(str(WEB_DIR.resolve())):
                return self._json(403, {"error": "forbidden"})
            ctype = {".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                     ".svg": "image/svg+xml", ".png": "image/png"}.get(fp.suffix, "application/octet-stream")
            return self._file(fp, ctype)
        if path == "/api/health":
            return self._health()
        if path == "/api/config":
            return self._json(200, {
                "name": PERSONA.name, "version": VERSION,
                "persona_source": os.path.basename(PERSONA.source),
                "llm": {"model": CFG["llm"]["model"], "style": CFG["llm"].get("api_style")},
                "tts_provider": CFG["tts"].get("provider"),
                "asr_ready": CFG["asr"].get("provider") != "none",
            })
        return self._json(404, {"error": "not found"})

    def _file(self, path: Path, ctype: str):
        try:
            data = path.read_bytes()
        except OSError:
            return self._json(404, {"error": f"missing {path.name}"})
        self._send(200, data, ctype)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/chat":
                return self.chat()
            if path == "/api/tts":
                return self.tts()
            if path == "/api/asr":
                return self.asr()
            if path == "/api/latency":
                return self.latency()
            if path == "/api/reset":
                reset_history()
                with LOCK:
                    FACTS.clear()
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})
        except BrokenPipeError:
            return
        except Exception as e:
            try:
                self._json(500, {"error": f"{e.__class__.__name__}: {e}"})
            except Exception:
                pass

    # ---- endpoints
    def _health(self):
        t0 = time.time()
        info = {"ok": False, "version": VERSION, "llm": {"model": CFG["llm"]["model"]}, "tts": {}}
        try:
            with urllib.request.urlopen(CFG["llm"]["host"].rstrip("/") + "/api/tags", timeout=3) as r:
                tags = json.loads(r.read())
            names = [m.get("name", "") for m in tags.get("models", [])]
            info["llm"]["reachable"] = True
            info["llm"]["present"] = any(n.split(":")[0] == CFG["llm"]["model"].split(":")[0] for n in names)
            info["llm"]["models"] = names
            info["ok"] = bool(info["llm"]["present"])
        except Exception as e:
            info["llm"]["reachable"] = False
            info["llm"]["error"] = str(e)
        try:
            with urllib.request.urlopen(CFG["llm"]["host"].rstrip("/") + "/api/ps", timeout=3) as r:
                ps = json.loads(r.read())
            loaded = [m.get("name") for m in ps.get("models", [])]
            info["llm"]["loaded"] = loaded
        except Exception:
            info["llm"]["loaded"] = None
        info["tts"] = {"provider": CFG["tts"].get("provider"),
                       "gpt_sovits": CFG["tts"]["gpt_sovits"]["endpoint"]}
        gs = CFG["tts"]["gpt_sovits"]["endpoint"].rstrip("/")
        try:
            with urllib.request.urlopen(gs + "/docs", timeout=1.5) as r:
                info["tts"]["gpt_sovits_up"] = r.status == 200
        except Exception:
            info["tts"]["gpt_sovits_up"] = False
        info["probe_ms"] = int((time.time() - t0) * 1000)
        self._json(200 if info["ok"] else 503, info)

    def chat(self):
        data = self._body()
        text = (data.get("text") or "").strip()
        turn_id = data.get("turn_id") or uuid.uuid4().hex[:12]
        if not text:
            return self._json(400, {"error": "empty text"})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def sse(event: str, obj: dict):
            self.wfile.write(f"event: {event}\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        sse("meta", {"turn_id": turn_id, "model": CFG["llm"]["model"], "t0": epoch_ms()})

        remember(text)
        guard = creation_guard(text)
        if guard:
            sse("token", {"text": guard})
            commit_turn(text, guard)
            sse("done", {"turn_id": turn_id, "t_llm_first_token": epoch_ms(), "ttft_ms": 0,
                         "llm_total_ms": 0, "chars": len(guard), "usage": {}, "guarded": "creation"})
            return

        parts: list[str] = []
        blocked = False

        def on_token(piece: str):
            nonlocal blocked
            parts.append(piece)
            joined = "".join(parts)
            if any(p.search(joined) for p in DROP_PATTERNS):
                blocked = True
                raise RuntimeError("output_filter: 命中提示词泄漏特征")
            sse("token", {"text": piece})

        t_start = time.time()
        try:
            full, first_ms, usage = llm_stream(build_messages(text), on_token)
        except Exception as e:
            if blocked:
                full, first_ms, usage = "", epoch_ms(), {}
                sse("token", {"text": "这个不给看。"})
                full = "这个不给看。"
            else:
                sse("error", {"message": f"{e.__class__.__name__}: {e}"})
                return
        total_ms = int((time.time() - t_start) * 1000)
        commit_turn(text, full)
        sse("done", {"turn_id": turn_id, "t_llm_first_token": first_ms,
                     "ttft_ms": (first_ms - t_start * 1000) if first_ms else None,
                     "llm_total_ms": total_ms, "chars": len(full), "usage": usage})

    def tts(self):
        data = self._body()
        raw = (data.get("text") or "").strip()
        text = clean_for_tts(raw)
        if not text:
            return self._json(400, {"error": "empty text"})
        t0 = time.time()
        try:
            audio, meta = synth(text)
        except Exception as e:
            return self._json(503, {"error": str(e), "provider": CFG["tts"].get("provider")})
        gen_ms = int((time.time() - t0) * 1000)
        self._send(200, audio, "audio/wav", {
            "X-TTS-Provider": meta.get("provider", "?"),
            "X-TTS-Gen-Ms": str(gen_ms),
            "X-TTS-Voice": str(meta.get("voice", "")),
        })

    def asr(self):
        provider = CFG["asr"].get("provider", "none")
        if provider == "none":
            return self._json(501, {
                "error": "ASR 未接入：本机没有识别模型（sherpa-onnx 缺 paraformer/whisper 权重）",
                "hint": "先在 private 仓的 models/ 放模型，再把 config.yaml -> asr.provider 改成 http",
            })
        return self._json(501, {"error": "asr http adapter not wired yet"})

    def latency(self):
        rec = self._body()
        rec["t_server_recv"] = epoch_ms()
        path = (APP_DIR / CFG["logging"]["latency_jsonl"])
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False)
        with LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        self._json(200, {"ok": True, "line": line})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=CFG["server"]["host"])
    ap.add_argument("--port", type=int, default=CFG["server"]["port"])
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"digital-me pet server {VERSION} → http://{args.host}:{args.port}")
    print(f"  人格卡: {PERSONA.source}")
    print(f"  健康检查: http://{args.host}:{args.port}/api/health")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
