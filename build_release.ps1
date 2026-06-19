param(
    [switch]$SkipInstaller,
    [switch]$SkipDownload
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $root 'tools'
$build = Join-Path $root '.build'
$venv = Join-Path $root '.build-venv'
$release = Join-Path $root 'release'
$ffmpeg = Join-Path $tools 'ffmpeg.exe'
$ffprobe = Join-Path $tools 'ffprobe.exe'

New-Item -ItemType Directory -Force $tools, $build, $release | Out-Null

if (!(Test-Path $ffmpeg) -or !(Test-Path $ffprobe)) {
    if ($SkipDownload) { throw 'Faltan tools\ffmpeg.exe y tools\ffprobe.exe.' }
    $archive = Join-Path $build 'ffmpeg-release-essentials.zip'
    $expanded = Join-Path $build 'ffmpeg'
    Write-Host 'Descargando FFmpeg para Windows...'
    Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $archive
    if (Test-Path $expanded) { Remove-Item -Recurse -Force $expanded }
    Expand-Archive $archive $expanded
    $ffmpegSource = Get-ChildItem $expanded -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    $ffprobeSource = Get-ChildItem $expanded -Recurse -Filter ffprobe.exe | Select-Object -First 1
    if (!$ffmpegSource -or !$ffprobeSource) { throw 'El paquete descargado no contiene FFmpeg y FFprobe.' }
    Copy-Item $ffmpegSource.FullName $ffmpeg
    Copy-Item $ffprobeSource.FullName $ffprobe
    $license = Get-ChildItem $expanded -Recurse -File | Where-Object Name -Match '^LICENSE(\.txt)?$' | Select-Object -First 1
    if ($license) { Copy-Item $license.FullName (Join-Path $build 'FFmpeg-LICENSE.txt') }
}

if (!(Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    Write-Host 'Creando entorno de compilación...'
    & 'C:\Python314\python.exe' -m venv $venv
}
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias de compilación.' }

$distRoot = Join-Path $build "dist-$PID"
$workRoot = Join-Path $build "work-$PID"
$specRoot = Join-Path $build 'spec'
& $python -m PyInstaller `
    --noconfirm --clean --onedir --windowed --noupx `
    --name GeViMastering `
    --distpath $distRoot --workpath $workRoot --specpath $specRoot `
    --add-data "$(Join-Path $root 'static');static" `
    --add-binary "$ffmpeg;tools" `
    --add-binary "$ffprobe;tools" `
    --hidden-import tkinter --hidden-import tkinter.filedialog `
    --collect-all webview `
    (Join-Path $root 'web_app.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller no pudo generar la aplicación.' }

$appDir = Join-Path $distRoot 'GeViMastering'
Copy-Item (Join-Path $root 'README.md') $appDir -Force
Copy-Item (Join-Path $root 'THIRD_PARTY_NOTICES.txt') $appDir -Force
if (Test-Path (Join-Path $build 'FFmpeg-LICENSE.txt')) {
    New-Item -ItemType Directory -Force (Join-Path $appDir 'licenses') | Out-Null
    Copy-Item (Join-Path $build 'FFmpeg-LICENSE.txt') (Join-Path $appDir 'licenses\FFmpeg-LICENSE.txt') -Force
}

$portable = Join-Path $release 'GeViMastering-portable.zip'
$compressed = $false
for ($attempt = 1; $attempt -le 5 -and !$compressed; $attempt++) {
    if (Test-Path $portable) { Remove-Item $portable -Force }
    & tar.exe -a -c -f $portable -C $appDir .
    if ($LASTEXITCODE -eq 0) {
        $compressed = $true
    } else {
        if ($attempt -eq 5) { throw 'No se pudo crear el ZIP portable.' }
        Write-Warning "El sistema mantiene archivos del build ocupados; reintento $attempt de 5..."
        Start-Sleep -Seconds 5
    }
}
Write-Host "Portable creado: $portable"

if (!$SkipInstaller) {
    $iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if (!$iscc) {
        $known = '${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe'
        $known = $ExecutionContext.InvokeCommand.ExpandString($known)
        if (Test-Path $known) { $iscc = Get-Command $known }
    }
    if ($iscc) {
        & $iscc.Source "/DSourceDir=$appDir" "/DOutputDir=$release" (Join-Path $root 'packaging\GeViMastering.iss')
        if ($LASTEXITCODE -ne 0) { throw 'Inno Setup no pudo generar el instalador.' }
    } else {
        Write-Warning 'Inno Setup no está instalado; se omitió Setup.exe. El ZIP portable sí está listo.'
    }
}
