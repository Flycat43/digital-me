#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""20 轮口语回归：一条命令复现「时间注入 / 复读 / 首字」三组指标。

用法：
    python3 scripts/turn20.py                 # 打本机 8765
    python3 scripts/turn20.py --port 8765 --json out.json

判据（PRD R2/R3 相关）：
    · 第 20 轮要能引用第 1 轮信息（默认素材：香菜）
    · 20 轮里把日期写进回复的条数应 ≤ 2
    · 完全重复的回复条数应 ≤ 2
    · 首字 P95 ≤ 1000 ms
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from collections import Counter

QUESTIONS = [
    "我最讨厌吃香菜，记住啊", "今天想干点啥", "随便聊两句", "你吃饭了吗", "最近忙不忙",
    "给我讲个冷笑话", "周末去哪玩", "我不太想去人多的地方", "推荐个电影", "你怎么看加班",
    "我在学AI训练师", "数据集质量怎么判断", "有点困了", "说句鼓励我的话", "你平时几点睡",
    "我妈昨天说啥了", "你是不是AI", "好了差不多了", "再聊一句", "我最讨厌吃什么？",
]
DATE_RE = re.compile(r"周[一二三四五六日天]|星期[一二三四五六日天]|20\d\d年|\d+月\d+日")


def post(path: str, body: dict, port: int) -> str:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode()


def one_turn(text: str, port: int) -> tuple[str, float | None, dict]:
    """返回 (回复全文, 首字毫秒, done 事件)"""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/chat", data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    first = None
    out = []
    done = {}
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            obj = json.loads(line[5:].strip())
            if obj.get("text") and "turn_id" not in obj:
                if first is None:
                    first = (time.time() - t0) * 1000
                out.append(obj["text"])
            elif "turn_id" in obj:
                done = obj
    return "".join(out), first, done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    post("/api/reset", {}, args.port)
    replies, ttfts = [], []
    for i, q in enumerate(QUESTIONS, 1):
        reply, ttft, done = one_turn(q, args.port)
        replies.append(reply)
        if ttft:
            ttfts.append(ttft)
        print(f"{i:2d} [{ttft or 0:5.0f} ms] {q} → {reply}")

    date_hits = [i + 1 for i, r in enumerate(replies) if DATE_RE.search(r)]
    dup = [(k, v) for k, v in Counter(replies).items() if v > 1 and len(k) >= 2]
    ttfts_sorted = sorted(ttfts)

    def pct(p):
        if not ttfts_sorted:
            return float("nan")
        k = (len(ttfts_sorted) - 1) * p
        f, c = int(k), min(int(k) + 1, len(ttfts_sorted) - 1)
        return ttfts_sorted[f] + (ttfts_sorted[c] - ttfts_sorted[f]) * (k - f)

    recall = "香菜" in replies[-1]
    summary = {
        "turns": len(replies),
        "ttft_p50": round(pct(.5), 1), "ttft_p95": round(pct(.95), 1),
        "date_leak_turns": date_hits, "date_leak_count": len(date_hits),
        "repeat_pairs": dup, "repeat_count": sum(v - 1 for _, v in dup),
        "turn20_recall": recall, "turn20_reply": replies[-1],
        "median_chars": sorted(len(r) for r in replies)[len(replies) // 2],
    }
    print("\n=== 汇总 ===")
    for k, v in summary.items():
        print(f"{k}: {v}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "replies": replies}, f, ensure_ascii=False, indent=2)
        print(f"[已写入] {args.json}")


if __name__ == "__main__":
    main()
