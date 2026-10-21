' ==============================================================================
' Speech-to-Prompt Pipeline - Lançador Silencioso (Zero Terminal)
' Executa a Dynamic Island em background utilizando pythonw.exe sem abrir console.
' ==============================================================================
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Obtém o diretório absoluto da pasta do projeto
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwExe = scriptDir & "\venv\Scripts\pythonw.exe"
liveScript = scriptDir & "\src\live_pill.py"

' Verifica se o executável do venv existe, senão busca o pythonw global
If Not fso.FileExists(pythonwExe) Then
    pythonwExe = "pythonw.exe"
End If

' 0 = Janela Oculta (sem cmd.exe ou powershell), False = Não bloqueia execução
WshShell.CurrentDirectory = scriptDir
WshShell.Run """" & pythonwExe & """ """ & liveScript & """", 0, False
