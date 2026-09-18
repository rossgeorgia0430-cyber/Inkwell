# =============================================================================
# cleanup_legacy.ps1
# -----------------------------------------------------------------------------
# 用途：清理旧版 Markdown 阅读器（MarkdownReader / MDReader）遗留的安装目录、
#       缓存、开始菜单快捷方式、注册表文件关联与 ProgId、以及临时资源目录。
#
# 安全说明：
#   - 本脚本只删除“已核实清单”内的项目，绝不触碰 VSCode.markdown 或任何
#     未列出的应用。
#   - HKCU（当前用户）相关操作无需管理员权限。
#   - HKLM（本机）相关操作需要管理员权限：请右键 → “以管理员身份运行”。
#     若非管理员运行，HKLM 部分会被安全跳过并打印警告，不会报错中断。
#   - 幂等：所有删除均带 Test-Path 保护，重复运行不会因“目标已不存在”而报错；
#     真正执行删除失败时会打印原因，不会中断脚本。
# =============================================================================

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

Line "==================================================" 'Cyan'
Line " Inkwell - 清理旧版 Markdown 阅读器遗留项" 'Cyan'
Line "==================================================" 'Cyan'

# 统计计数器，用于结尾摘要
$script:CountUninstalled = 0   # 已卸载
$script:CountRemoved     = 0   # 已删除
$script:CountSkipped     = 0   # 跳过（需管理员）
$script:CountNotFound    = 0   # 未找到

$IsAdmin = Test-Admin
if ($IsAdmin) {
    Write-Host "[信息] 当前为管理员会话：将尝试清理 HKLM 项。" -ForegroundColor DarkGray
} else {
    Write-Host "[信息] 当前为普通用户会话：HKLM 项将被跳过（如需清理请以管理员重跑）。" -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# 删除一个遗留文件/目录/注册表键：三者对 Remove-Item 而言操作等价
# （-Recurse 对非容器项是无害的空操作），只有提示文案里要不要点出“注册表键”不同。
# -----------------------------------------------------------------------------
function Remove-LegacyItem {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Label = $Path,
        [string]$Kind = ''   # 传 '注册表键' 会在提示里点出这是注册表键
    )
    $desc = if ($Kind) { "$Kind $Label" } else { $Label }
    $verb = if ($Kind) { "无法删除$Kind" } else { '无法删除' }
    if (Test-Path -LiteralPath $Path) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force
            Write-Host "[已删除] $desc" -ForegroundColor Green
            $script:CountRemoved++
        } catch {
            Write-Host "[失败]   $verb $Label : $($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[未找到] $desc" -ForegroundColor DarkGray
        $script:CountNotFound++
    }
}

# -----------------------------------------------------------------------------
# 清空一个扩展名的默认关联，如果它仍指向某个旧 ProgId。
# HKCU 与 HKLM 下的处理逻辑完全一样，只是根键、旧 ProgId 名字、提示文案前缀不同；
# HKLM 失败大概率是权限不足（未以管理员运行），按“跳过”而非“失败”计数。
# -----------------------------------------------------------------------------
function Clear-LegacyExtensionDefault {
    param(
        [Parameter(Mandatory = $true)][string]$ClassesRoot,
        [Parameter(Mandatory = $true)][string]$OldProgId,
        [string]$Prefix = '',
        [switch]$RequiresAdmin
    )
    foreach ($ext in @('.md', '.markdown')) {
        $extKey = "$ClassesRoot\$ext"
        if (-not (Test-Path -LiteralPath $extKey)) {
            Write-Host "[未找到] $extKey" -ForegroundColor DarkGray
            $script:CountNotFound++
            continue
        }
        $def = (Get-ItemProperty -LiteralPath $extKey -Name '(default)' -ErrorAction SilentlyContinue).'(default)'
        if ($def -ne $OldProgId) {
            Write-Host "[跳过]   $Prefix$ext 默认值为 '$def'（非旧 ProgId，保持不变）" -ForegroundColor DarkGray
            continue
        }
        try {
            Set-ItemProperty -LiteralPath $extKey -Name '(default)' -Value '' -Force
            Write-Host "[已删除] $Prefix$ext 的过期默认关联（原 $OldProgId）" -ForegroundColor Green
            $script:CountRemoved++
        } catch {
            if ($RequiresAdmin) {
                Write-Host "[跳过(需管理员)] 清理 $Prefix$ext 失败：$($_.Exception.Message)" -ForegroundColor Yellow
                $script:CountSkipped++
            } else {
                Write-Host "[失败]   清理 $ext 默认值失败：$($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }
}

# =============================================================================
# 1) 旧 Inno-Setup 安装目录（MarkdownReader，~177MB）
#    优先走干净卸载：若存在 unins000.exe 则静默卸载，残留再强删。
# =============================================================================
Head '[1/6] 旧安装目录 MarkdownReader'

$mrDir   = Join-Path $env:LOCALAPPDATA "Programs\MarkdownReader"
$mrUnins = Join-Path $mrDir "unins000.exe"

if (Test-Path -LiteralPath $mrDir) {
    if (Test-Path -LiteralPath $mrUnins) {
        try {
            Write-Host "[执行]   运行卸载程序 unins000.exe /VERYSILENT ..." -ForegroundColor DarkGray
            Start-Process -FilePath $mrUnins -ArgumentList '/VERYSILENT', '/NORESTART' -Wait
            Write-Host "[已卸载] MarkdownReader（通过 unins000.exe）" -ForegroundColor Green
            $script:CountUninstalled++
        } catch {
            Write-Host "[警告]   运行 unins000.exe 失败：$($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[信息]   未发现 unins000.exe，将直接强删目录。" -ForegroundColor DarkGray
    }
    # 卸载后若目录仍残留，强制删除
    if (Test-Path -LiteralPath $mrDir) {
        Remove-LegacyItem -Path $mrDir -Label "残留目录 $mrDir"
    } else {
        Write-Host "[信息]   目录已被卸载程序清除。" -ForegroundColor DarkGray
    }
} else {
    Write-Host "[未找到] $mrDir" -ForegroundColor DarkGray
    $script:CountNotFound++
}

# =============================================================================
# 2) 旧 Qt 缓存目录（基本为空）
# =============================================================================
Head '[2/6] 旧 Qt 缓存目录 MarkdownReader-Desktop'
$mrDesktop = Join-Path $env:LOCALAPPDATA "MarkdownReader-Desktop"
Remove-LegacyItem -Path $mrDesktop -Label $mrDesktop

# =============================================================================
# 3) 开始菜单快捷方式
# =============================================================================
Head '[3/6] 开始菜单快捷方式'
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
Remove-LegacyItem -Path (Join-Path $startMenu "MarkdownReader.lnk")      -Label "开始菜单 MarkdownReader.lnk"
Remove-LegacyItem -Path (Join-Path $startMenu "卸载 MarkdownReader.lnk") -Label "开始菜单 卸载 MarkdownReader.lnk"

# =============================================================================
# 4) HKCU 注册表：旧 ProgId 与文件关联
#    - 删除整个 MarkdownReader.Document 键
#    - 仅当 .md / .markdown 的默认值指向旧 ProgId 时才清理（避免误伤）
#      注意：install.ps1 会把 .md/.markdown 重新指向 Inkwell.Markdown，
#      因此这里只需移除过期的 ProgId 默认值即可。
# =============================================================================
Head '[4/6] HKCU 旧 ProgId / 文件关联'

# 4a) 删除旧 ProgId 整键
Remove-LegacyItem -Path "HKCU:\Software\Classes\MarkdownReader.Document" -Label "HKCU\...\MarkdownReader.Document" -Kind '注册表键'

# 4b) 若 .md 默认值 == MarkdownReader.Document，则清空该过期默认值
Clear-LegacyExtensionDefault -ClassesRoot 'HKCU:\Software\Classes' -OldProgId 'MarkdownReader.Document'

# =============================================================================
# 5) HKLM 注册表：旧 ProgId MDReader.Document 与 .md/.markdown 默认关联
#    需要管理员权限，整体 try/catch；无权限时优雅跳过并打印警告。
# =============================================================================
Head '[5/6] HKLM 旧 ProgId / 文件关联（需管理员）'

if (-not $IsAdmin) {
    Write-Host "[跳过(需管理员)] HKLM 清理：当前非管理员会话。" -ForegroundColor Yellow
    Write-Host "                 如需清理，请右键以管理员身份重新运行本脚本。" -ForegroundColor Yellow
    $script:CountSkipped++
} else {
    # 5a) 删除 HKLM 旧 ProgId 整键
    $hklmProg = "HKLM:\SOFTWARE\Classes\MDReader.Document"
    if (Test-Path -LiteralPath $hklmProg) {
        try {
            Remove-Item -LiteralPath $hklmProg -Recurse -Force
            Write-Host "[已删除] 注册表键 HKLM\SOFTWARE\Classes\MDReader.Document" -ForegroundColor Green
            $script:CountRemoved++
        } catch {
            Write-Host "[跳过(需管理员)] 删除 HKLM MDReader.Document 失败：$($_.Exception.Message)" -ForegroundColor Yellow
            $script:CountSkipped++
        }
    } else {
        Write-Host "[未找到] HKLM\SOFTWARE\Classes\MDReader.Document" -ForegroundColor DarkGray
        $script:CountNotFound++
    }

    # 5b) 若 HKLM .md/.markdown 默认值 == MDReader.Document，则清空
    Clear-LegacyExtensionDefault -ClassesRoot 'HKLM:\SOFTWARE\Classes' -OldProgId 'MDReader.Document' -Prefix 'HKLM ' -RequiresAdmin
}

# =============================================================================
# 6) 旧工具的临时资源目录
# =============================================================================
Head '[6/6] 临时资源目录 mdreader_assets_*'
$tmpPattern = Join-Path $env:TEMP "mdreader_assets_*"
$tmpDirs = Get-ChildItem -Path $tmpPattern -Directory -ErrorAction SilentlyContinue
if ($tmpDirs) {
    foreach ($d in $tmpDirs) {
        Remove-LegacyItem -Path $d.FullName -Label "临时目录 $($d.FullName)"
    }
} else {
    Write-Host "[未找到] $tmpPattern" -ForegroundColor DarkGray
    $script:CountNotFound++
}

# =============================================================================
# 摘要
# =============================================================================
Line "" 'Cyan'
Line "==================================================" 'Cyan'
Line " 清理完成 - 摘要" 'Cyan'
Line "==================================================" 'Cyan'
Line (" 已卸载        : {0}" -f $script:CountUninstalled) 'Green'
Line (" 已删除        : {0}" -f $script:CountRemoved)     'Green'
Line (" 跳过(需管理员): {0}" -f $script:CountSkipped)     'Yellow'
Line (" 未找到        : {0}" -f $script:CountNotFound)    'DarkGray'
Line "--------------------------------------------------" 'Cyan'
Line " 注意：未触碰 VSCode.markdown 或任何未列出的应用。" 'DarkGray'
Line "==================================================" 'Cyan'
