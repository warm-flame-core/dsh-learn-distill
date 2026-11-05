# 使用说明（新会话直接可用）

## 一、前置条件

| 项 | 要求 |
|---|---|
| Python | ≥3.13（`onnxruntime` 无 cp315 wheel） |
| venv | `.venv/`（`scripts/setup_env.ps1` 默认位置） |
| FFmpeg | 有，路径写进 `config/local.toml` |
| yutto | 有，路径写进 `config/local.toml` |
| B站登录 | `yutto auth login --auth-file <路径>` |
| VLM 模型 | Qwen2.5-VL GGUF + mmproj（手写识别用） |
| llama.cpp | `llama-mtmd-cli.exe` |

自检：

```
python scripts/env_check.py --config config/local.toml
```

## 二、配置

`config/local.toml`（不入 git，复制自 `local.toml.example`）：

```toml
[paths]
root = "<课程根目录>"

[tools]
ffmpeg = "<ffmpeg 绝对路径>"
ffprobe = "<ffprobe 绝对路径>"
yutto = "<yutto 绝对路径>"

[models]
hf_home = "<HF 缓存目录>"
qwen_vl_model = "<Qwen2.5-VL GGUF 路径>"
qwen_vl_mmproj = "<mmproj 路径>"

[ocr]
llama_bin = "<llama-mtmd-cli 路径>"

[bilibili]
auth_file = "<yutto auth.toml 路径>"
```

## 三、开始蒸馏

### 3.1 B 站课程

```
python scripts/distill.py BV1xxxxxxxxx --goal deep_mastery \
  --config config/local.toml
```

先跑一段验证：

```
python scripts/distill.py BV1xxxxxxxxx --goal deep_mastery \
  --pages 1 --sections "00:00:00-00:05:00" \
  --config config/local.toml
```

### 3.2 本地视频

```
python scripts/distill.py "<本地视频路径>" --goal exam_cram \
  --config config/local.toml
```

### 3.3 只列分P不下载

```
python scripts/fetch.py list BV1xxxxxxxxx -o pages.json
```

输出会标出哪些分P被判为介绍页/广告（自动排除）。

### 3.4 参数

| 参数 | 说明 |
|---|---|
| `--goal` | **必填**：`deep_mastery` / `exam_cram` |
| `--course` | 课程 id，不填则从 BV 号/文件名生成 |
| `--pages` | 分P范围，如 `1-5` 或 `1,3,5` |
| `--sections` | 只跑片段，如 `00:00:00-00:05:00` |
| `--textbook` | 教材 PDF |
| `--slides` | 课件 PDF/PPTX |
| `--exams` | 真题目录 |
| `--emphasis` | `subject` / `course` / `textbook` |
| `--dry-run` | 只列分P，不下载 |
| `--resume` | 从 state.json 续跑 |

## 四、流程跑到哪里停

`distill.py` 跑到 **Stage 6**（anchors.json 就绪）后停下：

```
[Stage 7 写作]
  锚点已就绪：<course>/dist/_meta/anchors.json
  → 派子代理写六栏讲义：references/contracts/chapter_writer.md
  → 写完用 state.py 标记：--lecture lecture-01 --status done
```

写作由**子代理或团队**完成，不是脚本。

## 五、派写作代理

任务书模板 → `references/agent-teams.md` §四

要点：
- 只读 `contracts/chapter_writer.md` + `dist/_meta/anchors.json`
- 只写 `dist/lecture-NN.md`
- 不得执行命令、不得写其他文件
- 回执格式固定

## 六、写完后续跑

```
python scripts/distill.py BV1xxxxxxxxx --goal deep_mastery \
  --config config/local.toml --resume
```

会自动装配（`_index.md` + `playbook.md`）并校验。

## 七、状态查看

```
python scripts/state.py show <course>/dist
```

输出：

```
课程：xxx | 目标：deep_mastery
阶段：6 对齐

讲次：
  ○ lecture-01 pending
  ○ lecture-02 pending
  ...

下一步：派发写作任务：lecture-01（还有 8 个待写）
```

## 八、产物

```
courses/<course-id>/
├── .distill/                    中间产物（可删）
│   ├── pages.json               分P列表
│   ├── probe/                   material-profile.json
│   ├── raw/                     视频 + 字幕
│   ├── frames/                  帧 + index.json
│   ├── ocr.json                 OCR 结果
│   └── ocr_sel/                 送 OCR 的帧
└── dist/                        交付物
    ├── lecture-NN.md            六栏讲义
    ├── _index.md                课程索引
    ├── playbook.md              可复现步骤
    └── _meta/
        ├── anchors.json         锚点索引（单一真相源）
        ├── state.json           进度状态
        └── validation.json      校验报告
```

## 九、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `找不到 yutto` | 不在 PATH | 在 `local.toml` 写 `[tools] yutto` |
| yutto 下载后无 mp4 | ffmpeg 不在 PATH | 在 `local.toml` 写 `[tools] ffmpeg` |
| 手写页识别成碎片 | VLM 未配置 | 检查 `[ocr] llama_bin` 与 `[models] qwen_vl_*` |
| 判型为 `info-stream` | 画面无信息量 | 该课程不适合抽帧，需 transcript 模式（未实现） |
| 校验报时间重叠 | 字幕吸附越界 | 已修，若复现请检查 frames.py 的 snap 逻辑 |

## 十、脚本清单

| 脚本 | 作用 |
|---|---|
| `distill.py` | 主入口编排 |
| `fetch.py` | 列分P、下载字幕/视频 |
| `probe.py` | 素材盘点判型 |
| `frames.py` | 抽帧切片选终态帧 |
| `ocr.py` | 印刷体/手写调度 |
| `align.py` | 三向对齐 |
| `build_doc.py` | 装配索引与 PLAYBOOK |
| `validate.py` | 确定性校验 |
| `glossary_fix.py` | 术语纠错 |
| `state.py` | 状态持久化 |
| `env_check.py` | 环境自检 |
| `check_skill.py` | 规范自检 |
| `media_util.py` | 外部程序探测 |
| `setup_env.ps1` | 建 venv 装依赖 |
