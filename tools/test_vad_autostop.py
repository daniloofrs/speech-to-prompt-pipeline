"""Teste automatizado da detecção de silêncio e sensibilidade adaptativa (Auto-Stop VAD)."""

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

import numpy as np
import tkinter as tk
from src.live_pill import LivePillApp

print("Iniciando testes de VAD Auto-Stop e Sensibilidade Adaptativa...")
root = tk.Tk()
root.withdraw()
app = LivePillApp(root)

# Simula início da gravação
app._start_audio_capture()
assert app.state == "recording", f"Esperado state='recording', obtido '{app.state}'"
assert not app.has_spoken, "has_spoken deve iniciar como False"
assert app.accumulated_speech_s == 0.0, "accumulated_speech_s deve iniciar como 0.0"

# --- Teste 1: Inatividade Inicial Prolongada (Timeout sem fala) ---
print("\n1. Testando timeout de inatividade inicial (6.0s sem fala)...")
app.record_start_time = time.time() - 6.5
app.current_rms = 0.001
app.smoothed_rms = 0.001
app.last_frame_time = time.perf_counter() - 0.02
app._animation_tick()

assert app.state == "toast", f"Esperado state='toast', obtido '{app.state}'"
assert app.audio_stream is None, f"Falha: audio_stream deveria ser None após timeout de inatividade inicial, mas vazou: {app.audio_stream}"
print("   -> Inatividade inicial tratada com sucesso! state = 'toast', stream liberado.")

# --- Teste 2: Sensibilidade para Voz Baixa (Quiet Voice Detection, ex: RMS = 0.010) ---
print("\n2. Testando sensibilidade para voz baixa (RMS = 0.010)...")
app._start_audio_capture()
if app.audio_stream and app.audio_stream.active:
    app.audio_stream.stop()
assert app.state == "recording", f"Esperado state='recording', obtido '{app.state}'"
assert not app.has_spoken, "has_spoken deve iniciar como False"

# Injeta voz baixa: RMS = 0.010 (antes descartada pelo limiar rígido de 0.016)
for _ in range(12):
    app.current_rms = 0.010
    app.last_frame_time = time.perf_counter() - 0.02
    app._animation_tick()

assert app.has_spoken, (
    f"Falha: voz baixa (RMS=0.010) deveria ativar has_spoken=True. "
    f"smoothed_rms={app.smoothed_rms:.4f}, ambient_floor={app.ambient_rms_floor:.4f}"
)
assert app.accumulated_speech_s > 0.0, "Falha: accumulated_speech_s deveria ter acumulado tempo de fala"
print(f"   -> Voz baixa detectada com sucesso! has_spoken = True, accumulated = {app.accumulated_speech_s:.2f}s")

# --- Teste 3: Proteção contra Micro-blips (< 0.5s de fala acumulada) ---
print("\n3. Testando proteção contra micro-blips (< 0.5s de fala)...")
app._start_audio_capture()
if app.audio_stream and app.audio_stream.active:
    app.audio_stream.stop()
assert not app.has_spoken
assert app.accumulated_speech_s == 0.0

# Simula micro-blip: 2 frames de áudio mais forte (0.04s de fala acumulada)
for _ in range(2):
    app.current_rms = 0.035
    app.last_frame_time = time.perf_counter() - 0.02
    app._animation_tick()

assert app.has_spoken, "Falha: micro-blip deveria acionar has_spoken temporariamente"
assert app.accumulated_speech_s < 0.5, (
    f"Micro-blip deve ter < 0.5s acumulados, obtido: {app.accumulated_speech_s:.3f}s"
)

# Injeta silêncio subsequente
app.current_rms = 0.002
app.smoothed_rms = 0.002

# Simula 1.0s de silêncio pós-blip
app.last_speech_time = time.time() - 1.0
app.last_frame_time = time.perf_counter() - 0.02
app._animation_tick()

# Não deve ativar contagem regressiva de silêncio por não ter 0.5s de fala acumulada
assert not app.is_silence_countdown, (
    "Falha: is_silence_countdown NÃO deve ativar para micro-blips com menos de 0.5s de fala acumulada"
)

# Simula 2.5s de silêncio pós-blip
app.last_speech_time = time.time() - 2.5
app.last_frame_time = time.perf_counter() - 0.02
app._animation_tick()

# Não deve ter feito auto-stop como se fosse fala real concluída
assert app.state == "recording", (
    f"Falha: gravação não deveria ter sido auto-concluída após micro-blip. state='{app.state}'"
)

# Se o silêncio se estender além de 6.0s (inatividade inicial prolongada), cancela sem falso positivo
app.record_start_time = time.time() - 6.5
app.last_frame_time = time.perf_counter() - 0.02
app._animation_tick()

assert app.state == "toast", f"Esperado state='toast' por inatividade após micro-blip, obtido '{app.state}'"
assert app.audio_stream is None, "Falha: audio_stream deveria ser liberado"
print("   -> Proteção contra micro-blip validada: silêncio de 2s ignorado e cancelamento gracioso após 6s.")

# --- Teste 4: Fala Sustentada (>= 0.5s) com Auto-Stop após Silêncio ---
print("\n4. Testando fala sustentada (>= 0.5s) e auto-stop após silêncio...")
app._start_audio_capture()
if app.audio_stream and app.audio_stream.active:
    app.audio_stream.stop()
app.audio_chunks.append(np.zeros((16000, 1), dtype=np.float32))

# Injeta fala sustentada por 30 frames (30 * 0.02 = 0.6s de fala contínua)
for _ in range(30):
    app.current_rms = 0.035
    app.last_frame_time = time.perf_counter() - 0.02
    app._animation_tick()

assert app.has_spoken, "Falha: has_spoken deve ser True"
assert app.accumulated_speech_s >= 0.5, (
    f"Fala sustentada esperava >= 0.5s, obtido {app.accumulated_speech_s:.2f}s"
)
print(f"   -> Fala sustentada confirmada: accumulated = {app.accumulated_speech_s:.2f}s")

# Injeta silêncio pós-fala (RMS = 0.002)
app.current_rms = 0.002
app.smoothed_rms = 0.002

# Simula 1.0s de silêncio pós-fala
app.last_speech_time = time.time() - 1.0
app.last_frame_time = time.perf_counter() - 0.02
app._animation_tick()

assert app.is_silence_countdown, "Falha: is_silence_countdown deve ser True após 1s de silêncio pós-fala"
print("   -> Contagem de silêncio ativada: is_silence_countdown = True")

# Simula 2.0s de silêncio pós-fala (ainda dentro da tolerância de 3.0s para pausas naturais)
app.last_speech_time = time.time() - 2.0
app.last_frame_time = time.perf_counter() - 0.02
app._animation_tick()
assert app.state == "recording", "Gravação deve continuar ativa aos 2.0s com timeout de 3.0s"

# Simula 3.1s de silêncio pós-fala (superando o SILENCE_TIMEOUT_S = 3.0s)
app.last_speech_time = time.time() - 3.1
app.last_frame_time = time.perf_counter() - 0.02
app._animation_tick()

assert app.state in ("processing", "idle"), f"Esperado state='processing' ou 'idle', obtido '{app.state}'"
assert app.audio_stream is None, f"Falha: audio_stream deveria ter sido liberado após auto-stop: {app.audio_stream}"
print("   -> Auto-Stop disparado com sucesso após fala sustentada! stream liberado.")

app.destroy()
print("\nTODOS OS TESTES DE AUTO-STOP E SENSIBILIDADE VAD PASSARAM COM SUCESSO! [PASS]")
