# 架构

## 四个部件

```
麦克风 → [ASR] → 文字 → [LLM] → 文字 → [TTS] → 音频 → 扬声器
                                    ↓
                              [Live2D 桌宠]
                              表情 + 口型
```

| 部件 | 选型 | 为什么 |
|---|---|---|
| ASR 语音识别 | sherpa-onnx（SenseVoice / Paraformer） | CPU 实时，中文准，不需要 GPU |
| LLM 对话 | llama.cpp + Qwen3-4B GGUF | 2.4 GB，16GB 内存的笔记本能跑，中文口语好 |
| TTS 语音合成 | GPT-SoVITS v2Pro | 1 分钟素材就能克隆，中文音色相似度最高 |
| 界面 | Open-LLM-VTuber + Live2D | 跨平台，自带桌宠模式（透明、置顶、穿透） |

## 为什么不用一个现成方案打包

现成的「AI 伴侣」项目通常三条路：绑定云 API、只支持 Windows、或者把音色锁死。
这个项目要的是**全本地 + 可换音色 + 跨平台**，只能自己把四块拼起来。

## 数据流细节

1. **唤醒/打断**：常驻监听，检测到人声就停掉正在播的 TTS
2. **ASR**：流式识别，说完一句立刻出文字
3. **LLM**：流式输出，第一个 token 就上屏（不等全文）
4. **TTS**：按标点切句，第一句合成完就播，后面边播边合成
5. **口型**：把音频的音量包络映射到 Live2D 的 `ParamMouthOpenY`

第 4 步是延迟的关键。等全文合成完再播，体感会慢 3 秒以上；分句追播能把首音压到 1 秒左右。

## 延迟预算（实测参考）

| 环节 | M3 MacBook Air | Windows + RTX 4060 |
|---|---|---|
| ASR（一句 5 秒） | 0.3 s | 0.2 s |
| LLM 首 token | 0.4 s | 0.2 s |
| TTS 首句 | 2.5-3.5 s | 0.3 s |
| **合计首音** | **3-4 s** | **0.7 s** |

Mac 上声音克隆是 CPU 推理，GPT-SoVITS 官方实测 RTF 0.526（M4 CPU），即生成 10 秒音频要 5.3 秒。有 N 卡时 RTF 0.028，快 19 倍。

## 本机实测（M3 MacBook Air / 16 GB）

Qwen3-4B Q4_K_M，通过 ollama 导入 gguf：

| 指标 | 实测 |
|---|---|
| 生成速度 | 30.08 tok/s |
| 预填充 | 49.67 tok/s |
| 模型加载 | 1.54 s |
| 文件大小 | 2,497,280,256 字节（校验一致） |

TTS（GPT-SoVITS v2Pro，CPU，zero-shot 参考音频）：

| 指标 | 实测 |
|---|---|
| 稳态 RTF | **0.385–0.405**（生成 10.54 s 音频用 4.06 s） |
| 首次调用 RTF | 1.073（含模型预热） |
| 模型加载 | 0.72 s |
| 20 字合成耗时 | 3.55 s → 8.76 s 音频 |
| 合成音频底噪 | -44.2 dBFS（源素材 -39.4，底噪被带进输出） |

**这个 0.39 比官方 M4 的 0.526 还好**，说明 v2Pro 在 Apple Silicon CPU 上比预期快。

**延迟结论**：整句合成完再播，首音 ≈ 3.5–4 s，刚好卡 M1 底线；按标点分句、第一分句合成完就播，首音可压到 **1.5–2 s**，M2 的 2 秒目标成立。

**坑**：Qwen3 默认开启思考模式，简单问一句会先生成 300-400 token 的思维链再回答，首字延迟被拉长好几倍。对话场景必须在 prompt 末尾加 `/no_think`，或者用 `enable_thinking=false` 的模板。

## 目录与数据边界

```
models/          模型权重（.gitignore，不进仓库）
voice/           音色模型（.gitignore，走私有仓 Release）
recordings/      原始录音（.gitignore）
config/character.yaml   人格（.gitignore，私人）
```

public 仓库只有代码、模板、文档。任何和「具体某个人」有关的东西都在 private 仓库或本地。

## 后续可换的部件

- **换大模型**：`scripts/fetch_models.py` 里加一条，改 `conf.yaml` 指向新 gguf
- **换 TTS**：CosyVoice2 流式更快但音色相似度略低；IndexTTS2 情感最好但最重
- **换形象**：Live2D 模型放进 `assets/live2d/`，改 `conf.yaml` 的模型路径
- **换 ASR**：whisper.cpp 精度更高但慢，sherpa-onnx 是速度优先
