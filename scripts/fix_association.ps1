# =============================================================================
# fix_association.ps1
# 彻底清除旧 MarkdownReader/MDReader 在文件关联各处的残留（这些导致双击 .md 弹
# “选择应用”而非直接用 Inkwell），并把 .md/.markdown 默认关联重建为 Inkwell。
# 覆盖位置：
#   - HKCU/HKLM Software\Classes 下旧 ProgId（MDReader.Document / MarkdownReader.Document / md_auto_file）
#   - HKCU/HKLM Software\Classes\Applications\*（旧 exe 注册，导致“打开方式”里出现 MarkdownReader.exe）
#   - HKCU ...\Explorer\FileExts\.md|.markdown 下 OpenWithList / OpenWithProgids / UserChoiceLatest 缓存
# HKLM 项需要管理员；若被系统 ACL 保护会尝试接管所有权后删除。
# =============================================================================
$ErrorActionPreference = 'Continue'

$removed = 0
function KillKey([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            Write-Host "[删] $Path" -ForegroundColor Green
            $script:removed++
        } catch {
            Write-Host "[失败] $Path : $($_.Exception.Message)" -ForegroundColor Yellow
            return $false
        }
    } else {
        Write-Host "[无] $Path" -ForegroundColor DarkGray
    }
    return $true
}

# 接管一个 HKLM\SOFTWARE\Classes 子键的所有权并赋予管理员完全控制，然后删除
function TakeOwnAndKill([string]$subPath) {
    $full = "HKLM:\SOFTWARE\Classes\$subPath"
    if (-not (Test-Path -LiteralPath $full)) { Write-Host "[无] $full" -ForegroundColor DarkGray; return }
    if (KillKey $full) { return }   # 先直接删；不行再接管
    try {
        $admins = New-Object System.Security.Principal.SecurityIdentifier 'S-1-5-32-544'
        $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey(
            "SOFTWARE\Classes\$subPath", [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree,
            [System.Security.AccessControl.RegistryRights]::TakeOwnership)
        $acl = $key.GetAccessControl()
        $acl.SetOwner($admins)
        $key.SetAccessControl($acl)
        $acl2 = $key.GetAccessControl()
        $rule = New-Object System.Security.AccessControl.RegistryAccessRule(
            $admins, 'FullControl', 'ContainerInherit', 'None', 'Allow')
        $acl2.AddAccessRule($rule)
        $key.SetAccessControl($acl2)
        $key.Close()
        KillKey $full | Out-Null
    } catch {
        Write-Host "[失败-接管] $full : $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

Write-Host "=== [1] HKCU 旧 ProgId ===" -ForegroundColor Cyan
KillKey 'HKCU:\Software\Classes\MDReader.Document'        | Out-Null
KillKey 'HKCU:\Software\Classes\MarkdownReader.Document'  | Out-Null
KillKey 'HKCU:\Software\Classes\md_auto_file'             | Out-Null

Write-Host "=== [2] HKCU Applications 残留 ===" -ForegroundColor Cyan
foreach ($a in 'MarkdownReader.exe','MarkdownReader-Web.exe','MarkdownReader-Desktop.exe','MDReader.exe') {
    KillKey "HKCU:\Software\Classes\Applications\$a" | Out-Null
}

Write-Host "=== [3] FileExts 缓存 (.md/.markdown) ===" -ForegroundColor Cyan
foreach ($e in '.md','.markdown') {
    $b = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$e"
    KillKey "$b\OpenWithList"      | Out-Null
    KillKey "$b\OpenWithProgids"   | Out-Null
    KillKey "$b\UserChoiceLatest"  | Out-Null
}

Write-Host "=== [4] HKLM 旧 ProgId / Applications（需管理员）===" -ForegroundColor Cyan
TakeOwnAndKill 'MDReader.Document'
TakeOwnAndKill 'MarkdownReader.Document'
foreach ($a in 'MarkdownReader.exe','MarkdownReader-Web.exe','MarkdownReader-Desktop.exe','MDReader.exe') {
    TakeOwnAndKill "Applications\$a"
}

Write-Host "=== [5] 重新确立 Inkwell 默认关联 (HKCU) ===" -ForegroundColor Cyan
$exe = Join-Path $env:LOCALAPPDATA 'Programs\Inkwell\Inkwell.exe'
function SetDefault($path, $val){ if(-not(Test-Path -LiteralPath $path)){ New-Item -Path $path -Force | Out-Null }; Set-ItemProperty -LiteralPath $path -Name '(default)' -Value $val -Force }
SetDefault 'HKCU:\Software\Classes\Inkwell.Markdown' 'Markdown 文档'
SetDefault 'HKCU:\Software\Classes\Inkwell.Markdown\DefaultIcon' ('"{0}",0' -f $exe)
SetDefault 'HKCU:\Software\Classes\Inkwell.Markdown\shell\open' '使用 Inkwell 打开'
SetDefault 'HKCU:\Software\Classes\Inkwell.Markdown\shell\open\command' ('"{0}" "%1"' -f $exe)
foreach ($e in '.md','.markdown') {
    $k = "HKCU:\Software\Classes\$e"
    SetDefault $k 'Inkwell.Markdown'
    if (-not (Test-Path -LiteralPath "$k\OpenWithProgids")) { New-Item -Path "$k\OpenWithProgids" -Force | Out-Null }
    New-ItemProperty -LiteralPath "$k\OpenWithProgids" -Name 'Inkwell.Markdown' -PropertyType None -Value ([byte[]]@()) -Force | Out-Null
}
if ($env:LOCALAPPDATA) { Set-ItemProperty -LiteralPath 'HKCU:\Software\Classes\.md' -Name 'Content Type' -Value 'text/markdown' -Force }

Write-Host "=== [6] 通知 Shell 刷新 ===" -ForegroundColor Cyan
if (-not ('Ink.Sh' -as [type])) {
    Add-Type -Namespace 'Ink' -Name 'Sh' -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int e, uint f, System.IntPtr a, System.IntPtr b);
'@
}
[Ink.Sh]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)

Write-Host ("`n完成：删除 $removed 个残留键。.md 默认 = " + (Get-Item 'HKCU:\Software\Classes\.md').GetValue('')) -ForegroundColor Green
