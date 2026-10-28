# 仓库分层与发布流程

本项目用**两个仓库**隔离代码与个人数据。这条边界是硬约束，不是建议。

| | public：`digital-me` | private：`digital-me-private` |
|---|---|---|
| 放什么 | 框架代码、`*.example.yaml`、测试集（脱敏）、CI、文档 | 角色卡真本、录音/语料、参考音频、质检报告、`config.yaml`、对话日志、评测原始数据 |
| 谁能看 | 所有人 | 只有仓库所有者 |
| 模型权重 | **都不放**：走官方下载 + sha256 校验 | 同左 |

## 目录

```
digital-me/                     # public
├── app/                        桌宠客户端（server / web / swift shell / 埋点脚本）
│   ├── config.example.yaml     占位符配置；真配置是 config.yaml（被 ignore）
│   ├── server.py               /api/chat /api/tts /api/health /api/reset
│   └── scripts/                turn20.py（20 轮回归）、latency_report.py
├── config/character.example.yaml   通用人格卡（不含任何真实对话）
├── scripts/check_repo.py       发布闸门：一条命令跑完全部静态检查
├── scripts/fetch_models.py     按 manifest 下载模型并校验 sha256
├── scripts/run.sh, install.sh  一条命令跑通
├── .env.example                环境变量模板
└── .github/workflows/ci.yml    PR / push 必过的闸门
```

## 一条命令跑通

```bash
git clone https://github.com/Flycat43/digital-me.git && cd digital-me
./install.sh            # 建 .venv、装依赖（缺 Python 时给 uv / python.org 两条路）
./scripts/fetch_models.py   # 下 Qwen3-4B-Q4_K_M.gguf 并校验 sha256
cp app/config.example.yaml app/config.yaml
./scripts/run.sh        # 起服务 → http://127.0.0.1:8765
```

想换成自己的音色，把参考音频放进 `voice/`（已 ignore），改 `app/config.yaml` 的 `tts.gpt_sovits.ref_audio`。

## 发布前必须过闸门

```bash
python3 scripts/check_repo.py          # 静态：泄露 / 大文件 / 语法 / 参数接线
python3 scripts/check_repo.py --live   # 追加：健康检查 / 思维链泄漏 / 延迟
```

检查 ID 与 PRD 的对应：

| ID | 拦什么 | 判据 |
|---|---|---|
| `size-gate` | 大文件进历史 | 单文件 ≤5 MB |
| `media-gate` | 模型/音频入仓 | 无 `*.gguf/*.pth/*.onnx/*.wav/...` |
| `secret-scan` | 密钥、真 `.env` | 0 命中 |
| `pii-scan` | 姓名/手机/邮箱/家目录绝对路径 | 0 命中；私有词表用 secret 注入 |
| `ignore-selfcheck` | `.gitignore` 被改坏 | 必须含 `models/ *.gguf *.pth voice/ data/ .env` |
| `syntax` | 语法错误 | py/sh/json/yaml/js 全部可解析 |
| `ollama-param` | 参数没真下发 | `num_ctx=4096`、`repeat_penalty≥1.2`、`repeat_last_n≥512`、`think=false`、`reasoning_effort=none` |
| `system-stable` | 有人把时间塞进 system | `build_messages` 里 system 只有 1 处且是 `system_static()` |
| `thinking-leak` | 思维链泄漏 / 空回复 | 可见文本 0 个 think 标签、非空、有 `done` |
| `latency-smoke` | 服务不可用 | `/api/health` 200 且 `ok=true` |

**私有词表怎么来**：真实姓名/单位绝不写进本仓的模式里。CI 用 repository secret `PII_EXTRA_TERMS`（逗号分隔）注入；
本地用 `python3 scripts/check_repo.py --extra-file ~/digital-me-private/pii-terms.txt`。

## 发布与回滚

1. `python3 scripts/check_repo.py --live` 全绿；
2. 更新 `CHANGELOG.md`（新增/修复/已知问题三节，**必须写回滚方法**）；
3. `git tag -a v0.x.0 -m "..."` 并 push tag；
4. `gh release create v0.x.0 --notes-file CHANGELOG-<tag>.md`，附件只放**校验清单**（模型 sha256、下载指引），不放权重；
5. 回滚：`git checkout v<上一个 tag> && ./install.sh && ./scripts/run.sh`；私有一侧 `git -C digital-me-private checkout <上一个 tag>`。

权重、录音、日志永不作为 Release 附件上传——需要留档就留在 private 仓或本机磁盘。
