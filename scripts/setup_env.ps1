# 创建隔离 venv 并安装依赖（Windows PowerShell）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1 [-PythonExe <path>] [-VenvPath <path>]

param(
    [string]$PythonExe = "py -3.13",
    [string]$VenvPath = "",
    [string]$IndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = "Stop"
$SkillRoot = Split-Path -Parent $PSScriptRoot
if (-not $VenvPath) { $VenvPath = Join-Path $SkillRoot ".venv" }

Write-Host "[setup] skill 根 : $SkillRoot"
Write-Host "[setup] venv     : $VenvPath"

# 检查 Python
$pyVersion = & cmd /c "$PythonExe -c `"import sys;print('%d.%d'%sys.version_info[:2])`""
if ($LASTEXITCODE -ne 0) { throw "找不到 Python：$PythonExe" }
Write-Host "[setup] python   : $pyVersion"
if ([version]$pyVersion -lt [version]"3.13") {
    throw "需要 Python >= 3.13，当前 $pyVersion（onnxruntime 无更低版本的 wheel）"
}

# 建 venv
if (-not (Test-Path $VenvPath)) {
    & cmd /c "$PythonExe -m venv `"$VenvPath`""
    if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败" }
}

$venvPy = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "venv 中找不到 python.exe：$venvPy" }

# 升级 pip
& $venvPy -m pip install --upgrade pip -i $IndexUrl
if ($LASTEXITCODE -ne 0) { throw "升级 pip 失败" }

# 安装依赖
$deps = @(
    "faster-whisper",
    "ctranslate2",
    "nvidia-cublas-cu12",
    "rapidocr-onnxruntime",
    "pillow",
    "numpy",
    "yt-dlp",
    "pypdf",
    "python-docx",
    "python-pptx",
    "pymupdf",
    "pyyaml"
)
& $venvPy -m pip install -i $IndexUrl @deps
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败" }

# yutto 单独装（避免依赖冲突）
& $venvPy -m pip install -i $IndexUrl yutto
if ($LASTEXITCODE -ne 0) { Write-Warning "yutto 安装失败，字幕下载不可用" }

# 自检
Write-Host "[setup] 自检："
& $venvPy (Join-Path $SkillRoot "scripts\env_check.py")
