"""Pílula Flutuante Avulsa para ditado de voz e geração instantânea de prompts (Live Pill).

Funcionalidades:
1. Widget flutuante Always-on-Top sem bordas, reposicionável na tela.
2. Captura de microfone 100% em memória usando sounddevice (16kHz mono).
3. Transcrição local na GPU via faster-whisper (CUDA float16).
4. Reescrita com LLM local via Ollama (qwen2.5:7b, temperature 0.1).
5. Cópia automática para a área de transferência do Windows (Ctrl + V).
6. Totalmente avulso: funciona sem necessidade do Docker ou PostgreSQL.
"""

import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

# Garante que a raiz do projeto esteja no sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Injeta DLLs da NVIDIA no PATH no Windows para garantir aceleracao CUDA
if sys.platform == "win32":
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    for nvidia_bin in site_packages.glob("nvidia/**/bin"):
        if nvidia_bin.is_dir():
            os.environ["PATH"] = str(nvidia_bin) + os.pathsep + os.environ["PATH"]
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(str(nvidia_bin))
                except OSError:
                    pass

import numpy as np
import pyperclip
import sounddevice as sd
from faster_whisper import WhisperModel

from src.config import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)
from src.rewrite import call_ollama, parse_llm_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] live_pill: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("live_pill")

SAMPLE_RATE = 16000  # Taxa nativa do Whisper


class LivePillApp:
    """Aplicativo de janela flutuante Always-on-Top estilo pílula."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.is_recording = False
        self.is_processing = False
        self.audio_chunks = []
        self.audio_stream: Optional[sd.InputStream] = None
        self.whisper_model: Optional[WhisperModel] = None

        # Fila para comunicacao entre threads e a interface grafica
        self.ui_queue = queue.Queue()

        self._configure_window()
        self._build_ui()
        self._load_whisper_in_background()
        self._start_queue_listener()

    def _configure_window(self) -> None:
        """Configura a janela como sem bordas, flutuante e reposicionavel."""
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg="#0b0f19")

        # Posiciona na parte inferior centralizada da tela (acima da barra de tarefas)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        pill_width = 320
        pill_height = 56
        x = (screen_width - pill_width) // 2
        y = screen_height - pill_height - 70
        self.root.geometry(f"{pill_width}x{pill_height}+{x}+{y}")

        # Suporte a arrastar a janela livremente
        self._drag_start_x = 0
        self._drag_start_y = 0

    def _build_ui(self) -> None:
        """Cria o visual de pilula moderno."""
        self.main_frame = tk.Frame(
            self.root,
            bg="#1e293b",
            highlightthickness=1,
            highlightbackground="#475569",
            padx=12,
            pady=8,
            cursor="hand2",
        )
        self.main_frame.pack(fill="both", expand=True)

        # Eventos de clique e arraste
        self.main_frame.bind("<Button-1>", self._on_frame_click)
        self.main_frame.bind("<B1-Motion>", self._on_frame_drag)
        self.main_frame.bind("<ButtonRelease-1>", self._on_frame_release)

        # Indicador de estado (Icone / Bolinha)
        self.status_icon = tk.Label(
            self.main_frame,
            text="🎙️",
            font=("Segoe UI Emoji", 13),
            bg="#1e293b",
            fg="#94a3b8",
        )
        self.status_icon.pack(side="left", padx=(4, 8))
        self.status_icon.bind("<Button-1>", self._on_frame_click)

        # Texto de acao
        self.status_text = tk.Label(
            self.main_frame,
            text="Falar Prompt (Clique)",
            font=("Segoe UI", 10, "bold"),
            bg="#1e293b",
            fg="#f1f5f9",
        )
        self.status_text.pack(side="left", fill="x", expand=True)
        self.status_text.bind("<Button-1>", self._on_frame_click)

        # Botao discreto para fechar (✕)
        self.close_btn = tk.Label(
            self.main_frame,
            text="✕",
            font=("Segoe UI", 9),
            bg="#1e293b",
            fg="#64748b",
            cursor="arrow",
        )
        self.close_btn.pack(side="right", padx=(8, 2))
        self.close_btn.bind("<Button-1>", lambda e: self.root.destroy())
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.config(fg="#ef4444"))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.config(fg="#64748b"))

    def _on_frame_click(self, event: tk.Event) -> None:
        self._drag_start_x = event.x_root - self.root.winfo_x()
        self._drag_start_y = event.y_root - self.root.winfo_y()
        self._has_dragged = False

    def _on_frame_drag(self, event: tk.Event) -> None:
        self._has_dragged = True
        new_x = event.x_root - self._drag_start_x
        new_y = event.y_root - self._drag_start_y
        self.root.geometry(f"+{new_x}+{new_y}")

    def _on_frame_release(self, event: tk.Event) -> None:
        # Se nao foi um arraste significativo, interpreta como clique de acao
        if not getattr(self, "_has_dragged", False):
            self._toggle_recording()

    def _set_state_idle(self, message: str = "Falar Prompt (Clique)") -> None:
        """Estado em espera (aguardando fala)."""
        self.is_recording = False
        self.is_processing = False
        self.main_frame.config(bg="#1e293b", highlightbackground="#475569")
        self.status_icon.config(text="🎙️", bg="#1e293b", fg="#94a3b8")
        self.status_text.config(text=message, bg="#1e293b", fg="#f1f5f9")
        self.close_btn.config(bg="#1e293b")

    def _set_state_recording(self) -> None:
        """Estado de gravacao ativa."""
        self.is_recording = True
        self.is_processing = False
        self.main_frame.config(bg="#2d1215", highlightbackground="#ef4444")
        self.status_icon.config(text="🔴", bg="#2d1215", fg="#ef4444")
        self.status_text.config(text="Ouvindo... (Clique p/ parar)", bg="#2d1215", fg="#fca5a5")
        self.close_btn.config(bg="#2d1215")

    def _set_state_processing(self) -> None:
        """Estado de processamento (Whisper + Ollama)."""
        self.is_recording = False
        self.is_processing = True
        self.main_frame.config(bg="#0c2d48", highlightbackground="#38bdf8")
        self.status_icon.config(text="⚡", bg="#0c2d48", fg="#38bdf8")
        self.status_text.config(text="Refinando com IA...", bg="#0c2d48", fg="#bae6fd")
        self.close_btn.config(bg="#0c2d48")

    def _set_state_copied(self, prompt_preview: str) -> None:
        """Estado de sucesso: prompt pronto e copiado para a area de transferencia."""
        self.is_recording = False
        self.is_processing = False
        self.main_frame.config(bg="#064e3b", highlightbackground="#34d399")
        self.status_icon.config(text="✓", bg="#064e3b", fg="#34d399")
        
        # Encurta a previa do texto se for longa
        short_preview = (
            prompt_preview[:32] + "..." if len(prompt_preview) > 32 else prompt_preview
        )
        self.status_text.config(
            text=f"Copiado! \"{short_preview}\"", bg="#064e3b", fg="#6ee7b7"
        )
        self.close_btn.config(bg="#064e3b")

        # Volta para o estado inicial apos 4.5 segundos
        self.root.after(4500, self._set_state_idle)

    def _toggle_recording(self) -> None:
        """Alterna entre iniciar e parar a gravacao."""
        if self.is_processing:
            return  # Ignora cliques enquanto a IA esta gerando

        if not self.is_recording:
            self._start_audio_capture()
        else:
            self._stop_audio_capture()

    def _start_audio_capture(self) -> None:
        """Inicia a captura de audio pelo microfone."""
        self.audio_chunks = []
        self._set_state_recording()

        def audio_callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            self.audio_chunks.append(indata.copy())

        try:
            self.audio_stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=audio_callback,
            )
            self.audio_stream.start()
            logger.info("Microfone ativado. Gravando fala em memoria...")
        except Exception as error:
            logger.error(f"Erro ao abrir microfone: {error}")
            self._set_state_idle("Erro no Microfone!")
            self.root.after(3000, self._set_state_idle)

    def _stop_audio_capture(self) -> None:
        """Finaliza a gravacao e dispara o processamento em thread separada."""
        if self.audio_stream:
            self.audio_stream.stop()
            self.audio_stream.close()
            self.audio_stream = None

        if not self.audio_chunks:
            self._set_state_idle()
            return

        self._set_state_processing()

        # Junta os pedacos de audio da memoria em um unico array numpy
        recorded_audio = np.concatenate(self.audio_chunks, axis=0).flatten()
        duration_s = len(recorded_audio) / SAMPLE_RATE

        logger.info(f"Gravacao concluida: {duration_s:.1f} segundos de audio.")

        if duration_s < 0.5:
            logger.warning("Gravacao muito curta descartada.")
            self._set_state_idle("Fala muito curta!")
            self.root.after(2000, self._set_state_idle)
            return

        # Dispara o Whisper e o Ollama em background para nao travar a interface
        threading.Thread(
            target=self._process_audio_worker,
            args=(recorded_audio,),
            daemon=True,
        ).start()

    def _process_audio_worker(self, audio_data: np.ndarray) -> None:
        """Executa a transcricao e reescrita do prompt na GPU local."""
        try:
            # 1. Carrega o modelo Whisper caso ainda nao esteja instanciado
            if self.whisper_model is None:
                self.whisper_model = WhisperModel(
                    WHISPER_MODEL,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                )

            # 2. Transcricao 100% em memoria
            logger.info("Transcrevendo fala com Whisper CUDA...")
            segments, info = self.whisper_model.transcribe(
                audio_data, language="pt", beam_size=5
            )
            raw_text = " ".join(seg.text.strip() for seg in segments).strip()

            if not raw_text:
                logger.warning("Nenhuma fala detectada pelo modelo.")
                self.ui_queue.put(("error", "Nenhuma fala detectada"))
                return

            logger.info(f"Transcricao crua: \"{raw_text}\"")

            # 3. Reescrita com LLM local via Ollama
            logger.info(f"Reescrevendo com Ollama ({OLLAMA_MODEL})...")
            llm_output = call_ollama(
                text=raw_text,
                model=OLLAMA_MODEL,
                temperature=OLLAMA_TEMPERATURE,
            )
            pedido, duvidas = parse_llm_response(llm_output)

            # 4. Copia automaticamente para a area de transferencia do Windows
            pyperclip.copy(pedido)
            logger.info(f"SUCESSO: Prompt copiado para a area de transferencia! ({len(pedido)} chars)")

            # 5. Notifica a UI
            self.ui_queue.put(("success", pedido))

        except Exception as error:
            logger.error(f"Erro no processamento da pilula: {error}")
            self.ui_queue.put(("error", "Falha ao processar"))

    def _start_queue_listener(self) -> None:
        """Verifica periodicamente eventos da thread de background."""
        try:
            while not self.ui_queue.empty():
                msg_type, payload = self.ui_queue.get_nowait()
                if msg_type == "success":
                    self._set_state_copied(payload)
                elif msg_type == "error":
                    self._set_state_idle(payload)
                    self.root.after(3000, self._set_state_idle)
        except Exception:
            pass

        # Checa novamente a cada 100ms
        self.root.after(100, self._start_queue_listener)

    def _load_whisper_in_background(self) -> None:
        """Pre-carrega o modelo Whisper em background para a primeira fala ser instantanea."""
        def load():
            try:
                logger.info("Inicializando modelo Whisper na GPU em segundo plano...")
                self.whisper_model = WhisperModel(
                    WHISPER_MODEL,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                )
                logger.info("Whisper pronto e aquecido na GPU!")
            except Exception as e:
                logger.warning(f"Fallback no pre-carregamento do Whisper: {e}")

        threading.Thread(target=load, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    app = LivePillApp(root)
    print("\n>>> Pílula Flutuante Ativa na sua tela!")
    print("    - Clique na pílula para falar.")
    print("    - Clique novamente para encerrar.")
    print("    - O prompt pronto é copiado direto para o seu Ctrl+V.")
    print("    - Arraste a pílula para onde preferir na tela.")
    print("    - Clique no '✕' para encerrar.\n")
    root.mainloop()


if __name__ == "__main__":
    main()
