#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""20 轮回归的断言层：把 turn20.py 的 json 输出 + 浏览器埋点日志变成 CI 判据。

用法：
    python3 scripts/assert_latency.py /tmp/turn20.json [--latency app/logs/latency.jsonl]

判据（PRD v0.4）：
    M1  首字 P95 ≤ 1000 ms、时间泄漏 ≤ 2 轮、复读 ≤ 2 条、第 20 轮召回成立
    M2  端到端首音 P95 ≤ 3000 ms（owner 2026-10-29 定）
        —— e2e 只由浏览器上报产生；没有日志就 SKIP，绝不填默认值
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def p95(values: list[float]) -> float:
    a = sorted(values)
    if not a:
        raise ValueError("空样本")
    k = (len(a) - 1) * 0.95
    f, c = int(k), min(int(k) + 1, len(a) - 1)
    return round(a[f] + (a[c] - a[f]) * (k - f), 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("turn20_json")
    ap.add_argument("--latency", default="app/logs/latency.jsonl")
    args = ap.parse_args()

    s = json.load(open(args.turn20_json, encoding="utf-8"))["summary"]
    print(f"ttft_p95 = {s['ttft_p95']} ms | date_leak = {s['date_leak_count']} | "
          f"repeat = {s['repeat_count']} | turn20_recall = {s['turn20_recall']}")
    assert s["ttft_p95"] <= 1000, f"M1 首字 P95 {s['ttft_p95']} ms 超过 1000 ms"
    assert s["date_leak_count"] <= 2, "时间注入泄漏超限"
    assert s["repeat_count"] <= 2, "跨轮复读超限"
    assert s["turn20_recall"], "第 20 轮未召回第 1 轮信息（R2）"

    if not os.path.exists(args.latency):
        print("e2e_audio_p95 = SKIP（本次没有浏览器上报的 latency.jsonl，M2 需人工跑一次 UI 会话）")
        print("\n结论: M1 通过；M2 未采样")
        return 0

    rows = [json.loads(l) for l in open(args.latency, encoding="utf-8") if l.strip()][-20:]
    e2e = [r["e2e_audio_ms"] for r in rows if r.get("e2e_audio_ms") is not None]
    if not e2e:
        print("e2e_audio_p95 = SKIP（latency.jsonl 没有 e2e_audio_ms 字段）")
        return 0
    v = p95(e2e)
    sovits = sum(1 for r in rows if r.get("tts_provider") == "gpt_sovits")
    print(f"e2e_audio_p95 = {v} ms（{len(e2e)} 轮，真音色 {sovits}/{len(rows)}）")
    assert v <= 3000, f"M2 端到端首音 P95 {v} ms 超过 3000 ms"
    assert sovits == len(rows), f"M2 正式验收要 20/20 真音色，实际 {sovits}/{len(rows)}"
    print("\n结论: M1 + M2 均通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
