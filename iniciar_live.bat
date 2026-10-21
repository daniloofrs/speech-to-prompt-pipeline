@echo off
:: ==============================================================================
:: Speech-to-Prompt Pipeline - Lançador Rápido Windows
:: Inicia a Dynamic Island sem travar o terminal (janela fecha imediatamente).
:: ==============================================================================
cd /d "%~dp0"

if exist "%~dp0venv\Scripts\pythonw.exe" (
    start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0src\live_pill.py"
) else (
    start "" pythonw "%~dp0src\live_pill.py"
)

exit
