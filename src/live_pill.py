"""Pílula Flutuante Avulsa para ditado de voz e geração instantânea de prompts (Live Pill).

Funcionalidades:
1. Widget flutuante Always-on-Top sem bordas, reposicionável na tela.
2. Clique 100% responsivo em qualquer parte da pílula para iniciar e parar.
3. Exibição da Transcrição Original e do Prompt Reescrito na tela ao finalizar.
4. Captura de microfone em memória via sounddevice (16kHz mono).
5. Transcrição na GPU via faster-whisper e reescrita pelo Ollama (qwen2.5:7b).
6. Cópia automática para a área de transferência do Windows (Ctrl + V).
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

SAMPLE_RATE = 16000


class LivePillApp:
    """Janela flutuante Always-on-Top que expande para mostrar transcricao e prompt."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.is_recording = False
        self.is_processing = False
        self.audio_chunks = []
        self.audio_stream: Optional[sd.InputStream] = None
        self.whisper_model: Optional[WhisperModel] = None
        self.record_start_time = 0.0

        # Variaveis de arraste
        self.start_x = 0
        self.start_y = 0
        self.win_x = 0
        self.win_y = 0
        self.has_dragged = False

        self.last_raw_text = ""
        self.last_prompt_text = ""

        self.ui_queue = queue.Queue()

        self._configure_window()
        self._build_ui()
        self._load_whisper_in_background()
        self._start_queue_listener()

    def _configure_window(self) -> None:
        """Configura a janela como sem bordas, Always-on-Top e centralizada embaixo."""
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg="#0f172a")

        self.screen_width = self.root.winfo_screenwidth()
        self.screen_height = self.root.winfo_screenheight()

        self._resize_to_compact()

    def _resize_to_compact(self) -> None:
        """Define o tamanho compacto de pilula (360x54)."""
        width = 360
        height = 54
        x = (self.screen_width - width) // 2
        y = self.screen_height - height - 65
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.results_frame.pack_forget()

    def _resize_to_expanded(self) -> None:
        """Expande a janela para exibir a transcricao e o prompt gerado (520x290)."""
        width = 520
        height = 290
        x = (self.screen_width - width) // 2
        y = self.screen_height - height - 65
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.results_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    def _bind_all_mouse(self, widget: tk.Widget) -> None:
        """Vincula eventos de mouse recursivamente garantindo clique confiavel em qualquer elemento."""
        widget.bind("<Button-1>", self._on_mouse_down)
        widget.bind("<B1-Motion>", self._on_mouse_move)
        widget.bind("<ButtonRelease-1>", self._on_mouse_up)

    def _on_mouse_down(self, event: tk.Event) -> None:
        self.start_x = event.x_root
        self.start_y = event.y_root
        self.win_x = self.root.winfo_x()
        self.win_y = self.root.winfo_y()
        self.has_dragged = False

    def _on_mouse_move(self, event: tk.Event) -> None:
        dx = event.x_root - self.start_x
        dy = event.y_root - self.start_y
        if (dx * dx + dy * dy) > 16:  # Deslocou mais de 4 pixels: considera arraste
            self.has_dragged = True
            self.root.geometry(f"+{self.win_x + dx}+{self.win_y + dy}")

    def _on_mouse_up(self, event: tk.Event) -> None:
        # Se nao foi arraste, dispara a acao de alternar gravacao
        if not self.has_dragged:
            self._toggle_recording()

    def _build_ui(self) -> None:
        """Cria os componentes visuais da pilula e do painel expandido."""
        # 1. Barra Principal (Pilula Compacta)
        self.pill_bar = tk.Frame(
            self.root,
            bg="#1e293b",
            highlightthickness=1,
            highlightbackground="#475569",
            cursor="hand2",
            padx=12,
            pady=10,
        )
        self.pill_bar.pack(fill="x", side="top")
        self._bind_all_mouse(self.pill_bar)

        self.status_icon = tk.Label(
            self.pill_bar,
            text="🎙️",
            font=("Segoe UI Emoji", 13),
            bg="#1e293b",
            fg="#94a3b8",
            cursor="hand2",
        )
        self.status_icon.pack(side="left", padx=(4, 8))
        self._bind_all_mouse(self.status_icon)

        self.status_text = tk.Label(
            self.pill_bar,
            text="Falar Prompt (Clique aqui)",
            font=("Segoe UI", 10, "bold"),
            bg="#1e293b",
            fg="#f1f5f9",
            cursor="hand2",
        )
        self.status_text.pack(side="left", fill="x", expand=True)
        self._bind_all_mouse(self.status_text)

        # Botao discreto para fechar
        self.close_btn = tk.Label(
            self.pill_bar,
            text="✕",
            font=("Segoe UI", 10),
            bg="#1e293b",
            fg="#64748b",
            cursor="arrow",
            padx=6,
        )
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda e: self.root.destroy())
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.config(fg="#ef4444"))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.config(fg="#64748b"))

        # 2. Painel Expandido com a Transcricao e o Prompt Reescrito
        self.results_frame = tk.Frame(self.root, bg="#0f172a")

        # Secao da Transcricao Crua
        tk.Label(
            self.results_frame,
            text="🗣️ TRANSCRIÇÃO DA FALA:",
            font=("Segoe UI", 8, "bold"),
            bg="#0f172a",
            fg="#94a3b8",
            anchor="w",
        ).pack(fill="x", pady=(8, 2))

        self.raw_text_box = tk.Text(
            self.results_frame,
            height=3,
            wrap="word",
            font=("Segoe UI", 9),
            bg="#1e293b",
            fg="#cbd5e1",
            relief="flat",
            padx=8,
            pady=6,
            highlightthickness=1,
            highlightbackground="#334155",
        )
        self.raw_text_box.pack(fill="x", pady=(0, 6))

        # Secao do Prompt Refinado
        tk.Label(
            self.results_frame,
            text="✨ PEDIDO PRONTO PARA IA (Copiado para o Ctrl+V):",
            font=("Segoe UI", 8, "bold"),
            bg="#0f172a",
            fg="#34d399",
            anchor="w",
        ).pack(fill="x", pady=(2, 2))

        self.prompt_text_box = tk.Text(
            self.results_frame,
            height=4,
            wrap="word",
            font=("Segoe UI", 9, "bold"),
            bg="#064e3b",
            fg="#ecfdf5",
            relief="flat",
            padx=8,
            pady=6,
            highlightthickness=1,
            highlightbackground="#059669",
        )
        self.prompt_text_box.pack(fill="x", pady=(0, 8))

        # Botoes de acao rapida
        btn_bar = tk.Frame(self.results_frame, bg="#0f172a")
        btn_bar.pack(fill="x")

        self.new_record_btn = tk.Button(
            btn_bar,
            text="🎙️ Novo Ditado",
            font=("Segoe UI", 8, "bold"),
            bg="#2563eb",
            fg="white",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._on_new_record_click,
        )
        self.new_record_btn.pack(side="left")

        self.copy_again_btn = tk.Button(
            btn_bar,
            text="📋 Copiar Novamente",
            font=("Segoe UI", 8),
            bg="#334155",
            fg="#f8fafc",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._on_copy_again_click,
        )
        self.copy_again_btn.pack(side="left", padx=8)

        self.minimize_btn = tk.Button(
            btn_bar,
            text="Recolher",
            font=("Segoe UI", 8),
            bg="#1e293b",
            fg="#94a3b8",
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
            command=self._resize_to_compact,
        )
        self.minimize_btn.pack(side="right")

    def _set_colors(self, bg_color: str, border_color: str, icon_color: str, text_color: str) -> None:
        """Aplica cores dinamicamente na barra da pilula."""
        self.pill_bar.config(bg=bg_color, highlightbackground=border_color)
        self.status_icon.config(bg=bg_color, fg=icon_color)
        self.status_text.config(bg=bg_color, fg=text_color)
        self.close_btn.config(bg=bg_color)

    def _set_state_idle(self, message: str = "Falar Prompt (Clique aqui)") -> None:
        self.is_recording = False
        self.is_processing = False
        self._set_colors("#1e293b", "#475569", "#94a3b8", "#f1f5f9")
        self.status_icon.config(text="🎙️")
        self.status_text.config(text=message)

    def _set_state_recording(self) -> None:
        self.is_recording = True
        self.is_processing = False
        self._resize_to_compact()
        self._set_colors("#2d1215", "#ef4444", "#ef4444", "#fca5a5")
        self.status_icon.config(text="🔴")
        self.status_text.config(text="Ouvindo... (Clique p/ parar)")
        self.record_start_time = time.time()
        self._update_recording_timer()

    def _update_recording_timer(self) -> None:
        if self.is_recording:
            elapsed = int(time.time() - self.record_start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            self.status_text.config(
                text=f"Ouvindo ({mins:02d}:{secs:02d}) - Clique p/ parar"
            )
            self.root.after(500, self._update_recording_timer)

    def _set_state_processing(self) -> None:
        self.is_recording = False
        self.is_processing = True
        self._set_colors("#0c2d48", "#38bdf8", "#38bdf8", "#bae6fd")
        self.status_icon.config(text="⚡")
        self.status_text.config(text="Whisper + Ollama refinando...")

    def _set_state_success(self, raw_text: str, prompt_text: str) -> None:
        """Exibe os resultados completos na tela e atualiza a barra."""
        self.is_recording = False
        self.is_processing = False
        self.last_raw_text = raw_text
        self.last_prompt_text = prompt_text

        self._set_colors("#064e3b", "#34d399", "#34d399", "#6ee7b7")
        self.status_icon.config(text="✓")
        self.status_text.config(text="Prompt Pronto e Copiado! (Ctrl+V)")

        # Preenche os campos de texto
        self.raw_text_box.delete("1.0", tk.END)
        self.raw_text_box.insert("1.0", raw_text)

        self.prompt_text_box.delete("1.0", tk.END)
        self.prompt_text_box.insert("1.0", prompt_text)

        # Expande a janela para exibir a transcricao e o prompt
        self._resize_to_expanded()

    def _on_new_record_click(self) -> None:
        self._set_state_idle()
        self._toggle_recording()

    def _on_copy_again_click(self) -> None:
        if self.last_prompt_text:
            pyperclip.copy(self.last_prompt_text)
            self.status_text.config(text="✓ Recopiado com sucesso!")

    def _toggle_recording(self) -> None:
        if self.is_processing:
            return

        if not self.is_recording:
            self._start_audio_capture()
        else:
            self._stop_audio_capture()

    def _start_audio_capture(self) -> None:
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
            logger.info("Gravando audio do microfone...")
        except Exception as error:
            logger.error(f"Falha ao iniciar microfone: {error}")
            self._set_state_idle("Erro no Microfone!")
            self.root.after(2500, self._set_state_idle)

    def _stop_audio_capture(self) -> None:
        if self.audio_stream:
            self.audio_stream.stop()
            self.audio_stream.close()
            self.audio_stream = None

        if not self.audio_chunks:
            self._set_state_idle()
            return

        self._set_state_processing()

        # Concatena os chunks da RAM em um unico array numpy
        recorded_audio = np.concatenate(self.audio_chunks, axis=0).flatten()
        duration_s = len(recorded_audio) / SAMPLE_RATE

        logger.info(f"Gravacao finalizada com {duration_s:.1f} segundos.")

        if duration_s < 0.6:
            logger.warning("Gravacao muito curta descartada.")
            self._set_state_idle("Fala muito curta!")
            self.root.after(2000, self._set_state_idle)
            return

        # Executa Whisper e Ollama em segundo plano
        threading.Thread(
            target=self._process_audio_worker,
            args=(recorded_audio,),
            daemon=True,
        ).start()

    def _process_audio_worker(self, audio_data: np.ndarray) -> None:
        try:
            if self.whisper_model is None:
                self.whisper_model = WhisperModel(
                    WHISPER_MODEL,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                )

            logger.info("Transcrevendo fala com Faster-Whisper...")
            segments, info = self.whisper_model.transcribe(
                audio_data, language="pt", beam_size=5
            )
            raw_text = " ".join(seg.text.strip() for seg in segments).strip()

            if not raw_text:
                logger.warning("Nenhum som compreendido.")
                self.ui_queue.put(("error", "Nenhuma fala detectada"))
                return

            logger.info(f"Transcricao gerada: \"{raw_text}\"")

            logger.info(f"Chamando Ollama ({OLLAMA_MODEL})...")
            llm_output = call_ollama(
                text=raw_text,
                model=OLLAMA_MODEL,
                temperature=OLLAMA_TEMPERATURE,
            )
            pedido, duvidas = parse_llm_response(llm_output)

            # Copia automaticamente para a area de transferencia
            pyperclip.copy(pedido)
            logger.info("Prompt copiado para a area de transferencia!")

            # Envia tanto o texto cru quanto o pedido para a interface
            self.ui_queue.put(("success", (raw_text, pedido)))

        except Exception as error:
            logger.error(f"Erro no processamento da pilula: {error}")
            self.ui_queue.put(("error", "Falha ao processar"))

    def _start_queue_listener(self) -> None:
        try:
            while not self.ui_queue.empty():
                msg_type, payload = self.ui_queue.get_nowait()
                if msg_type == "success":
                    raw_text, prompt_text = payload
                    self._set_state_success(raw_text, prompt_text)
                elif msg_type == "error":
                    self._set_state_idle(payload)
                    self.root.after(2500, self._set_state_idle)
        except Exception:
            pass

        self.root.after(100, self._start_queue_listener)

    def _load_whisper_in_background(self) -> None:
        def load():
            try:
                logger.info("Aquecendo Whisper na GPU em segundo plano...")
                self.whisper_model = WhisperModel(
                    WHISPER_MODEL,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                )
                logger.info("Whisper pronto!")
            except Exception as e:
                logger.warning(f"Aviso no carregamento do Whisper: {e}")

        threading.Thread(target=load, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    app = LivePillApp(root)
    print("\n>>> Pílula Flutuante Ativa na sua tela!")
    print("    - Clique em QUALQUER parte da pílula para falar.")
    print("    - Clique novamente para encerrar.")
    print("    - A transcrição e o prompt aparecem na tela e são copiados para o Ctrl+V.")
    print("    - Arraste a pílula para onde preferir.")
    print("    - Clique no '✕' para encerrar.\n")
    root.mainloop()


if __name__ == "__main__":
    main()
