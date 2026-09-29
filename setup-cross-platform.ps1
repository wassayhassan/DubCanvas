$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = "py"
try {
    & $python -3.11 --version | Out-Null
    & $python -3.11 -m venv .venv
} catch {
    $python = "python"
    & $python --version
    & $python -m venv .venv
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip setuptools wheel
& $venvPython -m pip install -r requirements-cross-platform.txt
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Python dependencies are incompatible. Resolve the reported conflicts before running DubCanvas." }

Write-Host ""
Write-Host "Python dependencies installed."
Write-Host "FFmpeg/ffprobe must also be available on PATH."
Write-Host "For local Piper TTS, download a Piper .onnx voice model and pass --piper-model."
Write-Host "Ollama is optional; install/start it separately if you want --translation ollama."
Write-Host ""
& $venvPython -m dubcanvas doctor
