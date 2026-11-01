param(
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildEnvironment = Join-Path $RepositoryRoot ".venv-desktop-build"
$Python = Join-Path $BuildEnvironment "Scripts\python.exe"

Push-Location $RepositoryRoot
try {
    $env:PYTHONUTF8 = "1"
    $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
    $env:QT_QPA_PLATFORM = "offscreen"

    if (-not (Test-Path $Python)) {
        $PythonSelector = "-$PythonVersion"
        py $PythonSelector -m venv $BuildEnvironment
        if ($LASTEXITCODE -ne 0) {
            throw "Konnte die Python-Build-Umgebung nicht erstellen."
        }
    }

    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip konnte nicht aktualisiert werden." }

    & $Python -m pip install ".[desktop]" "pyinstaller>=6.10,<7"
    if ($LASTEXITCODE -ne 0) { throw "Desktop-Build-Abhängigkeiten konnten nicht installiert werden." }

    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Die Tests sind fehlgeschlagen." }

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name "ChaosCode" `
        --collect-data chaoscode `
        --collect-all curl_cffi `
        --collect-all wasmtime `
        desktop_launcher.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller konnte ChaosCode.exe nicht erstellen." }

    $Executable = Join-Path $RepositoryRoot "dist\ChaosCode.exe"
    if (-not (Test-Path $Executable)) { throw "Die erwartete Datei fehlt: $Executable" }
    & $Executable --smoke-test
    if ($LASTEXITCODE -ne 0) { throw "Der EXE-Smoke-Test ist fehlgeschlagen." }
    Write-Host "Desktop-App erstellt: $Executable"
}
finally {
    Pop-Location
}
