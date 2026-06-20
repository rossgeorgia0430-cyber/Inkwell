# =============================================================================
# install.ps1  —  Inkwell（本地 Markdown 阅读器）安装器
# -----------------------------------------------------------------------------
# 同一个脚本既用于本机开发安装，也用于跨机分发安装：
#   - 自动定位程序载荷（payload）：分发包内的 .\Inkwell，或开发树的 ..\dist\Inkwell
#   - 检测 WebView2 运行时，缺失则联网静默补装（可 -SkipWebView2 跳过）
#   - 清理旧版 MarkdownReader / MDReader 残留（调用 cleanup_legacy.ps1）
#   - 复制到 %LOCALAPPDATA%\Programs\Inkwell
#   - 完整注册：ProgId + Applications\Inkwell.exe(FriendlyAppName+SupportedTypes)
#     + Capabilities + RegisteredApplications + OpenWithProgids
#     —— 这套“完整应用注册”正是让双击 .md 时出现“始终使用此应用打开”的关键
#   - 不直接改写扩展名默认值 / UserChoice，避免覆盖用户已有默认应用
#   - 通知 Shell 刷新、创建开始菜单/桌面快捷方式
#   - 收尾引导用户一键确认默认程序（Windows 安全机制要求的最后一步）
#
# 权限：默认全程 HKCU（当前用户），无需管理员。
#   -SetDefaultViaPolicy 开关会写 HKLM 组策略 XML 以“免点击”强制默认，
#   该开关需要管理员，且仅在域/Entra/MDM 托管机器上可靠（独立机器不保证生效）。
# =============================================================================
[CmdletBinding()]
param(
    [switch]$SkipWebView2,        # 跳过 WebView2 检测/补装
    [switch]$NoShortcut,          # 不创建快捷方式
    [switch]$SetDefaultViaPolicy, # 额外写 HKLM 组策略 XML（需管理员，托管机器才可靠）
    [switch]$Quiet                # 安装完成后不自动打开“默认应用”设置
)

$ErrorActionPreference = 'SilentlyContinue'

# ---- 固定参数 ---------------------------------------------------------------
$AppName  = 'Inkwell'
$ProgId   = 'Inkwell.Markdown'
$ExeName  = 'Inkwell.exe'
$Friendly = 'Inkwell'
$AppDesc  = 'Inkwell - 本地 Markdown 阅读器'
$Target   = Join-Path $env:LOCALAPPDATA 'Programs\Inkwell'
$Exts     = @('.md', '.markdown', '.mdown', '.mkd')
$BackupRoot = 'HKCU:\Software\Inkwell\AssociationBackup'

function Line($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Head($t) { Write-Host ""; Line "--- $t ---" 'White' }

Line "==================================================" 'Cyan'
Line " 安装 Inkwell - Markdown 阅读器" 'Cyan'
Line "==================================================" 'Cyan'

# =============================================================================
# 0) 定位程序载荷（payload）
# =============================================================================
Head '[0/8] 定位安装载荷'
$payloadCandidates = @(
    (Join-Path $PSScriptRoot 'Inkwell'),                                 # 分发包：脚本同级的 Inkwell\
    (Join-Path $PSScriptRoot 'payload\Inkwell'),                         # 分发包：payload\Inkwell\
    (Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\Inkwell')        # 开发树：..\dist\Inkwell\
)
$Payload = $null
foreach ($c in $payloadCandidates) {
    if (Test-Path -LiteralPath (Join-Path $c $ExeName)) { $Payload = $c; break }
}
if (-not $Payload) {
    Line "[错误]   未找到程序载荷（包含 $ExeName 的 Inkwell 目录）。" 'Red'
    Line "         已查找：" 'Red'
    $payloadCandidates | ForEach-Object { Line "           - $_" 'DarkGray' }
    Line "         开发环境请先运行  python build.py  生成 dist\Inkwell。" 'Yellow'
    exit 1
}
Line "[OK]     程序载荷：$Payload" 'Green'

# =============================================================================
# 1) 检测 / 补装 WebView2 运行时
# =============================================================================
Head '[1/8] 检测 WebView2 运行时'

function Test-WebView2Installed {
    $guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $paths = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$guid",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid",
        "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$guid"
    )
    foreach ($p in $paths) {
        $pv = (Get-ItemProperty -LiteralPath $p -Name 'pv' -ErrorAction SilentlyContinue).pv
        if (-not [string]::IsNullOrWhiteSpace($pv) -and $pv -ne '0.0.0.0') { return $true }
    }
    return $false
}

if ($SkipWebView2) {
    Line "[跳过]   已指定 -SkipWebView2。" 'DarkGray'
} elseif (Test-WebView2Installed) {
    Line "[OK]     已检测到 WebView2 运行时。" 'Green'
} else {
    Line "[!]      未检测到 WebView2 运行时，尝试联网静默补装…" 'Yellow'
    $setup = Join-Path $env:TEMP 'MicrosoftEdgeWebview2Setup.exe'
    try {
        $ErrorActionPreference = 'Stop'
        Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $setup -UseBasicParsing
        $p = Start-Process -FilePath $setup -ArgumentList '/silent', '/install' -Wait -PassThru
        Start-Sleep -Milliseconds 800
        if (Test-WebView2Installed) {
            Line "[OK]     WebView2 运行时安装成功。" 'Green'
        } else {
            Line "[警告]   WebView2 安装结束但未检测到（退出码 $($p.ExitCode)）。" 'Yellow'
        }
    } catch {
        Line "[警告]   WebView2 自动补装失败：$($_.Exception.Message)" 'Yellow'
        Line "         （可能离线/受代理限制。）请手动安装运行时后重试：" 'Yellow'
        Line "         https://developer.microsoft.com/microsoft-edge/webview2/" 'Yellow'
    } finally {
        $ErrorActionPreference = 'SilentlyContinue'
    }
}

# =============================================================================
# 2) 清理旧版遗留项
# =============================================================================
Head '[2/8] 清理旧版遗留项'
$cleanup = Join-Path $PSScriptRoot 'cleanup_legacy.ps1'
if (Test-Path -LiteralPath $cleanup) {
    & $cleanup
    Line "[OK]     旧版清理流程结束。" 'Green'
} else {
    Line "[跳过]   未找到 cleanup_legacy.ps1。" 'DarkGray'
}

# =============================================================================
# 3) 复制到安装目录
# =============================================================================
Head '[3/8] 复制到安装目录'
try {
    $ErrorActionPreference = 'Stop'
    if (Test-Path -LiteralPath $Target) {
        # 先尝试关闭正在运行的旧实例，避免占用
        Get-Process -Name 'Inkwell' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    $parent = Split-Path -Parent $Target
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    Copy-Item -Path (Join-Path $Payload '*') -Destination $Target -Recurse -Force
    Line "[OK]     已复制到 $Target" 'Green'
} catch {
    Line "[错误]   复制失败：$($_.Exception.Message)" 'Red'
    exit 1
} finally {
    $ErrorActionPreference = 'SilentlyContinue'
}

$exe = Join-Path $Target $ExeName
if (-not (Test-Path -LiteralPath $exe)) { Line "[错误]   复制后未找到 $exe。" 'Red'; exit 1 }

# =============================================================================
# 4) 完整文件关联注册（HKCU）—— 使“始终使用此应用打开”出现的关键
# =============================================================================
Head '[4/8] 注册文件关联（完整应用注册）'

function Ensure-Key($p) { if (-not (Test-Path -LiteralPath $p)) { New-Item -Path $p -Force | Out-Null } }
function Set-Default($p, $v) { Ensure-Key $p; Set-ItemProperty -LiteralPath $p -Name '(default)' -Value $v -Force }
function Save-ExtensionDefault($ext) {
    # 只在首次安装时记录；升级不能覆盖最初的安装前状态。
    $name = $ext.TrimStart('.')
    $backup = "$BackupRoot\$name"
    if (Test-Path -LiteralPath $backup) { return }
    Ensure-Key $backup
    $extPath = "$C\$ext"
    $hadDefault = $false
    $oldDefault = $null
    if (Test-Path -LiteralPath $extPath) {
        $item = Get-Item -LiteralPath $extPath
        $hadDefault = $item.GetValueNames() -contains ''
        if ($hadDefault) { $oldDefault = $item.GetValue('') }
    }
    Set-ItemProperty -LiteralPath $backup -Name 'HadDefault' -Value ([int]$hadDefault) -Type DWord -Force
    if ($hadDefault) {
        Set-ItemProperty -LiteralPath $backup -Name 'Value' -Value ([string]$oldDefault) -Type String -Force
    }
}

$C   = 'HKCU:\Software\Classes'
$cmd = ('"{0}" "%1"' -f $exe)

try {
    $ErrorActionPreference = 'Stop'

    # A) ProgId：类、友好类型名、图标、open 动词
    Set-Default "$C\$ProgId" 'Markdown 文档'
    Set-ItemProperty -LiteralPath "$C\$ProgId" -Name 'FriendlyTypeName' -Value 'Markdown 文档' -Force
    Set-Default "$C\$ProgId\DefaultIcon" ('"{0}",0' -f $exe)
    Set-Default "$C\$ProgId\shell\open" '使用 Inkwell 打开'
    Set-Default "$C\$ProgId\shell\open\command" $cmd

    # B) Application：FriendlyAppName + SupportedTypes（让“始终”选项出现）
    $app = "$C\Applications\$ExeName"
    Ensure-Key $app                      # 先建键：全新机器上该键尚不存在，否则 Set-ItemProperty 会抛错
    Set-ItemProperty -LiteralPath $app -Name 'FriendlyAppName' -Value $Friendly -Force
    Set-Default "$app\shell\open\command" $cmd
    Ensure-Key "$app\SupportedTypes"
    foreach ($e in $Exts) { Set-ItemProperty -LiteralPath "$app\SupportedTypes" -Name $e -Value '' -Type String -Force }

    # C) 只把 Inkwell 列入候选，不覆盖扩展名默认值或受保护的 UserChoice。
    #    同时保存安装前状态，供卸载旧版本遗留的 Inkwell 默认值时安全恢复。
    foreach ($e in $Exts) {
        Save-ExtensionDefault $e
        Ensure-Key "$C\$e"
        Ensure-Key "$C\$e\OpenWithProgids"
        New-ItemProperty -LiteralPath "$C\$e\OpenWithProgids" -Name $ProgId -PropertyType None -Value ([byte[]]@()) -Force | Out-Null
    }

    # D) App Capabilities + RegisteredApplications（进入“设置 > 默认应用”候选）
    $cap = 'HKCU:\Software\Inkwell\Capabilities'
    Ensure-Key "$cap\FileAssociations"
    Set-ItemProperty -LiteralPath $cap -Name 'ApplicationName'        -Value $Friendly -Force
    Set-ItemProperty -LiteralPath $cap -Name 'ApplicationDescription' -Value $AppDesc  -Force
    foreach ($e in $Exts) { Set-ItemProperty -LiteralPath "$cap\FileAssociations" -Name $e -Value $ProgId -Force }
    Ensure-Key 'HKCU:\Software\RegisteredApplications'
    Set-ItemProperty -LiteralPath 'HKCU:\Software\RegisteredApplications' -Name $Friendly -Value 'Software\Inkwell\Capabilities' -Force

    Line "[OK]     已注册为 Markdown 候选应用；未改写用户现有默认应用。" 'Green'
} catch {
    Line "[错误]   注册失败：$($_.Exception.Message)" 'Red'
} finally {
    $ErrorActionPreference = 'SilentlyContinue'
}

# =============================================================================
# 5) 通知 Shell 刷新关联缓存
# =============================================================================
Head '[5/8] 通知 Shell 刷新关联'
try {
    if (-not ('Inkwell.Shell32Native' -as [type])) {
        Add-Type -Namespace 'Inkwell' -Name 'Shell32Native' -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int wEventId, uint uFlags, System.IntPtr dwItem1, System.IntPtr dwItem2);
'@
    }
    [Inkwell.Shell32Native]::SHChangeNotify(0x08000000, 0x1000, [IntPtr]::Zero, [IntPtr]::Zero)
    Line "[OK]     已通知 Shell（SHCNE_ASSOCCHANGED）。" 'Green'
} catch {
    Line "[警告]   SHChangeNotify 失败（不影响功能）。" 'Yellow'
}

# =============================================================================
# 6) 快捷方式（开始菜单 + 桌面）
# =============================================================================
Head '[6/8] 创建快捷方式'
if ($NoShortcut) {
    Line "[跳过]   已指定 -NoShortcut。" 'DarkGray'
} else {
    try {
        $wsh = New-Object -ComObject WScript.Shell
        $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
        Ensure-Key $startMenu  # New-Item 对文件夹同样适用
        foreach ($dir in @($startMenu, [Environment]::GetFolderPath('Desktop'))) {
            if (-not (Test-Path -LiteralPath $dir)) { continue }
            $lnk = Join-Path $dir 'Inkwell.lnk'
            $sc = $wsh.CreateShortcut($lnk)
            $sc.TargetPath       = $exe
            $sc.WorkingDirectory = $Target
            $sc.IconLocation     = ('{0},0' -f $exe)
            $sc.Description       = $AppDesc
            $sc.Save()
        }
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh)
        Line "[OK]     已创建开始菜单 + 桌面快捷方式。" 'Green'
    } catch {
        Line "[警告]   创建快捷方式失败：$($_.Exception.Message)" 'Yellow'
    }
}

# =============================================================================
# 7) （可选）写组策略 XML 以免点击强制默认（需管理员；托管机器才可靠）
# =============================================================================
Head '[7/8] 永久默认（可选 / 组策略）'
function Test-Admin { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator) }
if ($SetDefaultViaPolicy) {
    if (-not (Test-Admin)) {
        Line "[跳过]   -SetDefaultViaPolicy 需要管理员权限。" 'Yellow'
    } else {
        try {
            $ErrorActionPreference = 'Stop'
            $xmlPath = Join-Path $env:ProgramData 'Inkwell\DefaultAssoc.xml'
            $pol = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System'
            $existingPolicy = (Get-ItemProperty -LiteralPath $pol -Name 'DefaultAssociationsConfiguration' -ErrorAction SilentlyContinue).DefaultAssociationsConfiguration
            if ($existingPolicy -and $existingPolicy -ne $xmlPath) {
                throw "系统已有默认关联策略 '$existingPolicy'，为避免覆盖已跳过。"
            }
            Ensure-Key (Split-Path $xmlPath -Parent)
            $assoc = ($Exts | ForEach-Object { "  <Association Identifier=`"$_`" ProgId=`"$ProgId`" ApplicationName=`"$Friendly`" />" }) -join "`r`n"
            $xml = "<?xml version=`"1.0`" encoding=`"UTF-8`"?>`r`n<DefaultAssociations>`r`n$assoc`r`n</DefaultAssociations>`r`n"
            [System.IO.File]::WriteAllText($xmlPath, $xml, (New-Object System.Text.UTF8Encoding($true)))
            Ensure-Key $pol
            Set-ItemProperty -LiteralPath $pol -Name 'DefaultAssociationsConfiguration' -Value $xmlPath -Type String -Force
            Line "[OK]     已写组策略 XML：$xmlPath（注销/重新登录后生效）。" 'Green'
            Line "         注意：独立（非域/非 MDM）机器组策略可能不处理此项。" 'DarkGray'
        } catch {
            Line "[警告]   写组策略失败：$($_.Exception.Message)" 'Yellow'
        } finally { $ErrorActionPreference = 'SilentlyContinue' }
    }
} else {
    Line "[跳过]   未指定 -SetDefaultViaPolicy（独立机器推荐用下方一键确认）。" 'DarkGray'
}

# =============================================================================
# 8) 收尾 + 默认程序引导
# =============================================================================
Head '[8/8] 完成'
Line "==================================================" 'Cyan'
Line " Inkwell 安装完成" 'Cyan'
Line "==================================================" 'Cyan'
Line (" 安装路径 : {0}" -f $Target) 'Green'
Line (" 主程序   : {0}" -f $exe) 'Green'
Line "--------------------------------------------------" 'Cyan'
Line " 已完成完整注册：双击 .md 时“始终使用此应用打开”将会出现。" 'Green'
Line " 最后一步（Windows 安全机制要求手动一次，永久生效）：" 'White'
Line "   双击任意 .md -> 选择 Inkwell -> 勾选/点击“始终”。" 'White'
if (-not $Quiet) {
    Line " 正在打开“默认应用”设置，便于你把 .md 指给 Inkwell…" 'DarkGray'
    Start-Process 'ms-settings:defaultapps' -ErrorAction SilentlyContinue
}
Line "==================================================" 'Cyan'
