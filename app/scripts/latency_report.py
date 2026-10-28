#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R7 延迟报告：一条命令把 logs/latency.jsonl 变成 P50/P95 表和 markdown。

用法：
    python3 scripts/latency_report.py                 # 读 ../logs/latency.jsonl
    python3 scripts/latency_report.py --since 12      # 只看最近 12 轮
    python3 scripts/latency_report.py --out logs/x.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEED = ("t_user_send", "t_llm_first_token", "t_tts_first_audio", "t_audio_start")


def pct(vals: list[float], p: float) -> float:
    v = sorted(vals)
    if not v:
        return float("nan")
    k = (len(v) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    return v[int(k)] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def summarize(rows: list[dict], key: str) -> dict:
    v = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    if not v:
        return {"n": 0}
    return {"n": len(v), "min": min(v), "p50": pct(v, .5), "p95": pct(v, .95),
            "max": max(v), "mean": st.mean(v)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(ROOT / "logs" / "latency.jsonl"))
    ap.add_argument("--since", type=int, default=0, help="只看最近 N 轮")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    path = Path(args.log)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.since:
        rows = rows[-args.since:]
    if not rows:
        print(f"没有数据：{path}")
        return

    incomplete = [r.get("turn_id") for r in rows if any(r.get(k) is None for k in NEED)]
    metrics = ["ttft_ms", "tts_first_ms", "e2e_audio_ms", "total_ms", "chars", "prompt_tokens"]
    stats = {m: summarize(rows, m) for m in metrics}
    providers = sorted({r.get("tts_provider") or "?" for r in rows})

    def line(m: str, label: str) -> str:
        s = stats[m]
        if not s["n"]:
            return f"| {label} | 0 | — | — | — | — |"
        return (f"| {label} | {s['n']} | {s['min']:.0f} | {s['p50']:.0f} | "
                f"{s['p95']:.0f} | {s['max']:.0f} |")

    out = []
    out.append(f"# 延迟报告 · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out.append("")
    out.append(f"- 数据源：`{path}`（{len(rows)} 轮，TTS 后端：{', '.join(providers)}）")
    out.append(f"- 字段缺失的轮次：{', '.join(incomplete) if incomplete else '无'}")
    out.append("")
    out.append("| 指标 | 样本 | min | P50 | P95 | max |")
    out.append("|---|---|---|---|---|---|")
    out.append(line("ttft_ms", "首字 ttft_ms"))
    out.append(line("tts_first_ms", "首字→首音 tts_first_ms"))
    out.append(line("e2e_audio_ms", "端到端首音 e2e_audio_ms"))
    out.append(line("total_ms", "整轮 total_ms"))
    out.append(line("chars", "回复字数 chars"))
    out.append(line("prompt_tokens", "上下文 prompt_tokens"))
    out.append("")
    out.append("| # | turn_id | 首字 ms | 首音 ms | 端到端 ms | 字数 | prompt | provider |")
    out.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        def f(k):
            v = r.get(k)
            return f"{v:.0f}" if isinstance(v, (int, float)) else "—"
        out.append(f"| {i} | {r.get('turn_id', '?')} | {f('ttft_ms')} | {f('tts_first_ms')} | "
                   f"{f('e2e_audio_ms')} | {f('chars')} | {f('prompt_tokens')} | {r.get('tts_provider') or '?'} |")
    text = "\n".join(out) + "\n"

    print(text)
    out_path = Path(args.out) if args.out else ROOT / "logs" / f"latency-report-{datetime.now().strftime('%Y-%m-%d')}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"[已写入] {out_path}")


if __name__ == "__main__":
    main()
