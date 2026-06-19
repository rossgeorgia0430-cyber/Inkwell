# =============================================================================
# cleanup_qq_and_fix_md.ps1   —— 请右键「以管理员身份运行 PowerShell」后执行
# 作用：
#   1) 清除 QQ电脑管家(QQPCMgr) 的残留：孤立内核驱动、注册表劫持(Unknown 处理器/
#      启动项/卸载项)、空安装目录。（其主程序 exe 已不在，只剩这些垃圾）
#   2) 把 .md / .markdown 的关联重建并锁定为 Inkwell。
# 说明：QQ 的驱动可能有自我保护，删除失败属正常——脚本会把它们设为“禁用”，
#   重启后即不再加载，再次运行本脚本即可彻底删除目录与驱动。
# =============================================================================
$ErrorActionPreference = 'SilentlyContinue'

function Admin() {
    return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}
if (-not (Admin)) {
    Write-Host "需要管理员权限。请右键 PowerShell -> 以管理员身份运行，再执行本脚本。" -ForegroundColor Red
    exit 1
}

Write-Host "==== [1] 处理 QQPCMgr 孤立驱动/服务 ====" -ForegroundColor Cyan
# 仅匹配二进制路径含 QQPCMgr 的驱动与服务（绝不动 Windows 自带服务）
$targets = @()
$targets += Get-CimInstance Win32_SystemDriver | Where-Object { $_.PathName -match 'QQPCMgr' } | Select-Object -ExpandProperty Name
$targets += Get-CimInstance Win32_Service       | Where-Object { $_.PathName -match 'QQPCMgr' } | Select-Object -ExpandProperty Name
$targets = $targets | Sort-Object -Unique
if (-not $targets) { Write-Host "  未发现 QQPCMgr 驱动/服务。" -ForegroundColor DarkGray }
foreach ($name in $targets) {
    Write-Host "  - $name : 停止/禁用/删除..."
    & sc.exe stop $name   | Out-Null
    & sc.exe config $name start= disabled | Out-Null
    $del = & sc.exe delete $name 2>&1
    Write-Host ("    sc delete -> " + ($del -join ' '))
}

Write-Host "==== [2] 清除 QQ 注册表残留（指向已删除 exe 的悬空项）====" -ForegroundColor Cyan
# Unknown 文件处理器劫持（QQ 把所有未知类型导向 QQPCFileOpen.exe，该 exe 已不存在）
if (Test-Path 'HKLM:\SOFTWARE\Classes\Unknown\shell\open') {
    Remove-Item 'HKLM:\SOFTWARE\Classes\Unknown\shell\open' -Recurse -Force
    Write-Host "  [删] HKLM Unknown\shell\open（恢复 Windows 默认“打开方式”行为）"
}
# 启动项
Remove-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run' -Name 'QQPCTray' -Force -EA SilentlyContinue
Remove-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' -Name 'QQPCTray' -Force -EA SilentlyContinue
Write-Host "  [删] 启动项 QQPCTray（如存在）"
# 卸载项
foreach ($u in 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*') {
    Get-ChildItem $u -EA SilentlyContinue | Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -match '电脑管家|QQPCMgr' } | ForEach-Object {
        Remove-Item $_.PSPath -Recurse -Force; Write-Host ("  [删] 卸载项 " + $_.PSChildName)
    }
}

Write-Host "==== [3] 删除残留安装目录 ====" -ForegroundColor Cyan
$qqdir = 'C:\Program Files (x86)\Tencent\QQPCMgr'
if (Test-Path $qqdir) {
    Remove-Item $qqdir -Recurse -Force
    if (Test-Path $qqdir) { Write-Host "  [保留] 目录仍被驱动占用，重启后再次运行本脚本即可删除。" -ForegroundColor Yellow }
    else { Write-Host "  [删] $qqdir" }
}
# 若 Tencent 下已空，一并删空目录（不动 WeChat 等其它腾讯产品）
$ten = 'C:\Program Files (x86)\Tencent'
if ((Test-Path $ten) -and -not (Get-ChildItem $ten)) { Remove-Item $ten -Force; Write-Host "  [删] 空目录 $ten" }

Write-Host "==== [4] 重建并锁定 Inkwell 的 .md / .markdown 关联 ====" -ForegroundColor Cyan
$exe = Join-Path $env:LOCALAPPDATA 'Programs\Inkwell\Inkwell.exe'
if (-not (Test-Path $exe)) { Write-Host "  [警告] 未找到 $exe，请先安装 Inkwell（scripts\install.ps1）。" -ForegroundColor Yellow }
function SetDef($p,$v){ if(-not(Test-Path $p)){New-Item $p -Force|Out-Null}; Set-ItemProperty $p '(default)' $v -Force }
SetDef 'HKCU:\Software\Classes\Inkwell.Markdown' 'Markdown 文档'
SetDef 'HKCU:\Software\Classes\Inkwell.Markdown\DefaultIcon' ('"{0}",0' -f $exe)
SetDef 'HKCU:\Software\Classes\Inkwell.Markdown\shell\open\command' ('"{0}" "%1"' -f $exe)
foreach ($e in '.md','.markdown') {
    SetDef "HKCU:\Software\Classes\$e" 'Inkwell.Markdown'
    if(-not(Test-Path "HKCU:\Software\Classes\$e\OpenWithProgids")){New-Item "HKCU:\Software\Classes\$e\OpenWithProgids" -Force|Out-Null}
    New-ItemProperty "HKCU:\Software\Classes\$e\OpenWithProgids" -Name 'Inkwell.Markdown' -PropertyType None -Value ([byte[]]@()) -Force | Out-Null
}
Add-Type -Namespace Q -Name S -MemberDefinition '[System.Runtime.InteropServices.DllImport("shell32.dll")] public static extern void SHChangeNotify(int e, uint f, System.IntPtr a, System.IntPtr b);'
[Q.S]::SHChangeNotify(0x08000000,0,[IntPtr]::Zero,[IntPtr]::Zero)

Write-Host ""
Write-Host "==== 完成 ====" -ForegroundColor Green
Write-Host " - QQ残留已尽量清除（若提示目录被占用，请重启后再跑一次本脚本）。"
Write-Host " - .md/.markdown 已指向 Inkwell。"
Write-Host " - 最后一步（Windows 安全机制要求手动一次）：双击任意 .md ->"
Write-Host "   在弹窗里选择 Inkwell -> 勾选「始终使用此应用打开 .md 文件」。此后永久直接用 Inkwell 打开。"
