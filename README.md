# digital-me

把你自己做成一个能对话的桌面数字人：本地大模型 + 你自己的音色 + 你自己的人格。

不联网、不上传、不依赖任何云服务。模型跑在你自己的电脑上。

## 它是什么

- **桌面宠物形态**：透明背景、置顶、可拖拽，Live2D 会眨眼和说话
- **语音对话**：说话 → 本地识别 → 本地模型回复 → 用你的音色念出来（也可以只用文字）
- **人格可编辑**：一份 YAML 决定它是谁，不用重新训练
- **音色可克隆**：10 分钟录音，训练出你自己的声音

## 硬件要求

| | 最低 | 推荐 |
|---|---|---|
| 内存 | 8 GB | 16 GB |
| 磁盘 | 10 GB 空闲 | 20 GB 空闲 |
| GPU | 不需要 | NVIDIA 6GB+ 显存（语音克隆速度差 10 倍以上） |
| 系统 | macOS 12+ / Windows 10+ / Linux | 同左 |

CPU 也能跑。本机（Apple Silicon，Qwen3-4B Q4_K_M + GPT-SoVITS，4096 ctx）实测：
首字 **P50 180 ms / P95 255 ms**，端到端首音 **P50 1.29 s / P95 2.06 s**（20 轮真实出声，见 `app/logs/latency.jsonl`）。
有独显时语音克隆接近实时；CPU 上 TTS 是主要成本（RTF 实测 0.32–0.41）。

## 快速开始

```bash
git clone https://github.com/Flycat43/digital-me.git
cd digital-me
./install.sh          # Windows: .\install.ps1
./scripts/fetch_models.py
./scripts/run.sh
```

第一次运行会下载约 5 GB 模型，之后完全离线。

## 换成你自己的声音

1. 按 [docs/RECORDING.md](docs/RECORDING.md) 录 10 分钟干声
2. 把录音放进 `recordings/`
3. 跑训练：

```bash
./scripts/new_voice.sh --name 我的声音 --input recordings/
```

4. 试听 `voice/我的声音/samples/` 里的样本，不满意就回到第 1 步补录

训练出来的模型在 `voice/<名字>/`，已被 `.gitignore` 忽略，不会误传。

## 换成你自己的性格

复制 `config/character.example.yaml` 为 `config/character.yaml`，填：

- `system_prompt`：它是谁，说话什么风格
- `examples`：5-10 组「用户说 X → 我会回 Y」的真实例子（这个最影响像不像）
- `taboos`：不该说的话、不该碰的话题

`config/character.yaml` 也在 `.gitignore` 里。

## 目录结构

```
digital-me/
├─ install.sh / install.ps1    环境安装
├─ config/
│  ├─ conf.example.yaml        运行配置（模型路径、TTS、ASR）
│  └─ character.example.yaml   人格模板
├─ scripts/
│  ├─ fetch_models.py          下载模型
│  ├─ new_voice.sh             音色克隆
│  └─ run.sh / run.bat         启动
├─ assets/live2d/              Live2D 模型（自行获取，见下）
├─ docs/
│  ├─ RECORDING.md             录音规范
│  └─ ARCHITECTURE.md          各模块怎么串起来
└─ voice/                      你的音色模型（不提交）
```

## 关于 Live2D 模型

仓库**不分发** Live2D 模型文件。官方的免费样例模型有自己的授权协议，需要你从 [Live2D 官网](https://www.live2d.com/download/sample-data/) 自行下载后放进 `assets/live2d/`。

想要专属形象，需要委托画师出分层 PSD 再绑定骨骼。

## 隐私

- 所有推理在本地，没有网络请求
- 录音、语料、音色模型都在 `.gitignore` 里
- 如果你 fork 这个仓库，请先确认 `git status` 里没有你的个人数据

## 许可

MIT。第三方组件（llama.cpp、GPT-SoVITS、Open-LLM-VTuber、Live2D SDK）各自遵循其原始许可。
