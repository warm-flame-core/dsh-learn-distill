# 环境与依赖

## 依赖

| 依赖 | 用途 | 安装 |
|---|---|---|
| Python ≥ 3.13 | 运行时 | — |
| ffmpeg | 抽帧、媒体处理 | 系统安装或指定绝对路径 |
| yutto | B 站字幕/视频下载 | `pip install yutto` |
| faster-whisper | 本地 ASR（字幕缺失时兜底） | `pip install faster-whisper` |
| ctranslate2 | ASR 后端 | 随 faster-whisper |
| rapidocr-onnxruntime | 印刷体 OCR | `pip install rapidocr-onnxruntime` |
| onnxruntime | RapidOCR 后端 | 随 rapidocr |
| nvidia-cublas-cu12 | CT2 的 GPU 依赖 | `pip install nvidia-cublas-cu12` |
| pymupdf | PDF 解析与渲染 | `pip install pymupdf` |
| pypdf | PDF 文本层 | `pip install pypdf` |
| python-docx / python-pptx | DOCX/PPTX 解析 | `pip install` |

Python 版本要求 ≥ 3.13 的原因：`onnxruntime` 无 cp315 wheel。

## 模型

| 模型 | 用途 | 大小 | 放置 |
|---|---|---|---|
| faster-whisper large-v3-turbo | 兜底 ASR | 1.5 GB | `HF_HOME` |
| Qwen2.5-VL-3B Q4_K_M | 手写识别 | 1.8 GB | 配置项 |
| mmproj Q8 | Qwen2.5-VL 多模态层 | 0.8 GB | 与上同目录 |
| llama.cpp | GGUF 运行时 | ~50 MB | 系统安装 |

模型不入 skill 包，运行时按需下载。

## 环境变量

| 变量 | 作用 |
|---|---|
| `HF_HOME` | HuggingFace 缓存根 |
| `HF_ENDPOINT` | 镜像地址，国内用 `https://hf-mirror.com` |
| `HF_HUB_DISABLE_XET` | 设为 `1` 关闭 Xet 传输 |
| `LEARN_DISTILL_HOME` | skill 数据根，默认 `<cwd>/.distill` |

## 配置

`config/default.toml` 为默认值，`config/local.toml` 覆盖（不入 git）。

覆盖顺序：环境变量 > `local.toml` > `default.toml`。

## 自检

```
scripts/env_check.py
```

检查项：Python 版本、ffmpeg 可执行、yutto 可执行、CT2 GPU 可用性、OCR 可加载、模型文件存在。

## B 站登录

字幕与高画质需要登录态。

```
yutto auth login --auth-file <配置中的 auth_file 路径>
```

认证文件路径通过 `--auth-file` 传参指定，不依赖 `XDG_CONFIG_HOME`。

## 网络注意

字幕 CDN 走 IPv6 时可能返回错误内容。所有网络请求应强制 IPv4：

```python
import socket
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, type=0, proto=0, flags=0: \
    _orig(host, port, socket.AF_INET, type, proto, flags)
```

B 站域名建议走直连，不走代理。
