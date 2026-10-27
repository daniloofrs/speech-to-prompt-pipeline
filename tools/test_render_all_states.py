"""Testa a renderização de todos os estados visuais da Apple Liquid Glass (Clear Water)."""
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tkinter as tk
from src.live_pill import LivePillApp

def test_render_all_states():
    root = tk.Tk()
    root.withdraw()
    app = LivePillApp(root)
    
    # 1. Idle state
    app.models_ready = True
    app._transition_to("idle")
    app._render_canvas()
    print("[PASS] Idle state rendered")

    # 2. Recording state
    app.record_start_time = time.time()
    app.live_transcript = "Criar uma função em python para calcular média móvel"
    app._transition_to("recording")
    app._render_canvas()
    app._fast_update_recording_bars()
    print("[PASS] Recording state rendered & fast updated")

    # 3. Processing state
    app._transition_to("processing")
    app._render_canvas()
    app._fast_update_processing_spinner()
    print("[PASS] Processing state rendered & spinner pulsed")

    # 4. Result state
    app.last_raw_text = "cria uma função em python pra calcular média móvel"
    app.last_prompt_text = "Crie uma função em Python para calcular a média móvel de uma série temporal usando pandas."
    app._transition_to("result")
    app._render_canvas()
    print("[PASS] Result state rendered with hero card and action buttons")

    # 5. Toast state
    app._show_toast("Teste de aviso translúcido")
    app._render_canvas()
    print("[PASS] Toast state rendered")

    app.destroy()
    print("\nTODOS OS ESTADOS VISUAIS RENDERIZADOS COM SUCESSO! [PASS]")

if __name__ == "__main__":
    test_render_all_states()
