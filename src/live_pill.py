"""Pílula Flutuante Avulsa para ditado de voz e geração instantânea de prompts (Live Pill).

Design:
- Formato pílula (cápsula) 100% arredondada com cantos verdadeiros via máscara de transparência.
- Efeito translúcido (frosted glass) com alpha 0.92.
- Microcopy humanizado e profissional (sem jargões de IA como 'Whisper + Ollama').
- Expande suavemente após o processamento para exibir a transcrição e o pedido gerado.
- Cópia automática para a área de transferência do Windows (Ctrl + V).
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

# Injeta DLLs da NVIDIA no PATH no Windows para garantir aceleração CUDA
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
KEY_COLOR = "#000001"  # Cor chave para transparência dos cantos no Windows


def draw_smooth_round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float = 22,
    **kwargs,
) -> int:
    """Desenha um retângulo arredondado ultra suave usando spline Bézier."""
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class LivePillApp:
    """Widget de pílula translúcida e arredondada para ditado de prompts."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.is_recording = False
        self.is_processing = False
        self.is_expanded = False
        self.audio_chunks = []
        self.audio_stream: Optional[sd.InputStream] = None
        self.whisper_model: Optional[WhisperModel] = None
        self.record_start_time = 0.0

        # Coordenadas para arraste
        self.start_x = 0
        self.start_y = 0
        self.win_x = 0
        self.win_y = 0
        self.has_dragged = False

        self.last_raw_text = ""
        self.last_prompt_text = ""

        # Paleta de cores moderna (Zinc / Glassmorphism)
        self.theme = {
            "idle_bg": "#121217",
            "idle_border": "#27272a",
            "idle_text": "#f4f4f5",
            "recording_bg": "#2a0f12",
            "recording_border": "#ef4444",
            "recording_text": "#fca5a5",
            "processing_bg": "#0c1b2e",
            "processing_border": "#0284c7",
            "processing_text": "#7dd3fc",
            "success_bg": "#06281e",
            "success_border": "#10b981",
            "success_text": "#6ee7b7",
        }

        self.ui_queue = queue.Queue()

        self._configure_window()
        self._build_ui()
        self._load_whisper_in_background()
        self._start_queue_listener()

    def _configure_window(self) -> None:
        """Configura a janela como sem bordas, Always-on-Top e translúcida."""
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=KEY_COLOR)
        self.root.attributes("-transparentcolor", KEY_COLOR)
        self.root.attributes("-alpha", 0.93)  # Efeito translúcido frosted glass

        self.screen_width = self.root.winfo_screenwidth()
        self.screen_height = self.root.winfo_screenheight()

    def _build_ui(self) -> None:
        """Monta a estrutura visual base com Canvas transparente."""
        self.container = tk.Frame(self.root, bg=KEY_COLOR)
        self.container.pack(fill="both", expand=True)

        # Canvas para desenho da pílula arredondada
        self.pill_canvas = tk.Canvas(
            self.container,
            bg=KEY_COLOR,
            highlightthickness=0,
            cursor="hand2",
        )
        self.pill_canvas.pack(fill="both", expand=True)

        self._bind_mouse(self.pill_canvas)

        # Painel expandido de resultados (oculto por padrão)
        self.results_frame = tk.Frame(
            self.container,
            bg="#121217",
            highlightthickness=1,
            highlightbackground="#27272a",
            padx=16,
            pady=12,
        )

        self._build_expanded_results()
        self._show_compact_pill()

    def _build_expanded_results(self) -> None:
        """Cria os elementos da visão expandida de transcrição e prompt."""
        # Cabeçalho com status e fechar
        header = tk.Frame(self.results_frame, bg="#121217")
        header.pack(fill="x", pady=(0, 8))

        self.badge_status = tk.Label(
            header,
            text="✓ Copiado para o Ctrl+V",
            font=("Segoe UI", 9, "bold"),
            bg="#064e3b",
            fg="#34d399",
            padx=8,
            pady=2,
        )
        self.badge_status.pack(side="left")

        close_lbl = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 10),
            bg="#121217",
            fg="#71717a",
            cursor="hand2",
            padx=4,
        )
        close_lbl.pack(side="right")
        close_lbl.bind("<Button-1>", lambda e: self.root.destroy())
        close_lbl.bind("<Enter>", lambda e: close_lbl.config(fg="#ef4444"))
        close_lbl.bind("<Leave>", lambda e: close_lbl.config(fg="#71717a"))

        # Transcrição da Fala
        tk.Label(
            self.results_frame,
            text="O QUE VOCÊ DISSE:",
            font=("Segoe UI", 8, "bold"),
            bg="#121217",
            fg="#71717a",
            anchor="w",
        ).pack(fill="x", pady=(4, 2))

        self.raw_text_box = tk.Text(
            self.results_frame,
            height=3,
            wrap="word",
            font=("Segoe UI", 9),
            bg="#18181b",
            fg="#d4d4d8",
            relief="flat",
            padx=10,
            pady=6,
            highlightthickness=1,
            highlightbackground="#27272a",
        )
        self.raw_text_box.pack(fill="x", pady=(0, 8))

        # Pedido Refinado
        tk.Label(
            self.results_frame,
            text="PEDIDO PRONTO PARA IA:",
            font=("Segoe UI", 8, "bold"),
            bg="#121217",
            fg="#34d399",
            anchor="w",
        ).pack(fill="x", pady=(2, 2))

        self.prompt_text_box = tk.Text(
            self.results_frame,
            height=4,
            wrap="word",
            font=("Segoe UI", 9, "bold"),
            bg="#0d241c",
            fg="#ecfdf5",
            relief="flat",
            padx=10,
            pady=6,
            highlightthickness=1,
            highlightbackground="#065f46",
        )
        self.prompt_text_box.pack(fill="x", pady=(0, 10))

        # Barra de Ações
        actions = tk.Frame(self.results_frame, bg="#121217")
        actions.pack(fill="x")

        self.btn_new = tk.Button(
            actions,
            text="🎙️ Falar Novamente",
            font=("Segoe UI", 8, "bold"),
            bg="#27272a",
            fg="#f4f4f5",
            activebackground="#3f3f46",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=5,
            cursor="hand2",
            command=self._on_new_record_click,
        )
        self.btn_new.pack(side="left")

        self.btn_copy = tk.Button(
            actions,
            text="📋 Copiar Novamente",
            font=("Segoe UI", 8),
            bg="#18181b",
            fg="#a1a1aa",
            activebackground="#27272a",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            command=self._on_copy_again_click,
        )
        self.btn_copy.pack(side="left", padx=8)

        self.btn_collapse = tk.Button(
            actions,
            text="Recolher",
            font=("Segoe UI", 8),
            bg="#121217",
            fg="#71717a",
            relief="flat",
            padx=8,
            pady=5,
            cursor="hand2",
            command=self._show_compact_pill,
        )
        self.btn_collapse.pack(side="right")

    def _show_compact_pill(self, text: str = "🎙️  Falar comando", state_type: str = "idle") -> None:
        """Exibe a pílula compacta de formato cápsula perfeitamente arredondada."""
        self.is_expanded = False
        self.results_frame.pack_forget()
        self.pill_canvas.pack(fill="both", expand=True)

        width = 330
        height = 48
        x = (self.screen_width - width) // 2
        y = self.screen_height - height - 60
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.pill_canvas.delete("all")

        # Seleciona as cores do estado atual
        bg_col = self.theme.get(f"{state_type}_bg", self.theme["idle_bg"])
        border_col = self.theme.get(f"{state_type}_border", self.theme["idle_border"])
        text_col = self.theme.get(f"{state_type}_text", self.theme["idle_text"])

        # Desenha a cápsula perfeitamente arredondada (radius = 22px para height 46px)
        draw_smooth_round_rect(
            self.pill_canvas,
            x1=2,
            y1=2,
            x2=width - 2,
            y2=height - 2,
            radius=22,
            fill=bg_col,
            outline=border_col,
            width=1.5,
        )

        # Texto centralizado e legível
        self.canvas_text = self.pill_canvas.create_text(
            width // 2 - 12,
            height // 2,
            text=text,
            fill=text_col,
            font=("Segoe UI", 10, "bold"),
        )

        # Botão sutil de fechar (✕)
        self.canvas_close = self.pill_canvas.create_text(
            width - 22,
            height // 2,
            text="✕",
            fill="#52525b",
            font=("Segoe UI", 9),
        )

    def _show_expanded_results(self, raw_text: str, prompt_text: str) -> None:
        """Exibe o painel expandido translúcido com transcrição e prompt prontos."""
        self.is_expanded = True
        self.pill_canvas.pack_forget()

        width = 500
        height = 290
        x = (self.screen_width - width) // 2
        y = self.screen_height - height - 60
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.last_raw_text = raw_text
        self.last_prompt_text = prompt_text

        self.raw_text_box.delete("1.0", tk.END)
        self.raw_text_box.insert("1.0", raw_text)

        self.prompt_text_box.delete("1.0", tk.END)
        self.prompt_text_box.insert("1.0", prompt_text)

        self.results_frame.pack(fill="both", expand=True)

    def _bind_mouse(self, widget: tk.Widget) -> None:
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
        if (dx * dx + dy * dy) > 16:
            self.has_dragged = True
            self.root.geometry(f"+{self.win_x + dx}+{self.win_y + dy}")

    def _on_mouse_up(self, event: tk.Event) -> None:
        if self.has_dragged:
            return

        # Verifica se clicou próximo ao botão de fechar (canto direito)
        if event.x > 300:
            self.root.destroy()
            return

        self._toggle_recording()

    def _toggle_recording(self) -> None:
        if self.is_processing:
            return

        if not self.is_recording:
            self._start_audio_capture()
        else:
            self._stop_audio_capture()

    def _start_audio_capture(self) -> None:
        self.audio_chunks = []
        self.is_recording = True
        self.record_start_time = time.time()
        self._show_compact_pill("🔴  Ouvindo... (00:00)", state_type="recording")
        self._update_recording_timer()

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
            logger.info("Microfone ativado.")
        except Exception as error:
            logger.error(f"Erro no microfone: {error}")
            self._show_compact_pill("⚠️  Microfone indisponível", state_type="idle")
            self.root.after(2500, lambda: self._show_compact_pill("🎙️  Falar comando"))

    def _update_recording_timer(self) -> None:
        if self.is_recording:
            elapsed = int(time.time() - self.record_start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            self._show_compact_pill(
                f"🔴  Ouvindo... ({mins:02d}:{secs:02d})", state_type="recording"
            )
            self.root.after(500, self._update_recording_timer)

    def _stop_audio_capture(self) -> None:
        self.is_recording = False
        if self.audio_stream:
            self.audio_stream.stop()
            self.audio_stream.close()
            self.audio_stream = None

        if not self.audio_chunks:
            self._show_compact_pill("🎙️  Falar comando")
            return

        self.is_processing = True
        self._show_compact_pill("✨  Estruturando pedido...", state_type="processing")

        recorded_audio = np.concatenate(self.audio_chunks, axis=0).flatten()
        duration_s = len(recorded_audio) / SAMPLE_RATE

        if duration_s < 0.6:
            self.is_processing = False
            self._show_compact_pill("⚠️  Fala muito curta", state_type="idle")
            self.root.after(2000, lambda: self._show_compact_pill("🎙️  Falar comando"))
            return

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

            logger.info("Transcrevendo fala...")
            segments, info = self.whisper_model.transcribe(
                audio_data, language="pt", beam_size=5
            )
            raw_text = " ".join(seg.text.strip() for seg in segments).strip()

            if not raw_text:
                self.ui_queue.put(("error", "Nenhuma fala detectada"))
                return

            logger.info("Reescrevendo com modelo de linguagem local...")
            llm_output = call_ollama(
                text=raw_text,
                model=OLLAMA_MODEL,
                temperature=OLLAMA_TEMPERATURE,
            )
            pedido, duvidas = parse_llm_response(llm_output)

            # Copia automaticamente para o clipboard do Windows
            pyperclip.copy(pedido)
            logger.info("Prompt pronto copiado para a área de transferência!")

            self.ui_queue.put(("success", (raw_text, pedido)))

        except Exception as error:
            logger.error(f"Erro no processamento: {error}")
            self.ui_queue.put(("error", "Falha ao processar"))

    def _start_queue_listener(self) -> None:
        try:
            while not self.ui_queue.empty():
                msg_type, payload = self.ui_queue.get_nowait()
                self.is_processing = False
                if msg_type == "success":
                    raw_text, prompt_text = payload
                    self._show_expanded_results(raw_text, prompt_text)
                elif msg_type == "error":
                    self._show_compact_pill(f"⚠️  {payload}", state_type="idle")
                    self.root.after(2500, lambda: self._show_compact_pill("🎙️  Falar comando"))
        except Exception:
            pass

        self.root.after(100, self._start_queue_listener)

    def _on_new_record_click(self) -> None:
        self._show_compact_pill("🎙️  Falar comando")
        self._toggle_recording()

    def _on_copy_again_click(self) -> None:
        if self.last_prompt_text:
            pyperclip.copy(self.last_prompt_text)
            self.badge_status.config(text="✓ Recopiado com sucesso!")
            self.root.after(2000, lambda: self.badge_status.config(text="✓ Copiado para o Ctrl+V"))

    def _load_whisper_in_background(self) -> None:
        def load():
            try:
                self.whisper_model = WhisperModel(
                    WHISPER_MODEL,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                )
                logger.info("Modelo pronto na GPU.")
            except Exception as e:
                logger.warning(f"Aviso no carregamento do Whisper: {e}")

        threading.Thread(target=load, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    app = LivePillApp(root)
    print("\n>>> Pílula Translúcida Ativa!")
    print("    - Formato cápsula 100% arredondado e translúcido.")
    print("    - Clique para falar, clique para encerrar.")
    print("    - A transcrição e o prompt aparecem na tela e vão direto para o Ctrl+V.\n")
    root.mainloop()


if __name__ == "__main__":
    main()
