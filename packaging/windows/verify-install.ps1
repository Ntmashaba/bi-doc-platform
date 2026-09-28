<#
  A13 installer acceptance: install, prerequisite diagnosis, generation, desktop app,
  upgrade and uninstall, checked from a shell with no Python on PATH.

    pwsh packaging/windows/verify-install.ps1 -Installer dist\installer\bidoc-setup-0.2.0-unsigned.exe `
         [-UpgradeInstaller path\to\newer-setup.exe] [-Report verify-report.json]

  Run it on a clean Windows 10/11 machine (no Python, no Docker) to complete A13. CI runs
  the same script on a GitHub-hosted runner, which does have Python on disk; there it is
  only removed from PATH, and the report says so.
  Exit code 0 when every check passed.
#>
param(
  [Parameter(Mandatory)] [string] $Installer,
  [string] $UpgradeInstaller,
  [string] $Inputs = "$PSScriptRoot\verify-inputs",
  [string] $Work = (Join-Path ([System.IO.Path]::GetTempPath()) ("bidoc-verify-" + [guid]::NewGuid().ToString("N").Substring(0, 8))),
  [string] $Report = "verify-report.json"
)
$ErrorActionPreference = "Stop"
$AppId = "{6F1B2C94-3D7E-4B8A-9E51-B0C7D2A4F8E3}_is1"
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
$App = Join-Path $env:LOCALAPPDATA "Programs\BI Documentation Generator"
$DataHome = Join-Path $env:LOCALAPPDATA "bidoc"
$Installer = (Resolve-Path $Installer).Path
if ($UpgradeInstaller) { $UpgradeInstaller = (Resolve-Path $UpgradeInstaller).Path }
$Inputs = (Resolve-Path $Inputs).Path
$checks = New-Object System.Collections.Generic.List[object]

function Check([string] $name, [bool] $ok, [string] $detail = "") {
  $checks.Add([ordered]@{ check = $name; ok = $ok; detail = $detail })
  Write-Host ("  [{0}] {1}{2}" -f ($(if ($ok) { "ok" } else { "FAIL" })), $name, $(if ($detail) { ": $detail" } else { "" }))
}

function Run-Setup([string] $exe, [string] $log) {
  $p = Start-Process -FilePath $exe -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$log`"" -Wait -PassThru
  return $p.ExitCode
}

function Bidoc([string[]] $arguments) {
  $out = & (Join-Path $App "bidoc.exe") @arguments 2>&1
  return @{ code = $LASTEXITCODE; text = ($out | Out-String) }
}

function Installed-Version { (Get-ItemProperty $UninstallKey -ErrorAction SilentlyContinue).DisplayVersion }

New-Item -ItemType Directory -Force $Work | Out-Null
Write-Host "Working in $Work"

# ---- a shell without Python ----------------------------------------------------------
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
foreach ($v in "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "BIDOC_HOME", "BIDOC_PBI_TOOLS") { Remove-Item "env:$v" -ErrorAction SilentlyContinue }
$pythonOnPath = [bool](Get-Command python, python3, py -ErrorAction SilentlyContinue)
Check "no Python on PATH" (-not $pythonOnPath)
$pythonOnDisk = (Test-Path "C:\hostedtoolcache\windows\Python") -or (Test-Path "$env:LOCALAPPDATA\Programs\Python")
$os = (Get-CimInstance Win32_OperatingSystem)

# ---- install -------------------------------------------------------------------------
if (Test-Path $UninstallKey) { throw "The generator is already installed for this account; uninstall it first." }
$code = Run-Setup $Installer (Join-Path $Work "install.log")
Check "silent install exits 0" ($code -eq 0) "exit $code"
$v1 = Installed-Version
Check "registered for uninstall (per user)" ([bool]$v1) "version $v1"
foreach ($f in "bidoc.exe", "BI Documentation Generator.exe", "unins000.exe") {
  Check "installed $f" (Test-Path (Join-Path $App $f))
}
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\BI Documentation Generator"
Check "Start menu shortcut" (Test-Path (Join-Path $startMenu "BI Documentation Generator.lnk"))
$fileVersion = (Get-Item (Join-Path $App "bidoc.exe")).VersionInfo.FileVersion
Check "bidoc.exe carries version metadata" ($fileVersion -eq $v1) "file version $fileVersion"

# ---- prerequisites -------------------------------------------------------------------
$r = Bidoc @("--version")
Check "bidoc --version runs without Python" ($r.code -eq 0 -and $r.text -match "bidoc") $r.text.Trim()
$r = Bidoc @("doctor", "--json")
$doctor = $r.text | ConvertFrom-Json
Check "doctor: PBIP ready" ([bool]$doctor.inputs.pbip.available)
Check "doctor: ADF ready" ([bool]$doctor.inputs.adf_git.available)
$pbix = $doctor.inputs.pbix
Check "doctor: PBIX readiness explained" ([bool]$pbix.available -or [bool]$pbix.reason) $(if ($pbix.available) { "ready" } else { $pbix.reason })

# ---- generation ----------------------------------------------------------------------
Copy-Item -Recurse $Inputs (Join-Path $Work "inputs")
$out = Join-Path $Work "out"
$r = Bidoc @("batch", "$Work\inputs\Sales", "$Work\inputs\factory", "--output-dir", $out, "--profile", "shared", "--json")
$batch = $null
try { $batch = $r.text | ConvertFrom-Json } catch { }
$states = if ($batch) { ($batch.items | ForEach-Object { $_.state }) -join "," } else { $r.text }
Check "batch generates PBIP and ADF documentation" ($r.code -eq 0 -and $states -eq "completed,completed") $states
$docs = @(Get-ChildItem $out -Filter *.shared.html -ErrorAction SilentlyContinue)
Check "shared documents written with a publication manifest" ($docs.Count -eq 2 -and ($docs | Where-Object { (Get-Content $_.FullName -Raw) -match 'id="pbidoc-manifest"' }).Count -eq 2) "$($docs.Count) file(s)"
$r = Bidoc @("generate", "--engine", "power_bi", "--kind", "pbip", "--source", "$Work\inputs\Sales", "--output-dir", "$Work\out-local")
Check "local generation of a PBIP project" ($r.code -eq 0) "exit $($r.code)"
$tool = Join-Path $Work "pbi-tools.exe"
Set-Content $tool "" -Encoding ascii
$r = Bidoc @("config", "--pbi-tools", $tool)
Check "settings saved" ($r.code -eq 0)

# ---- desktop app ---------------------------------------------------------------------
$gui = Start-Process -FilePath (Join-Path $App "BI Documentation Generator.exe") -ArgumentList "--check" -Wait -PassThru
$desktopCheck = Get-Content (Join-Path $DataHome "desktop-check.json") -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
Check "desktop executable starts (windowed build)" ([bool]$desktopCheck.frozen -and [bool]$desktopCheck.pywebview) "exit $($gui.ExitCode); WebView2 $($desktopCheck.webview2)"
$port = Get-Random -Minimum 20000 -Maximum 40000
$server = Start-Process -FilePath (Join-Path $App "bidoc.exe") -ArgumentList "desktop", "--no-window", "--port", "$port" -PassThru -WindowStyle Hidden
$page = $null
for ($i = 0; $i -lt 60 -and -not $page; $i++) {
  Start-Sleep -Milliseconds 500
  try { $page = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/" -TimeoutSec 5 } catch { }
}
Check "desktop app serves its UI on loopback" ($page -and $page.StatusCode -eq 200 -and $page.Content -match "bidoc-session")
$listening = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | ForEach-Object { $_.LocalAddress })
Check "desktop app listens on 127.0.0.1 only" ($listening.Count -ge 1 -and -not ($listening | Where-Object { $_ -ne "127.0.0.1" })) ($listening -join ", ")
$denied = $false
try { Invoke-WebRequest -UseBasicParsing -Method Post "http://127.0.0.1:$port/api/review" -Body '{"inputs":["x"]}' -ContentType "application/json" | Out-Null }
catch { $denied = ($_.Exception.Response.StatusCode.value__ -eq 401) }
Check "desktop app refuses changes without the session secret" $denied
Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue

$historyBefore = @(((Bidoc @("history", "--json")).text | ConvertFrom-Json)).Count

# ---- upgrade -------------------------------------------------------------------------
if ($UpgradeInstaller) {
  $code = Run-Setup $UpgradeInstaller (Join-Path $Work "upgrade.log")
  $v2 = Installed-Version
  Check "upgrade installs over the previous version" ($code -eq 0 -and $v2 -and $v2 -ne $v1) "$v1 -> $v2"
  Check "one installation registered after upgrade" (@(Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" | Where-Object { $_.PSChildName -like "*6F1B2C94*" }).Count -eq 1)
  $fileVersion = (Get-Item (Join-Path $App "bidoc.exe")).VersionInfo.FileVersion
  Check "program files replaced" ($fileVersion -eq $v2) "file version $fileVersion"
  $historyAfter = @(((Bidoc @("history", "--json")).text | ConvertFrom-Json)).Count
  Check "history kept across upgrade" ($historyAfter -eq $historyBefore -and $historyAfter -ge 1) "$historyBefore -> $historyAfter batch(es)"
  $settings = (Bidoc @("config", "--json")).text | ConvertFrom-Json
  Check "settings kept across upgrade" ($settings.pbi_tools -eq (Resolve-Path $tool).Path) $settings.pbi_tools
  $r = Bidoc @("batch", "$Work\inputs\factory", "--output-dir", "$Work\out-upgraded")
  Check "generation works after upgrade" ($r.code -eq 0) "exit $($r.code)"
} else {
  Check "upgrade (skipped: no -UpgradeInstaller)" $true "not verified"
}

# ---- uninstall -----------------------------------------------------------------------
$uninstaller = Join-Path $App "unins000.exe"
Start-Process -FilePath $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait | Out-Null
for ($i = 0; $i -lt 60 -and (Test-Path (Join-Path $App "bidoc.exe")); $i++) { Start-Sleep -Milliseconds 500 }
Check "uninstall removes the program" (-not (Test-Path (Join-Path $App "bidoc.exe")) -and -not (Test-Path $UninstallKey))
Check "uninstall removes the Start menu entry" (-not (Test-Path (Join-Path $startMenu "BI Documentation Generator.lnk")))
Check "uninstall keeps history and settings" ((Test-Path (Join-Path $DataHome "history.sqlite3")) -and (Test-Path (Join-Path $DataHome "config.json")))
Check "uninstall keeps generated documentation" ((@(Get-ChildItem $out -Filter *.html)).Count -eq 2)

# ---- report --------------------------------------------------------------------------
$failed = @($checks | Where-Object { -not $_.ok })
[ordered]@{
  machine = [ordered]@{ os = $os.Caption; version = $os.Version; build = $os.BuildNumber
                        python_on_path = $pythonOnPath; python_on_disk = $pythonOnDisk
                        clean_machine = (-not $pythonOnDisk) }
  installer = Split-Path $Installer -Leaf; upgrade_installer = $(if ($UpgradeInstaller) { Split-Path $UpgradeInstaller -Leaf } else { $null })
  passed = ($failed.Count -eq 0); checks = $checks
} | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $Report
Write-Host "$($checks.Count - $failed.Count) of $($checks.Count) checks passed. Report: $Report"
if ($failed.Count) { exit 1 }
