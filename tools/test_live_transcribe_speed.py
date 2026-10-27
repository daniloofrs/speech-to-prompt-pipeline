"""Verifica a reatividade imediata da transcrição ao vivo e atualização no Canvas."""
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tkinter as tk
from src.live_pill import LivePillApp

def test_live_transcription_speed():
    root = tk.Tk()
    root.withdraw()
    app = LivePillApp(root)
    app.models_ready = True
    
    # 1. Transita para recording
    app._transition_to("recording")
    app._render_canvas()
    
    assert app.live_text_id is not None, "live_text_id deve estar registrado no canvas"
    initial_text = app.canvas.itemcget(app.live_text_id, "text")
    assert "Fale naturalmente" in initial_text, f"Texto inicial incorreto: {initial_text}"
    
    # 2. Simula chegada de live_update
    sample_text = "Crie uma função em Python"
    app.ui_queue.put(("live_update", sample_text))
    
    # Processa fila da UI
    app._start_queue_listener()
    
    updated_text = app.canvas.itemcget(app.live_text_id, "text")
    assert updated_text == sample_text, f"Texto não foi atualizado imediatamente no canvas! Obtido: '{updated_text}'"
    print(f"[PASS] Atualização imediata do Canvas validada: '{updated_text}'")

    # 3. Testa sincronização contínua no tick de 60 FPS
    second_phrase = "Crie uma função em Python para calcular média móvel"
    app.live_transcript = second_phrase
    app._fast_update_recording_bars()
    
    synced_text = app.canvas.itemcget(app.live_text_id, "text")
    assert synced_text == second_phrase, f"Sync de 60 FPS falhou! Obtido: '{synced_text}'"
    print(f"[PASS] Sincronização a 60 FPS validada: '{synced_text}'")

    app.destroy()
    print("\nTESTE DE VELOCIDADE E SINCRONIZAÇÃO EM TEMPO REAL: 100% SUCESSO! [PASS]")

if __name__ == "__main__":
    test_live_transcription_speed()
