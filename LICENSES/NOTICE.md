# 第三方代码声明

本项目包含来自第三方的代码。以下逐一列出出处、许可与修改内容。

## skill-bnote

- 来源：https://github.com/HarveyZed/skill-bnote
- 许可：MIT
- 版权：Copyright (c) 2026 HarveyZed
- 许可原文：`LICENSES/skill-bnote-MIT.txt`

### 借用文件

| 原文件 | 移植为 | 修改内容 |
|---|---|---|
| `bnote/segmenters/stable.py` | `scripts/frames.py` | 保留核心判据（硬切判定、稳定段、终态帧候选池、同页合并、字幕吸附）；去掉 overlay/overlay.json 遮罩、角色判定、OCR 一致性合并等 bnote 特有逻辑；改用本项目的配置结构 |
| `bnote/segmenters/framesig.py` | `scripts/frames.py` | 保留 dHash、汉明距离、墨迹、清晰度的计算口径；去掉手写笔迹挖除与整屏应用判定 |
| `bnote/tools.py` | `scripts/frames.py` | 保留 `find_ffmpeg` / `probe_media` / `sha256_file` 的探测顺序与实现；去掉 `imageio-ffmpeg` 兜底与 `os.symlink` 软链 |

### 未借用的部分

`bnote/layers/*`（overlay、composite、roles、profile、export 等）、`bnote/cli.py`、`bnote/backends/*`、
`bnote/auth_store.py` 均未借用。

## 其他

其余代码为原创。词库数据（`assets/glossary/*.json`）为原创整理。
