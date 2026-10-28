# 桌宠客户端（desktop pet）— digital-me

M1 目标：**文字对话 + 出声 + 延迟可测量**。本目录是可运行的客户端层，不依赖 Open-LLM-VTuber 的安装，
但链路与它一致（ASR → LLM → TTS 流式）。Live2D 模型尚未接入，当前用 SVG 伪骨骼顶着。

```
app/
├── server.py            # 标准库 HTTP 服务：SSE 对话 / TTS / ASR 占位 / 延迟落盘 / 健康检查
├── config.yaml          # 端口、ollama 参数、人格卡路径、TTS 后端
├── web/                 # 桌宠页面（index.html + pet.css + app.js）
├── shell_mac.swift      # macOS 原生窗口壳：无边框 / 透明 / 常驻最前 / 可拖动
├── bin/digital-me-pet   # swiftc 编译产物（run_mac.sh 会自动重编）
├── run_mac.sh           # macOS 一键启动
├── run_windows.ps1      # Windows 一键启动
├── logs/latency.jsonl   # 每轮一行（R7）
└── shots/               # 截图与 GIF
```

## 启动

### macOS（原生窗口：透明 + 置顶 + 无边框）
```bash
cd ~/Desktop/我的数字人/app
./run_mac.sh
```
首次会自动用 `swiftc` 编译窗口壳（约 8 秒，Xcode Command Line Tools 自带，无需额外安装）。
⌘Q 退出。拖动角色或面板标题栏可移动窗口。

### Windows（Edge/Chrome `--app` 窗口）
```powershell
cd $HOME\Desktop\我的数字人\app
powershell -ExecutionPolicy Bypass -File .\run_windows.ps1
```
Windows 侧用 `--app` 开无地址栏窗口；**透明和常驻最前做不到**，要这两样需加一个依赖 `pywebview`
（等拍板，不擅自加）。

### 纯浏览器（两端通用，调试用）
```bash
# 先起服务
python3 server.py --port 8765
# 浏览器打开
open http://127.0.0.1:8765/          # macOS
start http://127.0.0.1:8765/         # Windows
```

## 接口（给 @孙 做 CI smoke test）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | **健康检查**。200 = ollama 可达且模型存在；503 = 服务活着但模型/ollama 没就绪。返回 `probe_ms`、`llm.loaded`、`tts.gpt_sovits_up` |
| GET | `/api/config` | 前端自检用（人格名、模型名、ASR 是否就绪） |
| POST | `/api/chat` | `{"text": "...", "turn_id": "..."}` → SSE：`meta` / `token` / `done` / `error` |
| POST | `/api/tts` | `{"text": "..."}` → `audio/wav`，响应头带 `X-TTS-Provider`、`X-TTS-Gen-Ms` |
| POST | `/api/asr` | M1 返回 501（无识别模型），接口已就位 |
| POST | `/api/reset` | 清空上下文 |

**端口 8765**（`config.yaml` 可改），只监听 `127.0.0.1`，不对外。

CI 冒烟建议三条：
```bash
curl -sf -m 5 http://127.0.0.1:8765/api/health | jq -e '.ok'         # 健康
curl -s  -m 30 -X POST http://127.0.0.1:8765/api/chat \
     -H 'Content-Type: application/json' -d '{"text":"在吗"}' | grep -q 'event: done'   # 能出字
curl -s  -o /tmp/t.wav -w '%{http_code} %{content_type}\n' -m 60 \
     -X POST http://127.0.0.1:8765/api/tts \
     -H 'Content-Type: application/json' -d '{"text":"测试"}'          # 200 audio/wav
```

## 延迟埋点（R7）

`logs/latency.jsonl`，**一轮一行**，字段：

| 字段 | 来源 | 含义 |
|---|---|---|
| `t_user_send` | 浏览器 | 点发送的时刻（epoch ms） |
| `t_llm_first_token` | 服务端 | **可见文本第一个字**产生的时刻（不是 socket 首字节，思维链不计） |
| `t_tts_first_audio` | 浏览器 | TTS 第一个音频字节到达（响应头） |
| `t_audio_start` | 浏览器 | 真正开始播放（解码后） |
| `ttft_ms` / `tts_first_ms` / `e2e_audio_ms` | 派生 | 首字 / 首字→首音 / 端到端首音 |
| `prompt_tokens` / `gen_tokens` / `chars` | 服务端 | 上下文长度与回复长度，排查变慢用 |
| `tts_provider` | 服务端 | `gpt_sovits` / `macos_say` / `windows_sapi` |

统计（本机 20 轮连聊，2026-10-29 02:53，**TTS 已切到 GPT-SoVITS 真音色**，19/20 轮命中）：

| 指标 | min | P50 | P95 | max |
|---|---|---|---|---|
| 首字 ttft_ms | 147 | **180** | **255** | 306 |
| 首字→首音 tts_first_ms | 536 | 1098 | 1870 | 2929 |
| 端到端首音 e2e_audio_ms | 701 | **1294** | **2062** | 3230 |
| 回复字数 chars | 2 | 11 | 30 | 37 |

GPT-SoVITS 本机实测 RTF **0.32–0.41**（3 种句长各 2 次：0.94 s 音频耗 0.39 s / 6.4 s 音频耗 2.17 s）。
1/20 轮回退到系统音色（`X-TTS-Provider` 会标明），服务端 stderr 记了原因——兜底不是静默的。

> 一条命令出报告：`python3 scripts/latency_report.py` → `logs/latency-report-<日期>.md`

### 运行时策略（都踩过坑才这么写）

- **时间按需注入**：只在问到「现在几点/今天几号/星期几」时才把时间贴到用户消息末尾。
  每条都贴的副作用（@吴 实测）：20 轮里 13 轮把日期写进回复，复读 8 条。现在实测 20 轮 **0 次**日期泄漏。
- **事实便签**：用户说「我讨厌/我喜欢/我叫/我住」时正则抽一句存会话内，问到相关问题时贴在消息末尾。
  20 轮召回（第 20 轮答出第 1 轮的「香菜」）从 1/3 提升到 **3/3**。
- **创作类请求先拦**：命中「写周报/邮件/方案…」且没给素材时，不调模型，直接回「给我素材」。
  4B 这类请求 3/3 编内容，改 prompt 修不好。
- **复读参数**：`repeat_penalty 1.25` + `repeat_last_n 512`（默认 1.1 / 64 压不住跨轮复读，实测 8 条 → 0 条）。

### 一个已修的坑：把时间写进 system 会毁掉前缀缓存
角色卡要求注入真实时间。最初实现是替换 system 里的 `{{runtime_now}}`，结果**每轮 system 都变**，
ollama 的 KV 前缀缓存整段失效，每轮重新 prefill 500~870 token：20 轮里首字从 168 ms 涨到 1606 ms。
改成「system 保持静态 + 时间贴在用户消息末尾」后，同样的 20 轮流程首字 P50 866 → 282 ms；
再把注入改成按需（只答时间问句）后 → **180 ms**。端到端首音 P50 1892 → 1056 → **1294 ms**（后一档换成了真音色，见上）。
旧数据留在 `logs/latency.timein-system.jsonl` / `logs/latency.pre-ondemand.jsonl` 里可复算。

## 说话链路

1. 用户输入 → `POST /api/chat`（SSE 流）
2. 服务端把人格卡 system_prompt 里的 `{{runtime_now}}` 换成真实时间（模型自己不会看表）
3. 前端**按句切分**：凑满一句立刻 `POST /api/tts`，不等整段生成（首音只等第一句）
4. 音频播放时用 WebAudio AnalyserNode 取 RMS 驱动嘴型，角色状态 待机→思考→说话

## TTS 后端

`config.yaml → tts.provider`：
- `gpt_sovits`：POST `http://127.0.0.1:9880/tts`（api_v2 格式，参考音频 `voice-report/clips/ref-A_554s_8s.wav`）。**@周 的服务起来后把 `auto` 改这里即可**，客户端不用改。
- `macos_say` / `windows_sapi`：系统音色，**不是用户音色**，只为把链路跑通、把延迟量出来。
- `auto`（默认）：先试 GPT-SoVITS，不通退系统音色，并在界面提示「音色未就绪」。

## 已知限制（别当成 bug 掩盖）

- **ASR 未接**：本机没有识别模型（sherpa-onnx 缺 paraformer/whisper 权重），`/api/asr` 返回 501，输入框旁的麦克风是灰的。
- **Live2D 未接**：现在是 SVG 伪骨骼（呼吸/眨眼/口型/倾斜）。换 Live2D 需要 `.model3.json` + Cubism Core，工作量估 1.5–2 天。
- **TTS 是占位音色**：`macos_say`，不是用户音色；GPT-SoVITS RTF 还没实测（@周）。
- **Windows 未实测**：本机只有 macOS，`run_windows.ps1` 按标准写法给出，未跑过。

## 隐私

`config.yaml` 指向 `../config-draft/character.yaml`（含真名与真实对话），**不进 public 仓**。
public 仓只放 `config/character.example.yaml`。
