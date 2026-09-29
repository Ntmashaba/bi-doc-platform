<#
  Build the Windows generator: frozen executables (PyInstaller) and the installer (Inno Setup).

    pwsh packaging/windows/build.ps1 -Version 0.2.0 [-BuildLabel "development build (unsigned)"] [-SkipInstall]

  Output in dist/: bidoc/ (program folder), installer/bidoc-setup-<v>-unsigned.exe,
  release.json (version metadata), SHA256SUMS.txt and RELEASE-NOTES.md.
  Run from the repository root on Windows with Python 3.11.
#>
param(
  [Parameter(Mandatory)] [string] $Version,
  [string] $BuildLabel = "development build (unsigned)",
  [switch] $SkipInstall
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must look like 1.2.3" }

if (-not $SkipInstall) {
  # Regular (not editable) installs, so PyInstaller sees ordinary package folders.
  python -m pip install --upgrade pip
  python -m pip install -c requirements-lock.txt "./components/power-bi[portable]" ./components/adf ./packages/contracts ./packages/engines ./packages/relationships ./apps/generator
  python -m pip install -r requirements-windows-build.txt
  if ($LASTEXITCODE) { throw "dependency install failed" }
}

$parts = $Version.Split(".")
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($parts[0]), $($parts[1]), $($parts[2]), 0), prodvers=($($parts[0]), $($parts[1]), $($parts[2]), 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'BI Documentation Platform'),
    StringStruct('FileDescription', 'BI Documentation Generator ($BuildLabel)'),
    StringStruct('FileVersion', '$Version'),
    StringStruct('ProductName', 'BI Documentation Generator'),
    StringStruct('ProductVersion', '$Version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -Encoding utf8 packaging/windows/version_info.txt

Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --distpath dist --workpath build packaging/windows/bidoc.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }
$frozen = & .\dist\bidoc\bidoc.exe --version
if ($LASTEXITCODE -or $frozen -notmatch "bidoc") { throw "frozen bidoc.exe did not run: $frozen" }

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
  choco install innosetup -y --no-progress | Out-Null
  $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
& $iscc /Qp "/DAppVersion=$Version" "/DBuildLabel=$BuildLabel" "/DSourceDir=$root\dist\bidoc" `
  "/DOutputDir=$root\dist\installer" packaging/windows/installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
$setup = Get-Item "dist\installer\bidoc-setup-$Version-unsigned.exe"

$versions = python -c "import json,platform,pbidocgen,adfdocgen,bidoc_generator,PyInstaller,webview; print(json.dumps({'python': platform.python_version(), 'generator': bidoc_generator.__version__, 'pbi-doc-gen': pbidocgen.__version__, 'adf-doc-gen': adfdocgen.__version__, 'pyinstaller': PyInstaller.__version__, 'pywebview': webview.__version__ if hasattr(webview, '__version__') else 'unknown'}))" | ConvertFrom-Json
$commit = (git rev-parse HEAD 2>$null)
$release = [ordered]@{
  version = $Version; label = $BuildLabel; signed = $false; commit = $commit
  built_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  installer = $setup.Name; installer_bytes = $setup.Length
  sha256 = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash.ToLower()
  components = $versions
  prerequisites_not_bundled = @("Power BI Desktop (PBIX only)", "pbi-tools Desktop, AGPL-3.0 (PBIX only)",
                                "Microsoft Edge WebView2 Runtime (desktop window)")
}
$release | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 dist\release.json
"$($release.sha256)  $($setup.Name)" | Set-Content -Encoding ascii dist\SHA256SUMS.txt
(Get-Content packaging/windows/RELEASE-NOTES.template.md -Raw).
  Replace("{{VERSION}}", $Version).Replace("{{LABEL}}", $BuildLabel).Replace("{{COMMIT}}", "$commit").
  Replace("{{SHA256}}", $release.sha256).Replace("{{INSTALLER}}", $setup.Name).
  Replace("{{PBIDOCGEN}}", $versions.'pbi-doc-gen').Replace("{{ADFDOCGEN}}", $versions.'adf-doc-gen').
  Replace("{{PYTHON}}", $versions.python) | Set-Content -Encoding utf8 dist\RELEASE-NOTES.md
Write-Host "Built $($setup.FullName) ($([math]::Round($setup.Length / 1MB, 1)) MB)"
