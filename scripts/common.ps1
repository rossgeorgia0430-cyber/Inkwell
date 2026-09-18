# =============================================================================
# common.ps1  —  install.ps1 / uninstall.ps1 / cleanup_legacy.ps1 共用的小工具
# -----------------------------------------------------------------------------
# 只放三者都要用的东西：带颜色的输出、注册表默认值写入、管理员判断、
# 通知 Shell 刷新关联缓存。由调用方用
#   . (Join-Path $PSScriptRoot 'common.ps1')
# 引入，不单独设置 $ErrorActionPreference（沿用调用方的设置）。
# =============================================================================

function Line($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Head($t) { Write-Host ""; Line "--- $t ---" 'White' }

function Ensure-Key($p) { if (-not (Test-Path -LiteralPath $p)) { New-Item -Path $p -Force | Out-Null } }
function Set-Default($p, $v) { Ensure-Key $p; Set-ItemProperty -LiteralPath $p -Name '(default)' -Value $v -Force }

function Test-Admin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

# 通知 Shell 文件关联缓存已变化（SHCNE_ASSOCCHANGED）。不在这里捕获异常：
# 失败原因要留给调用方的 try/catch 打印，不能在这里就地丢掉。
function Send-ShellChangeNotify {
    if (-not ('Inkwell.Shell32Native' -as [type])) {
        Add-Type -Namespace 'Inkwell' -Name 'Shell32Native' -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int wEventId, uint uFlags, System.IntPtr dwItem1, System.IntPtr dwItem2);
'@
    }
    [Inkwell.Shell32Native]::SHChangeNotify(0x08000000, 0x1000, [IntPtr]::Zero, [IntPtr]::Zero)
}
