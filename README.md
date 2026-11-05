# dsh-learn-distill

把网课视频蒸馏成可自足阅读的线性讲义（`deep_mastery`）或考试突击卡片（`exam_cram`）的 skill。

面向 DeepSeek Harness 的 skill 规范编写，也可作为普通 skill 目录被其它 agent 平台读取。

## 它解决什么

网课的原始形态是「几十分钟的屏幕录制 + 一份自动字幕」。直接让模型总结，得到的是转述；
把字幕逐句整理，得到的又是逐字稿。这个 skill 做的是中间那层：先判清素材形态（纯讲解 / 课件翻页 / 板书 / 文档），
再从画面里把讲师写下的公式、例题、图示抽成可对齐的锚点，最后按固定的六栏结构写成一份能脱离视频独立阅读的讲义，
每条内容都带时间锚点，可以回查。

## 流程

```
0 解析参数          agent
1 probe 素材盘点     列分P、过滤介绍页、报告缺口
2 代价闸门           停，等用户确认范围与 token 成本
3 取素材             字幕 / 视频 / 附带文档
4 术语纠错           按学科词库确定性纠正 ASR 错字
5 抽帧 + OCR         稳定段切分 → 终态帧 → 印刷体 OCR，手写页走 VLM
6 对齐               帧 / 字幕 / 讲次三向对齐 → anchors.json
7 六栏写作           agent（每讲次一个）
8 装配               索引与操作手册
9 校验               锚点存在性、时间越界、篇幅比例；盲测复现
```

确定性的事由脚本做，创造性的事由 agent 做，两个闸门由用户做决定。

## 用法

```
# 环境自检
python scripts/env_check.py --config config/local.toml

# 一键编排（跑到锚点就绪后停下）
python scripts/distill.py BV1xxxxxxxxx --goal deep_mastery --config config/local.toml

# 续跑
python scripts/state.py show <course>/dist
```

配置写在 `config/local.toml`（不入 git，复制自 `config/local.toml.example`）。
本机路径只出现在这个文件里，脚本按「配置 → 环境变量 → PATH → 常见位置」的顺序探测。

## 目录

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 入口与流程总览 |
| `references/entry.md` | 入口参数、中文映射、闸门 |
| `references/env.md` | 依赖、模型、环境变量 |
| `references/agent-teams.md` | 子代理与团队分工 |
| `references/orchestration.md` | 长任务编排、会话结束 |
| `references/degradation.md` | 降级方案与上报格式 |
| `references/branches.md` | 方法分支与可测量判据 |
| `references/contracts/chapter_writer.md` | 六栏写作契约 |
| `scripts/` | 确定性脚本 |
| `assets/glossary/` | 多学科术语纠错词库 |
| `config/` | 默认配置与本机覆盖示例 |

## 当前边界

已跑通单讲次全链路。以下能力尚未实现，调用时会按 `references/degradation.md` 显式上报：

- 多分P 逐讲次流水线（当前只对单个讲次产出 anchors）
- 教材 PDF / 课件 PPTX 的结构化解析（CLI 参数已声明，解析未接入）
- 无字幕时的本地 ASR 兜底
- `exam_cram` 的 Anki 卡片产出

## 许可

MIT，见 `LICENSE`。第三方代码来源与许可见 `LICENSES/NOTICE.md`。
