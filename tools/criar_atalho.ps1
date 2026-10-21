# ==============================================================================
# Speech-to-Prompt Pipeline - Gerador de Atalho na Área de Trabalho
# Cria o atalho "Speech to Prompt.lnk" no Desktop apontando para o inicializador silencioso.
# ==============================================================================
$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [Environment]::GetFolderPath('Desktop')
$ProjectDir = Split-Path -Parent $PSScriptRoot
$ShortcutPath = Join-Path $DesktopPath "Speech to Prompt.lnk"
$VbsPath = Join-Path $ProjectDir "iniciar_live.vbs"

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "wscript.exe"
$Shortcut.Arguments = "`"$VbsPath`""
$Shortcut.WorkingDirectory = $ProjectDir

# Ícone oficial personalizado em alta resolução do aplicativo
$CustomIcon = Join-Path $ProjectDir "assets\app-icon.ico"
if (Test-Path $CustomIcon) {
    $Shortcut.IconLocation = "$CustomIcon,0"
} elseif (Test-Path "$env:SystemRoot\System32\imageres.dll") {
    $Shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,99"
}

$Shortcut.Description = "Speech-to-Prompt Pipeline - Apple Dynamic Island (Atalho F8)"
$Shortcut.Save()

Write-Host ""
Write-Host ">>> SUCESSO: Atalho 'Speech to Prompt' criado na sua Area de Trabalho!" -ForegroundColor Green
Write-Host "    Arquivo: $ShortcutPath"
Write-Host "    Basta dar dois cliques no icone da Area de Trabalho para iniciar em background."
Write-Host ""
