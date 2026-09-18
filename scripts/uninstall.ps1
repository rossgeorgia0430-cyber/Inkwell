# =============================================================================
# uninstall.ps1  —  卸载 Inkwell，移除其完整注册与文件
# -----------------------------------------------------------------------------
# 清理本程序自身留下的（全部位于 HKCU 与当前用户目录，无需管理员）：
#   - ProgId  Inkwell.Markdown（整键）
#   - Applications\Inkwell.exe（整键，含 SupportedTypes/FriendlyAppName）
#   - Capabilities：HKCU\Software\Inkwell（整键）+ RegisteredApplications\Inkwell
#   - 各扩展名 OpenWithProgids 中的 Inkwell.Markdown
#   - 仅当扩展名默认值仍指向 Inkwell 时，恢复首次安装前保存的值
#   - 开始菜单 + 桌面快捷方式
#   - 安装目录 %LOCALAPPDATA%\Programs\Inkwell
#   - 通知 Shell 刷新（SHChangeNotify）
#   - 可选 -RemovePolicy：清除安装时写入的 HKLM 组策略 XML（需管理员）
#
# 注意：不会重装旧版 MarkdownReader（有意为之）。
# =============================================================================
[CmdletBinding()]
param([switch]$RemovePolicy)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$ProgId  = 'Inkwell.Markdown'
$ExeName = 'Inkwell.exe'
$Friendly = 'Inkwell'
$Target  = Join-Path $env:LOCALAPPDATA 'Programs\Inkwell'
$C       = 'HKCU:\Software\Classes'
$Exts    = @('.md', '.markdown', '.mdown', '.mkd')
$BackupRoot = 'HKCU:\Software\Inkwell\AssociationBackup'

$removed = 0
function KillKey($p) {
    if (Test-Path -LiteralPath $p) {
        try {
            Remove-Item -LiteralPath $p -Recurse -Force
            Line "[删] $p" 'Green'; $script:removed++
        } catch {
            Line "[失败] $p : $($_.Exception.Message)" 'Yellow'
        }
    } else { Line "[无] $p" 'DarkGray' }
}

Line "==================================================" 'Cyan'
Line " 卸载 Inkwell - Markdown 阅读器" 'Cyan'
Line "==================================================" 'Cyan'

# 关闭正在运行的实例（便于删除安装目录）
Get-Process -Name 'Inkwell' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 300

Head '[1/5] 删除注册表键'
KillKey "$C\$ProgId"
KillKey "$C\Applications\$ExeName"
$registered = (Get-ItemProperty -LiteralPath 'HKCU:\Software\RegisteredApplications' -Name $Friendly -ErrorAction SilentlyContinue).$Friendly
if ($registered -eq 'Software\Inkwell\Capabilities') {
    try {
        Remove-ItemProperty -LiteralPath 'HKCU:\Software\RegisteredApplications' -Name $Friendly -Force
        Line "[删] RegisteredApplications\$Friendly" 'Green'; $removed++
    } catch {
        Line "[失败] RegisteredApplications\$Friendly : $($_.Exception.Message)" 'Yellow'
    }
}

Head '[2/5] 清理扩展名关联'
foreach ($e in $Exts) {
    $owp = "$C\$e\OpenWithProgids"
    if (Get-ItemProperty -LiteralPath $owp -Name $ProgId -ErrorAction SilentlyContinue) {
        try {
            Remove-ItemProperty -LiteralPath $owp -Name $ProgId -Force
            Line "[删] $e\OpenWithProgids\$ProgId" 'Green'; $removed++
        } catch {
            Line "[失败] $e\OpenWithProgids\$ProgId : $($_.Exception.Message)" 'Yellow'
        }
    }
    $def = (Get-ItemProperty -LiteralPath "$C\$e" -Name '(default)' -ErrorAction SilentlyContinue).'(default)'
    if ($def -eq $ProgId) {
        $backup = "$BackupRoot\$($e.TrimStart('.'))"
        $saved = Get-ItemProperty -LiteralPath $backup -ErrorAction SilentlyContinue
        try {
            if ($saved -and $saved.HadDefault -eq 1 -and $saved.Value -ne $ProgId) {
                Set-ItemProperty -LiteralPath "$C\$e" -Name '(default)' -Value ([string]$saved.Value) -Force
                Line "[恢复] $e 默认值 -> $($saved.Value)" 'Green'; $removed++
            } else {
                # 没有旧默认值，或旧安装未留下可用备份：只移除明确属于 Inkwell 的值。
                Remove-ItemProperty -LiteralPath "$C\$e" -Name '(default)' -Force
                Line "[删] $e 的 Inkwell 默认值" 'Green'; $removed++
            }
        } catch {
            Line "[失败] $e 默认值处理 : $($_.Exception.Message)" 'Yellow'
        }
    }
}
KillKey 'HKCU:\Software\Inkwell'

Head '[3/5] 删除快捷方式'
$lnks = @(
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Inkwell.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Inkwell.lnk')
)
foreach ($l in $lnks) {
    if (Test-Path -LiteralPath $l) {
        try {
            Remove-Item -LiteralPath $l -Force
            Line "[删] $l" 'Green'; $removed++
        } catch {
            Line "[失败] $l : $($_.Exception.Message)" 'Yellow'
        }
    }
}

Head '[4/5] 删除安装目录'
if (Test-Path -LiteralPath $Target) {
    try {
        Remove-Item -LiteralPath $Target -Recurse -Force
        Line "[删] $Target" 'Green'; $removed++
    } catch {
        # 目录很可能被正在运行的 Inkwell.exe 占用；把异常信息一并打出来，比只说“失败”更好定位。
        Line "[失败] 目录被占用，请关闭 Inkwell 后重试：$Target（$($_.Exception.Message)）" 'Yellow'
    }
} else { Line "[无] $Target" 'DarkGray' }

if ($RemovePolicy) {
    $pol = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System'
    $cur = (Get-ItemProperty -LiteralPath $pol -Name 'DefaultAssociationsConfiguration' -ErrorAction SilentlyContinue).DefaultAssociationsConfiguration
    $ownedPolicy = Join-Path $env:ProgramData 'Inkwell\DefaultAssoc.xml'
    if ($cur -and $cur -eq $ownedPolicy) {
        try {
            Remove-ItemProperty -LiteralPath $pol -Name 'DefaultAssociationsConfiguration' -Force
            Remove-Item -LiteralPath (Split-Path $ownedPolicy -Parent) -Recurse -Force
            Line "[删] HKLM 组策略 DefaultAssociationsConfiguration" 'Green'; $removed++
        } catch {
            Line "[失败] 清理 HKLM 组策略失败（可能需要管理员权限）：$($_.Exception.Message)" 'Yellow'
        }
    }
}

Head '[5/5] 通知 Shell 刷新'
try {
    Send-ShellChangeNotify
    Line "[OK] 已通知 Shell。" 'Green'
} catch {
    Line "[警告] SHChangeNotify 失败：$($_.Exception.Message)" 'Yellow'
}

Line ""
Line "==================================================" 'Cyan'
Line (" Inkwell 卸载完成，共移除 {0} 项。" -f $removed) 'Green'
Line "==================================================" 'Cyan'
