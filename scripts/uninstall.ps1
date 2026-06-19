# =============================================================================
# uninstall.ps1  —  卸载 Inkwell，移除其完整注册与文件
# -----------------------------------------------------------------------------
# 清理本程序自身留下的（全部位于 HKCU 与当前用户目录，无需管理员）：
#   - ProgId  Inkwell.Markdown（整键）
#   - Applications\Inkwell.exe（整键，含 SupportedTypes/FriendlyAppName）
#   - Capabilities：HKCU\Software\Inkwell（整键）+ RegisteredApplications\Inkwell
#   - 各扩展名 OpenWithProgids 中的 Inkwell.Markdown；默认值若指向本程序则清空
#   - 开始菜单 + 桌面快捷方式
#   - 安装目录 %LOCALAPPDATA%\Programs\Inkwell
#   - 通知 Shell 刷新（SHChangeNotify）
#   - 可选 -RemovePolicy：清除安装时写入的 HKLM 组策略 XML（需管理员）
#
# 注意：不会重装旧版 MarkdownReader（有意为之）。
# =============================================================================
[CmdletBinding()]
param([switch]$RemovePolicy)

$ErrorActionPreference = 'SilentlyContinue'

$ProgId  = 'Inkwell.Markdown'
$ExeName = 'Inkwell.exe'
$Friendly = 'Inkwell'
$Target  = Join-Path $env:LOCALAPPDATA 'Programs\Inkwell'
$C       = 'HKCU:\Software\Classes'
$Exts    = @('.md', '.markdown', '.mdown', '.mkd')

$removed = 0
function Line($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function KillKey($p) {
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Recurse -Force
        if (-not (Test-Path -LiteralPath $p)) { Line "[删] $p" 'Green'; $script:removed++ }
        else { Line "[失败] $p" 'Yellow' }
    } else { Line "[无] $p" 'DarkGray' }
}

Line "==================================================" 'Cyan'
Line " 卸载 Inkwell - Markdown 阅读器" 'Cyan'
Line "==================================================" 'Cyan'

# 关闭正在运行的实例（便于删除安装目录）
Get-Process -Name 'Inkwell' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 300

Line ""; Line "--- [1/5] 删除注册表键 ---" 'White'
KillKey "$C\$ProgId"
KillKey "$C\Applications\$ExeName"
KillKey 'HKCU:\Software\Inkwell'
if (Get-ItemProperty -LiteralPath 'HKCU:\Software\RegisteredApplications' -Name $Friendly -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -LiteralPath 'HKCU:\Software\RegisteredApplications' -Name $Friendly -Force
    Line "[删] RegisteredApplications\$Friendly" 'Green'; $removed++
}

Line ""; Line "--- [2/5] 清理扩展名关联 ---" 'White'
foreach ($e in $Exts) {
    $owp = "$C\$e\OpenWithProgids"
    if (Get-ItemProperty -LiteralPath $owp -Name $ProgId -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -LiteralPath $owp -Name $ProgId -Force
        Line "[删] $e\OpenWithProgids\$ProgId" 'Green'; $removed++
    }
    $def = (Get-ItemProperty -LiteralPath "$C\$e" -Name '(default)' -ErrorAction SilentlyContinue).'(default)'
    if ($def -eq $ProgId) {
        Set-ItemProperty -LiteralPath "$C\$e" -Name '(default)' -Value '' -Force
        Line "[删] $e 默认值（原 $ProgId，已清空）" 'Green'; $removed++
    }
}

Line ""; Line "--- [3/5] 删除快捷方式 ---" 'White'
$lnks = @(
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Inkwell.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Inkwell.lnk')
)
foreach ($l in $lnks) {
    if (Test-Path -LiteralPath $l) { Remove-Item -LiteralPath $l -Force; Line "[删] $l" 'Green'; $removed++ }
}

Line ""; Line "--- [4/5] 删除安装目录 ---" 'White'
if (Test-Path -LiteralPath $Target) {
    Remove-Item -LiteralPath $Target -Recurse -Force
    if (Test-Path -LiteralPath $Target) { Line "[失败] 目录被占用，请关闭 Inkwell 后重试：$Target" 'Yellow' }
    else { Line "[删] $Target" 'Green'; $removed++ }
} else { Line "[无] $Target" 'DarkGray' }

if ($RemovePolicy) {
    $pol = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System'
    $cur = (Get-ItemProperty -LiteralPath $pol -Name 'DefaultAssociationsConfiguration' -ErrorAction SilentlyContinue).DefaultAssociationsConfiguration
    if ($cur -and $cur -like '*Inkwell*') {
        Remove-ItemProperty -LiteralPath $pol -Name 'DefaultAssociationsConfiguration' -Force
        Remove-Item -LiteralPath (Join-Path $env:ProgramData 'Inkwell') -Recurse -Force
        Line "[删] HKLM 组策略 DefaultAssociationsConfiguration" 'Green'; $removed++
    }
}

Line ""; Line "--- [5/5] 通知 Shell 刷新 ---" 'White'
try {
    if (-not ('Inkwell.Shell32Native' -as [type])) {
        Add-Type -Namespace 'Inkwell' -Name 'Shell32Native' -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int wEventId, uint uFlags, System.IntPtr dwItem1, System.IntPtr dwItem2);
'@
    }
    [Inkwell.Shell32Native]::SHChangeNotify(0x08000000, 0x1000, [IntPtr]::Zero, [IntPtr]::Zero)
    Line "[OK] 已通知 Shell。" 'Green'
} catch { Line "[警告] SHChangeNotify 失败。" 'Yellow' }

Line ""
Line "==================================================" 'Cyan'
Line (" Inkwell 卸载完成，共移除 {0} 项。" -f $removed) 'Green'
Line "==================================================" 'Cyan'
