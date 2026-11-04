# dsh-learn-distill

把网课视频蒸馏成可自足阅读的线性讲义（`deep_mastery`）或考试突击卡片（`exam_cram`）的 skill。

面向 DeepSeek Harness 的 skill 规范编写，也可作为普通 skill 目录被其它 agent 平台读取。

## 状态

骨架与确定性流水线已跑通：B 站链接 → 列分P → 取字幕 → 抽帧 → OCR → 对齐 → 装配 → 校验。
六栏写作由 agent 按 `references/contracts/chapter_writer.md` 完成，脚本不参与。

尚未实现（见 `references/degradation.md` 的降级约定）：

- 多分P 逐讲次流水线（当前只对单个讲次产出 anchors）
- 教材 PDF / 课件 PPTX 的结构化解析（CLI 参数已声明，解析未接入）
- 无字幕时的本地 ASR 兜底
- `exam_cram` 的 Anki 卡片产出

## 用法

```
python scripts/env_check.py --config config/local.toml
python scripts/distill.py BV1xxxxxxxxx --goal deep_mastery --config config/local.toml
```

流程跑到锚点就绪后停下，等 agent 写作；写完再跑一次，自动装配与校验。

## 目录

| 目录 | 内容 |
|---|---|
| `SKILL.md` | 入口与流程总览 |
| `references/` | 入口参数、环境、编排、降级、方法分支、写作契约 |
| `scripts/` | 确定性脚本（取素材、抽帧、OCR、对齐、装配、校验） |
| `assets/glossary/` | 多学科术语纠错词库 |
| `config/` | 默认配置与本机覆盖示例 |

## 许可

MIT。第三方代码来源与许可见 `LICENSES/NOTICE.md`。
