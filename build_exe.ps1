param([string]$OutputDirectory = 'dist\1.0.0')

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $outputPath = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $PSScriptRoot $OutputDirectory }
    & py -3 -m PyInstaller --noconfirm --distpath $outputPath LCPacker.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败，退出码 $LASTEXITCODE" }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'packaging\使用说明.txt') -Destination (Join-Path $outputPath '使用说明.txt') -Force
    Get-Item -LiteralPath (Join-Path $outputPath 'LC打包.exe') | Select-Object FullName, Length
} finally {
    Pop-Location
}
