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
#   - 幂等：所有删除均带 Test-Path / -ErrorAction SilentlyContinue 保护，
#     重复运行不会因“目标已不存在”而报错。
# =============================================================================

# 探测“可能不存在”的对象时用 SilentlyContinue，保证幂等；
# 真正执行删除的关键步骤会在局部 try/catch 中临时改为 Stop。
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Inkwell - 清理旧版 Markdown 阅读器遗留项" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 统计计数器，用于结尾摘要
$script:CountUninstalled = 0   # 已卸载
$script:CountRemoved     = 0   # 已删除
$script:CountSkipped     = 0   # 跳过（需管理员）
$script:CountNotFound    = 0   # 未找到

# 检测当前是否以管理员身份运行
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if ($IsAdmin) {
    Write-Host "[信息] 当前为管理员会话：将尝试清理 HKLM 项。" -ForegroundColor DarkGray
} else {
    Write-Host "[信息] 当前为普通用户会话：HKLM 项将被跳过（如需清理请以管理员重跑）。" -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# 辅助函数：删除文件夹
# -----------------------------------------------------------------------------
function Remove-LegacyFolder {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Label = $Path
    )
    if (Test-Path -LiteralPath $Path) {
        try {
            $ErrorActionPreference = 'Stop'
            Remove-Item -LiteralPath $Path -Recurse -Force
            Write-Host "[已删除] $Label" -ForegroundColor Green
            $script:CountRemoved++
        } catch {
            Write-Host "[失败]   无法删除 $Label : $($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[未找到] $Label" -ForegroundColor DarkGray
        $script:CountNotFound++
    }
}

# -----------------------------------------------------------------------------
# 辅助函数：删除文件（如快捷方式）
# -----------------------------------------------------------------------------
function Remove-LegacyFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Label = $Path
    )
    if (Test-Path -LiteralPath $Path) {
        try {
            $ErrorActionPreference = 'Stop'
            Remove-Item -LiteralPath $Path -Force
            Write-Host "[已删除] $Label" -ForegroundColor Green
            $script:CountRemoved++
        } catch {
            Write-Host "[失败]   无法删除 $Label : $($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[未找到] $Label" -ForegroundColor DarkGray
        $script:CountNotFound++
    }
}

# -----------------------------------------------------------------------------
# 辅助函数：删除整个注册表键
# -----------------------------------------------------------------------------
function Remove-LegacyRegKey {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Label = $Path
    )
    if (Test-Path -LiteralPath $Path) {
        try {
            $ErrorActionPreference = 'Stop'
            Remove-Item -LiteralPath $Path -Recurse -Force
            Write-Host "[已删除] 注册表键 $Label" -ForegroundColor Green
            $script:CountRemoved++
        } catch {
            Write-Host "[失败]   无法删除注册表键 $Label : $($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[未找到] 注册表键 $Label" -ForegroundColor DarkGray
        $script:CountNotFound++
    }
}

# =============================================================================
# 1) 旧 Inno-Setup 安装目录（MarkdownReader，~177MB）
#    优先走干净卸载：若存在 unins000.exe 则静默卸载，残留再强删。
# =============================================================================
Write-Host ""
Write-Host "--- [1/6] 旧安装目录 MarkdownReader ---" -ForegroundColor White

$mrDir   = Join-Path $env:LOCALAPPDATA "Programs\MarkdownReader"
$mrUnins = Join-Path $mrDir "unins000.exe"

if (Test-Path -LiteralPath $mrDir) {
    if (Test-Path -LiteralPath $mrUnins) {
        try {
            $ErrorActionPreference = 'Stop'
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
        Remove-LegacyFolder -Path $mrDir -Label "残留目录 $mrDir"
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
Write-Host ""
Write-Host "--- [2/6] 旧 Qt 缓存目录 MarkdownReader-Desktop ---" -ForegroundColor White
$mrDesktop = Join-Path $env:LOCALAPPDATA "MarkdownReader-Desktop"
Remove-LegacyFolder -Path $mrDesktop -Label $mrDesktop

# =============================================================================
# 3) 开始菜单快捷方式
# =============================================================================
Write-Host ""
Write-Host "--- [3/6] 开始菜单快捷方式 ---" -ForegroundColor White
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
Remove-LegacyFile -Path (Join-Path $startMenu "MarkdownReader.lnk")      -Label "开始菜单 MarkdownReader.lnk"
Remove-LegacyFile -Path (Join-Path $startMenu "卸载 MarkdownReader.lnk") -Label "开始菜单 卸载 MarkdownReader.lnk"

# =============================================================================
# 4) HKCU 注册表：旧 ProgId 与文件关联
#    - 删除整个 MarkdownReader.Document 键
#    - 仅当 .md / .markdown 的默认值指向旧 ProgId 时才清理（避免误伤）
#      注意：install.ps1 会把 .md/.markdown 重新指向 Inkwell.Markdown，
#      因此这里只需移除过期的 ProgId 默认值即可。
# =============================================================================
Write-Host ""
Write-Host "--- [4/6] HKCU 旧 ProgId / 文件关联 ---" -ForegroundColor White

# 4a) 删除旧 ProgId 整键
Remove-LegacyRegKey -Path "HKCU:\Software\Classes\MarkdownReader.Document" -Label "HKCU\...\MarkdownReader.Document"

# 4b) 若 .md 默认值 == MarkdownReader.Document，则清空该过期默认值
foreach ($ext in @('.md', '.markdown')) {
    $extKey = "HKCU:\Software\Classes\$ext"
    if (Test-Path -LiteralPath $extKey) {
        $def = (Get-ItemProperty -LiteralPath $extKey -Name '(default)' -ErrorAction SilentlyContinue).'(default)'
        if ($def -eq 'MarkdownReader.Document') {
            try {
                $ErrorActionPreference = 'Stop'
                # 把默认值置空（保留键本身，install.ps1 会重新写入 Inkwell.Markdown）
                Set-ItemProperty -LiteralPath $extKey -Name '(default)' -Value '' -Force
                Write-Host "[已删除] $ext 的过期默认关联（原 MarkdownReader.Document）" -ForegroundColor Green
                $script:CountRemoved++
            } catch {
                Write-Host "[失败]   清理 $ext 默认值失败：$($_.Exception.Message)" -ForegroundColor Yellow
            }
        } else {
            Write-Host "[跳过]   $ext 默认值为 '$def'（非旧 ProgId，保持不变）" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "[未找到] $extKey" -ForegroundColor DarkGray
        $script:CountNotFound++
    }
}

# =============================================================================
# 5) HKLM 注册表：旧 ProgId MDReader.Document 与 .md/.markdown 默认关联
#    需要管理员权限，整体 try/catch；无权限时优雅跳过并打印警告。
# =============================================================================
Write-Host ""
Write-Host "--- [5/6] HKLM 旧 ProgId / 文件关联（需管理员）---" -ForegroundColor White

if (-not $IsAdmin) {
    Write-Host "[跳过(需管理员)] HKLM 清理：当前非管理员会话。" -ForegroundColor Yellow
    Write-Host "                 如需清理，请右键以管理员身份重新运行本脚本。" -ForegroundColor Yellow
    $script:CountSkipped++
} else {
    # 5a) 删除 HKLM 旧 ProgId 整键
    $hklmProg = "HKLM:\SOFTWARE\Classes\MDReader.Document"
    if (Test-Path -LiteralPath $hklmProg) {
        try {
            $ErrorActionPreference = 'Stop'
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
    foreach ($ext in @('.md', '.markdown')) {
        $hklmExt = "HKLM:\SOFTWARE\Classes\$ext"
        if (Test-Path -LiteralPath $hklmExt) {
            $def = (Get-ItemProperty -LiteralPath $hklmExt -Name '(default)' -ErrorAction SilentlyContinue).'(default)'
            if ($def -eq 'MDReader.Document') {
                try {
                    $ErrorActionPreference = 'Stop'
                    Set-ItemProperty -LiteralPath $hklmExt -Name '(default)' -Value '' -Force
                    Write-Host "[已删除] HKLM $ext 的过期默认关联（原 MDReader.Document）" -ForegroundColor Green
                    $script:CountRemoved++
                } catch {
                    Write-Host "[跳过(需管理员)] 清理 HKLM $ext 失败：$($_.Exception.Message)" -ForegroundColor Yellow
                    $script:CountSkipped++
                }
            } else {
                Write-Host "[跳过]   HKLM $ext 默认值为 '$def'（非旧 ProgId，保持不变）" -ForegroundColor DarkGray
            }
        } else {
            Write-Host "[未找到] $hklmExt" -ForegroundColor DarkGray
            $script:CountNotFound++
        }
    }
}

# =============================================================================
# 6) 旧工具的临时资源目录
# =============================================================================
Write-Host ""
Write-Host "--- [6/6] 临时资源目录 mdreader_assets_* ---" -ForegroundColor White
$tmpPattern = Join-Path $env:TEMP "mdreader_assets_*"
$tmpDirs = Get-ChildItem -Path $tmpPattern -Directory -ErrorAction SilentlyContinue
if ($tmpDirs) {
    foreach ($d in $tmpDirs) {
        Remove-LegacyFolder -Path $d.FullName -Label "临时目录 $($d.FullName)"
    }
} else {
    Write-Host "[未找到] $tmpPattern" -ForegroundColor DarkGray
    $script:CountNotFound++
}

# =============================================================================
# 摘要
# =============================================================================
Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 清理完成 - 摘要" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host (" 已卸载        : {0}" -f $script:CountUninstalled) -ForegroundColor Green
Write-Host (" 已删除        : {0}" -f $script:CountRemoved)     -ForegroundColor Green
Write-Host (" 跳过(需管理员): {0}" -f $script:CountSkipped)     -ForegroundColor Yellow
Write-Host (" 未找到        : {0}" -f $script:CountNotFound)    -ForegroundColor DarkGray
Write-Host "--------------------------------------------------" -ForegroundColor Cyan
Write-Host " 注意：未触碰 VSCode.markdown 或任何未列出的应用。" -ForegroundColor DarkGray
Write-Host "==================================================" -ForegroundColor Cyan
