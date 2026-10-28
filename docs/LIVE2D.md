# Live2D 模型怎么进仓（许可与体积双重约束）

结论先说：**模型文件不进 public 仓**，安装时下载，用户自己点同意许可。

## 为什么不能直接 vendor 进来

Live2D 官方的 Sample Data 不是自由许可，条款里有三条会直接影响本仓：

1. **要同意两份协议**才能用：Free Material License Agreement + Terms of Use for Live2D Cubism Sample Data。
   仓库里塞一份模型 = 替所有 clone 的人默认接受了协议，这不行。
2. **商用范围按主体规模分档**：General User / 小规模事业者（年销售额 < 1000 万日元）商用与非商用都可以；
   中大型主体只能**私有测试**，对外宣传要单独申请书面许可。
3. **合作角色（Collaboration Character）明确禁止**：既不能商用，也不能修改、不能分发。

条款出处（2026-10-29 核对）：
- https://www.live2d.com/en/learn/sample/model-terms
- https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html

另外 `Unity-chan` 与 `Hatsune Miku` 是明确排除在“自由使用”之外的，别拿它们当默认模型。

## 所以本仓这么做

```
assets/live2d/           # 只有 .gitkeep，模型文件被 .gitignore 挡掉
scripts/fetch_live2d.py  # 官方源下载 + sha256 校验 + 打印许可提示
```

- `.gitignore` 已挡 `assets/live2d/**`（保留 `.gitkeep`），`media-gate` 会把任何 `.moc3 / .model3.json / .png` 图集拦下来。
- 首次启动提示用户去官方页面阅读并同意条款，再执行下载；不同意就退回内置的 SVG 伪骨骼（`app/web/` 里那套，零依赖、零许可问题）。
- **不把模型打包进 Release 附件**——Release 只放校验清单，理由同上：分发即再分发。

## 自绘模型的路线（如果以后要商用发布）

自己用 Cubism Editor 做的模型，版权在自己手上，可以随仓走（仍受 Cubism SDK 的运行时许可约束，SDK 不是开源的）。
这条要在 `LICENSE` 里单独写清：代码许可 ≠ 模型许可 ≠ SDK 许可，三者分开声明。

## 对演示的影响

M1 用开源 sample 模型演示没问题（个人/小规模主体条款覆盖）。但**演示截图、录屏发到公开平台**时，
画面里出现的角色仍受第 3 条（不适内容）约束，别配擦边台词。这条写进 PRD 的演示脚本检查项。
