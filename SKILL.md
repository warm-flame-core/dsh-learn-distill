---
name: learn-distill
description: 把网课蒸馏成可自足阅读的线性讲义（deep_mastery），或蒸馏成 Anki 复习卡片（exam_cram）。当用户要求"蒸馏课程"、"蒸馏这门网课"、"把这门课做成讲义/资料"时使用。
whenToUse: 蒸馏课程, 蒸馏网课, 课程蒸馏, 网课转讲义, 网课转卡片
---

# learn-distill：网课蒸馏

## 入口

用户输入包含三要素：**素材** + **"蒸馏课程"** + **目标**。

| 素材形态 | 示例 |
|---|---|
| B 站视频 | `BV1xxxxxxxxx`、`https://www.bilibili.com/video/BV...` |
| 本地视频 | `<本地视频路径>` |
| 附带非视频素材 | 教材 PDF、课件 PPTX/PDF、真题目录 |

| 目标 | 中文表述 |
|---|---|
| `deep_mastery` | 深度掌握、好好学、精通、吃透 |
| `exam_cram` | 考试突击、备考、速成、不挂科 |

**缺 target 或 goal 时只反问一次**，非视频素材是可选，不追问。

参数与解析规则 → `references/entry.md`

## 执行流程

```
0 解析参数          Lead
1 probe 素材盘点     Lead（脚本）→ 列分P、过滤介绍页、报告缺口
2 代价闸门           Lead → 停，等用户确认范围与 token 成本
3 取素材            Lead（脚本）：fetch.py 取字幕/视频 + 本地素材
4 术语纠错          Lead（脚本）：glossary_fix.py
5 抽帧 + OCR        Lead（脚本）：frames.py + ocr.py
6 对齐              Lead（脚本）：align.py → anchors.json
7 六栏写作          子代理（每讲次一个）/ 团队（≥5 讲次）
8 装配              Lead（脚本）：build_doc.py
9 校验              Lead（脚本）：validate.py + 盲测（fresh 子代理）
```

两个强制闸门：Stage 1 确认范围、Stage 2 确认代价 → `references/entry.md`

一键编排：

```
python scripts/distill.py <BV号或本地视频> --goal deep_mastery --config config/local.toml
```

流程跑到 Stage 6（anchors.json 就绪）后停下，等派代理写作。
写完讲义后再跑一次，会自动装配与校验。

续跑：

```
python scripts/state.py show <course>/dist     # 看进度与下一步
python scripts/distill.py <target> --goal ... --resume
```

## 分工原则

- **确定性的事用脚本**：取字幕、纠错、抽帧、OCR、对齐、校验——主代理自己跑
- **创造性的事用子代理**：六栏写作、盲测复现——每讲次一个，任务书自足
- **多讲次（≥5）用智能体团队**：写作组 + 校验组，写范围互斥
- **决策归 Lead**：参数、闸门、最终裁决，不可委派

任务书模板与等待纪律 → `references/agent-teams.md`
长任务编排、会话结束、队友状态判据 → `references/orchestration.md`

## 产物

```
courses/<course-id>/
├── focus.yaml
├── sources/                     原始素材（只读）
├── .distill/                    中间产物（可删）
└── dist/
    ├── _index.md                课程索引
    ├── lecture-NN.md            deep_mastery 讲义
    ├── playbook.md              deep_mastery 操作手册
    ├── cards/                   exam_cram Anki 导入件
    └── _meta/
        ├── anchors.json         锚点索引
        ├── corrections.json     术语纠错审计
        └── validation.json      校验报告
```

## 六栏结构

六栏定义、顺序、每栏要求、锚点规范、来源标记、篇幅比例
→ `references/contracts/chapter_writer.md`

要点：六栏顺序固定，空栏写「本讲无」不省略，每条必须带锚点与来源标记。

## 术语纠错

ASR 对专业术语有系统性错字，用词库确定性纠正（不靠 LLM 猜）。

```
scripts/glossary_fix.py <srt> --subjects math,physics
```

词库在 `assets/glossary/`，按学科分文件。单字规则需 `allow_single` + `context`。
替换记录写入 `corrections.json`，可逐条回溯。

## 环境

```
scripts/env_check.py            # 检查依赖与模型
scripts/setup_env.ps1           # 创建 venv 并安装依赖
```

配置：`config/default.toml`（默认值）+ `config/local.toml`（本机覆盖，不入 git）。

## 检索与修改

产出后可按用户提问定位内容：只从源材料提取并附可验证引用，不做教学推进。
引用格式：`[<章节> · <锚点>] "原文摘录"`。

## 参考文件

| 主题 | 文件 |
|---|---|
| 使用说明（新会话从这里读） | `references/usage.md` |
| 入口参数、中文映射、闸门 | `references/entry.md` |
| 环境、依赖、模型 | `references/env.md` |
| 子代理与团队分工 | `references/agent-teams.md` |
| 长任务编排、会话结束 | `references/orchestration.md` |
| 降级方案 | `references/degradation.md` |
| 方法分支（OCR/判型/抽帧等） | `references/branches.md` |
| 六栏写作契约 | `references/contracts/chapter_writer.md` |

## 禁止事项

| 禁止 | 判据 |
|---|---|
| 编造锚点 | 校验会拦；破坏可回溯性 |
| 公式/例题/考点栏标 `unsupported` | 这三栏是硬事实 |
| 自己推断考点 | 只写讲师明确说过的 |
| 逐句转录讲师原话 | 那是 transcript 的职责 |
| 省略空栏 | 读者会以为漏了内容 |
| 静默降级依赖 | 必须在报告里写明缺什么、用了什么替代 |
