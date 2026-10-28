# 变更记录

格式：每节写 **新增 / 修复 / 已知问题 / 回滚**。回滚那节不许省。

## v0.1.0（未发布，待 R14 收口）

### 新增
- `app/` 桌宠客户端：`server.py`（`/api/chat` SSE、`/api/tts`、`/api/health`、`/api/reset`）、
  `web/`（SVG 伪骨骼：呼吸/眨眼/口型跟音量）、`shell_mac.swift`（透明置顶无边框窗口）、
  `run_windows.ps1`（Edge/Chrome `--app` 窗口）。
- 延迟埋点：`logs/latency.jsonl` 一轮一行，`t_user_send` / `t_llm_first_token` / `t_tts_first_audio` / `t_audio_start`。
  `t_llm_first_token` 定义为**可见文本第一个字**（不是 socket 首字节，思维链不计）。
- `scripts/check_repo.py`：发布闸门，一条命令跑 8 项静态检查 + 2 项运行时检查。
- `.github/workflows/ci.yml`：PR/push 必过静态闸门；运行时闸门在自托管 runner 手动触发。
- `.env.example`、`app/config.example.yaml`、`docs/REPO-LAYOUT.md`。

### 修复
- `scripts/run.sh` 现在真的起服务：预检模型 → 确保 ollama 模型 → `exec app/server.py`。
  旧版在有 ollama 时 `exit 0`，桌面端根本没启动。
- `install.sh` 缺 Python 时不再只给 `brew install`：本机无 Homebrew 会直接卡住，
  改为 python.org / uv 优先，Homebrew 仅在有 brew 时提示。
- README 延迟说法改成实测值（首字 P50 180 ms；端到端首音 P50 1.29 s / P95 2.06 s），
  与 `docs/ARCHITECTURE.md` 的拆档一致。
- `.gitignore` 补 `models`（裸名）：`ln -s ../models models` 生成的软链接不受 `models/` 规则约束。

### 已知问题
- ASR 未接入（`/api/asr` 返回 501），Live2D 未接（现为 SVG 伪骨骼），打断未做，Windows 未实测。
- 「写周报/邮件/方案」类请求在 4B 上会编内容，现由客户端拦截要素材，模型侧未解决。
- 事实性推荐（电影/书/工具）靠概率，不保证；PRD 的抽检只有 5 题。

### 回滚
```bash
git checkout v<上一个 tag>          # 例：v0.0.1
./install.sh && ./scripts/run.sh
```
私有侧：`git -C digital-me-private checkout <上一个 tag>`。
权重与录音从不进仓库，回滚不涉及它们。

## v0.0.1（历史骨架）
- 首次提交：安装脚本、模型下载、人格模板、录音规范、架构说明。
