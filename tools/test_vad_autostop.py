"""Teste automatizado da detecção de silêncio (Auto-Stop VAD)."""

import os
import sys
import time
from pathlib import Path

# Auto-reexec no venv
_project_root = Path(__file__).resolve().parent.parent
_venv_python = _project_root / "venv" / "Scripts" / "python.exe"
if _venv_python.exists() and Path(sys.executable).resolve() != _venv_python.resolve():
    import subprocess
    sys.exit(subprocess.call([str(_venv_python)] + sys.argv))

if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import tkinter as tk
from src.live_pill import LivePillApp

print("Iniciando teste de VAD Auto-Stop...")
root = tk.Tk()
root.withdraw()
app = LivePillApp(root)

# Simula início da gravação
app._start_audio_capture()
assert app.state == "recording", f"Esperado state='recording', obtido '{app.state}'"
assert not app.has_spoken, "has_spoken deve iniciar como False"

print("1. Injetando energia de voz simulada (RMS = 0.05)...")
import numpy as np
app.audio_chunks.append(np.zeros(16000, dtype=np.float32))
app.current_rms = 0.05
# Executa 5 ticks de animação para simular o loop
for _ in range(5):
    app.last_frame_time = time.perf_counter() - 0.02
    app._animation_tick()

assert app.has_spoken, "Falha: has_spoken deveria ser True após energia de voz"
print("   -> Voz detectada com sucesso! has_spoken = True")

print("2. Injetando silêncio pós-fala (RMS = 0.002)...")
app.current_rms = 0.002
app.smoothed_rms = 0.002
# Define o timestamp de fala para 1.0s atrás
app.last_speech_time = time.time() - 1.0
app.last_frame_time = time.perf_counter() - 0.02
app._animation_tick()

assert app.is_silence_countdown, "Falha: is_silence_countdown deveria ser True após 1s de silêncio"
print("   -> Countdown de silêncio ativo! is_silence_countdown = True")

print("3. Avançando tempo de silêncio para 2.1s...")
# Simula que passaram 2.1s de silêncio contínuo
app.current_rms = 0.002
app.smoothed_rms = 0.002
app.last_speech_time = time.time() - 2.1
app.last_frame_time = time.perf_counter() - 0.02

# O tick deve acionar _stop_audio_capture() automaticamente
app._animation_tick()
assert app.state in ("processing", "idle"), f"Esperado transição para processing, obtido: {app.state}"
print("   -> Auto-Stop disparado com sucesso sem intervenção manual!")

app.destroy()
print("\nTODOS OS TESTES DE AUTO-STOP POR SILÊNCIO PASSARAM COM SUCESSO! [PASS]")
