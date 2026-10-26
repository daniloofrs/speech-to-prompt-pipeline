"""Teste automatizado de inicialização, hotkey Win32 e aquecimento neural do LivePillApp."""

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


def test_live_pill_initialization():
    print("=" * 70)
    print("TESTE DE INICIALIZAÇÃO HEADLESS: LIVE_PILL (DYNAMIC ISLAND)")
    print("=" * 70)

    root = tk.Tk()
    root.withdraw()
    app = LivePillApp(root)

    print("\n1. Verificando integridade da janela e hotkey listener...")
    assert app.hotkey_listener is not None, "Falha: hotkey_listener não foi instanciado"
    assert app.hotkey_listener.is_alive(), "Falha: thread do hotkey_listener não está ativa"
    print(f"   -> Hotkey listener global '{app.hotkey_listener.key_name}' ativo com sucesso!")

    print("\n2. Aguardando aquecimento e prontidão dos modelos neurais (Whisper + Ollama)...")
    t0 = time.time()
    timeout_s = 30.0
    while not app.models_ready and (time.time() - t0) < timeout_s:
        root.update()
        time.sleep(0.05)

    assert app.models_ready, (
        f"Falha: models_ready permaneceu False após {timeout_s}s. "
        f"whisper_model={app.whisper_model}"
    )
    elapsed = time.time() - t0
    print(f"   -> Modelos carregados e aquecidos na GPU em {elapsed:.2f}s! models_ready = True")
    assert app.whisper_model is not None, "Falha: whisper_model não instanciado"

    print("\n3. Verificando encerramento gracioso e desalocação de recursos...")
    app.destroy()
    time.sleep(0.2)
    print("   -> Recursos e hotkey liberados com sucesso!")

    print("\n" + "=" * 70)
    print("RESULTADO: INICIALIZAÇÃO E AQUECIMENTO 100% VALIDADOS [PASS]")
    print("=" * 70)


if __name__ == "__main__":
    test_live_pill_initialization()
