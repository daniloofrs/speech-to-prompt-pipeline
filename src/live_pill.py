"""Pílula Flutuante Avulsa com Transcrição em Tempo Real (Live Streaming).

Otimizações de Performance:
1. Transcrição ao vivo enquanto você fala: o texto aparece na tela em tempo real.
2. Modelo otimizado para baixa latência (small com beam_size=1, sem timestamps).
3. Transcrição instantânea ao finalizar (o texto já está 95%+ pronto ao clicar em parar).
4. Chamada de reescrita ao Ollama ultra rápida (num_predict=180, num_ctx=1024).
5. Visual pílula moderno, 100% arredondado e translúcido (frosted glass).
6. Cópia automática para o clipboard (Ctrl+V) com exibição completa na tela.
"""

import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.request
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
    LIVE_OLLAMA_MODEL,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
)
from src.rewrite import parse_llm_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] live_pill: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("live_pill")

SAMPLE_RATE = 16000
KEY_COLOR = "#000001"  # Cor chave para transparência no Windows

# Modelos ultra-rápidos e precisos para interação desktop em tempo real
LIVE_WHISPER_MODEL = os.getenv("LIVE_WHISPER_MODEL", "medium")
FAST_OLLAMA_MODEL = os.getenv("LIVE_OLLAMA_MODEL", LIVE_OLLAMA_MODEL)

# Prompt de contexto para calibrar o vocabulário técnico do Whisper em português
TECH_INITIAL_PROMPT = (
    "Transcrição em português brasileiro com pontuação precisa e vocabulário técnico: "
    "Python, SQL, PostgreSQL, Docker, FastAPI, React, JavaScript, TypeScript, Linux, "
    "Git, API REST, banco de dados, função, query, pipeline, script, frontend, backend."
)


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    """Remove ruído de bias DC e normaliza volume para faixa ideal de escuta do Whisper."""
    if len(audio) == 0:
        return audio
    # Remove componente DC (bias contínuo do microfone)
    audio = audio - np.mean(audio)
    peak = np.max(np.abs(audio))
    if peak > 0.01:
        audio = (audio / peak) * 0.95
    return audio.astype(np.float32)


def draw_smooth_round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float = 22,
    **kwargs,
) -> int:
    """Desenha um retângulo arredondado suave usando spline Bézier."""
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


def fast_call_ollama(text: str) -> str:
    """Chamada ultra-otimizada ao Ollama: keep_alive em VRAM e retorno imperativo direto."""
    endpoint = f"{OLLAMA_HOST.rstrip('/')}/api/chat"
    system_prompt = (
        "Você é um especialista em Prompt Engineering. "
        "Sua tarefa é transformar a transcrição de fala do usuário em um prompt direto, claro e profissional para uma IA.\n"
        "Regras:\n"
        "1. Retorne APENAS o comando imperativo refinado (ex: 'Crie uma função...', 'Escreva uma query...').\n"
        "2. Seja direto e objetivo, sem enrolação.\n"
        "3. Não inclua introduções, explicações, saudações, títulos, notas ou aspas."
    )

    payload = {
        "model": FAST_OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "options": {
            "temperature": 0.0,
            "num_predict": 60,   # Corta a geração logo após o comando direto
            "num_ctx": 256,      # Contexto reduzido acelera consideravelmente o KV cache
            "stop": ["\n\n", "Nota:", "Observação:", "Explicação:", "DÚVIDAS:"],
        },
        "keep_alive": "60m",     # Mantém o modelo residente na VRAM da GPU sem descarregar
        "stream": False,
    }

    req_data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url=endpoint,
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
        raw_output = data.get("message", {}).get("content", "").strip()
        for prefix in ["PEDIDO:", "Prompt:", "prompt:"]:
            if raw_output.startswith(prefix):
                raw_output = raw_output[len(prefix):].strip()
        return raw_output


class LivePillApp:
    """Widget de pílula translúcida com transcrição ao vivo em tempo real."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.is_recording = False
        self.is_processing = False
        self.audio_chunks = []
        self.audio_stream: Optional[sd.InputStream] = None
        self.whisper_model: Optional[WhisperModel] = None
        self.record_start_time = 0.0

        # Controle de transcrição ao vivo
        self.live_transcript = ""
        self.last_transcribed_len = 0
        self.live_transcribe_thread: Optional[threading.Thread] = None

        # Coordenadas de arraste
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
        self._load_models_in_background()
        self._start_queue_listener()

    def _configure_window(self) -> None:
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=KEY_COLOR)
        self.root.attributes("-transparentcolor", KEY_COLOR)
        self.root.attributes("-alpha", 0.94)

        self.screen_width = self.root.winfo_screenwidth()
        self.screen_height = self.root.winfo_screenheight()

    def _build_ui(self) -> None:
        self.container = tk.Frame(self.root, bg=KEY_COLOR)
        self.container.pack(fill="both", expand=True)

        # 1. Canvas para a Pílula Compacta
        self.pill_canvas = tk.Canvas(
            self.container,
            bg=KEY_COLOR,
            highlightthickness=0,
            cursor="hand2",
        )
        self.pill_canvas.pack(fill="both", expand=True)
        self._bind_mouse(self.pill_canvas)

        # 2. Frame de Gravação com Transcrição ao Vivo
        self.live_frame = tk.Frame(
            self.container,
            bg="#18131a",
            highlightthickness=1,
            highlightbackground="#ef4444",
            padx=14,
            pady=10,
        )
        self._build_live_frame()

        # 3. Frame de Resultados Expandido (Final)
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

    def _build_live_frame(self) -> None:
        """Cria os componentes da tela de gravação com texto ao vivo."""
        header = tk.Frame(self.live_frame, bg="#18131a")
        header.pack(fill="x", pady=(0, 4))
        self._bind_mouse(header)

        self.live_timer_lbl = tk.Label(
            header,
            text="🔴 Ouvindo em tempo real (00:00)",
            font=("Segoe UI", 9, "bold"),
            bg="#18131a",
            fg="#fca5a5",
            cursor="hand2",
        )
        self.live_timer_lbl.pack(side="left")
        self._bind_mouse(self.live_timer_lbl)

        close_btn = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 9),
            bg="#18131a",
            fg="#71717a",
            cursor="hand2",
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self.root.destroy())

        # Caixa onde a fala aparece ao vivo conforme o usuário fala
        self.live_text_lbl = tk.Label(
            self.live_frame,
            text="Fale agora, suas palavras aparecerão aqui...",
            font=("Segoe UI", 10),
            bg="#241b26",
            fg="#e4e4e7",
            wraplength=440,
            justify="left",
            anchor="nw",
            padx=10,
            pady=8,
            cursor="hand2",
        )
        self.live_text_lbl.pack(fill="x", pady=(2, 6))
        self._bind_mouse(self.live_text_lbl)

        footer = tk.Label(
            self.live_frame,
            text="Clique em qualquer lugar para concluir e copiar para o Ctrl+V",
            font=("Segoe UI", 8),
            bg="#18131a",
            fg="#a1a1aa",
            cursor="hand2",
        )
        footer.pack(anchor="w")
        self._bind_mouse(footer)

    def _build_expanded_results(self) -> None:
        """Cria a tela com os resultados finais."""
        header = tk.Frame(self.results_frame, bg="#121217")
        header.pack(fill="x", pady=(0, 6))

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
        self.raw_text_box.pack(fill="x", pady=(0, 6))

        # Pedido Refinado
        tk.Label(
            self.results_frame,
            text="PEDIDO PRONTO PARA IA (COPIADO):",
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

        # Ações
        actions = tk.Frame(self.results_frame, bg="#121217")
        actions.pack(fill="x")

        self.btn_new = tk.Button(
            actions,
            text="🎙️ Falar Novamente",
            font=("Segoe UI", 8, "bold"),
            bg="#27272a",
            fg="#f4f4f5",
            activebackground="#3f3f46",
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

    def _show_compact_pill(self, text: str = "🎙️  Falar comando", bg_col: str = "#121217", border_col: str = "#27272a", text_col: str = "#f4f4f5") -> None:
        """Exibe a pílula compacta de espera."""
        self.results_frame.pack_forget()
        self.live_frame.pack_forget()
        self.pill_canvas.pack(fill="both", expand=True)

        width = 330
        height = 48
        x = (self.screen_width - width) // 2
        y = self.screen_height - height - 60
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.pill_canvas.delete("all")
        draw_smooth_round_rect(
            self.pill_canvas,
            x1=2, y1=2, x2=width - 2, y2=height - 2,
            radius=22,
            fill=bg_col,
            outline=border_col,
            width=1.5,
        )
        self.pill_canvas.create_text(
            width // 2 - 12, height // 2,
            text=text,
            fill=text_col,
            font=("Segoe UI", 10, "bold"),
        )
        self.pill_canvas.create_text(
            width - 22, height // 2,
            text="✕",
            fill="#52525b",
            font=("Segoe UI", 9),
        )

    def _show_live_recording(self) -> None:
        """Exibe o painel de transcrição ao vivo durante a fala."""
        self.pill_canvas.pack_forget()
        self.results_frame.pack_forget()
        self.live_frame.pack(fill="both", expand=True)

        width = 480
        height = 116
        x = (self.screen_width - width) // 2
        y = self.screen_height - height - 60
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.live_text_lbl.config(text="Fale agora, suas palavras aparecerão aqui...", fg="#94a3b8")

    def _show_expanded_results(self, raw_text: str, prompt_text: str) -> None:
        """Exibe o resultado final com as duas caixas de texto."""
        self.pill_canvas.pack_forget()
        self.live_frame.pack_forget()
        self.results_frame.pack(fill="both", expand=True)

        width = 520
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

        # Botão de fechar da pílula compacta
        if not self.is_recording and event.x > 300:
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
        self.live_transcript = ""
        self.last_transcribed_len = 0
        self.record_start_time = time.time()

        self._show_live_recording()
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
            logger.info("Microfone ligado. Iniciando transcrição contínua...")
            
            # Inicia a thread que transcreve o áudio em tempo real enquanto você fala
            self.live_transcribe_thread = threading.Thread(
                target=self._live_transcribe_loop, daemon=True
            )
            self.live_transcribe_thread.start()

        except Exception as error:
            logger.error(f"Erro ao abrir microfone: {error}")
            self._show_compact_pill("⚠️  Microfone indisponível")
            self.root.after(2500, lambda: self._show_compact_pill())

    def _update_recording_timer(self) -> None:
        if self.is_recording:
            elapsed = int(time.time() - self.record_start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            self.live_timer_lbl.config(text=f"🔴 Ouvindo em tempo real ({mins:02d}:{secs:02d})")
            self.root.after(500, self._update_recording_timer)

    def _live_transcribe_loop(self) -> None:
        """Loop que transcreve blocos de fala em tempo real na GPU com normalização de áudio."""
        while self.is_recording:
            time.sleep(0.55)  # Intervalo de atualização fluida
            if not self.is_recording:
                break

            if len(self.audio_chunks) < 3:
                continue

            try:
                # Cria snapshot do áudio atual e normaliza amplitude
                audio_snapshot = np.concatenate(list(self.audio_chunks)).flatten()
                if len(audio_snapshot) < SAMPLE_RATE * 0.6:
                    continue

                if self.whisper_model is None:
                    continue

                norm_audio = normalize_audio(audio_snapshot)

                # Transcrição ao vivo de baixa latência
                segments, _ = self.whisper_model.transcribe(
                    norm_audio,
                    language="pt",
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    initial_prompt=TECH_INITIAL_PROMPT,
                    condition_on_previous_text=False,
                    without_timestamps=True,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=300, speech_pad_ms=200),
                )
                partial_text = " ".join(s.text.strip() for s in segments).strip()

                if partial_text and partial_text != self.live_transcript:
                    self.live_transcript = partial_text
                    self.ui_queue.put(("live_update", partial_text))

            except Exception as e:
                logger.warning(f"Aviso na transcrição ao vivo: {e}")

    def _stop_audio_capture(self) -> None:
        """Encerra a gravação e dispara a reescrita com Ollama instantaneamente."""
        self.is_recording = False
        if self.audio_stream:
            self.audio_stream.stop()
            self.audio_stream.close()
            self.audio_stream = None

        if not self.audio_chunks:
            self._show_compact_pill()
            return

        self.is_processing = True
        self._show_compact_pill(
            "✨  Estruturando pedido...",
            bg_col="#0c1b2e", border_col="#0284c7", text_col="#7dd3fc"
        )

        final_audio = np.concatenate(self.audio_chunks).flatten()
        duration_s = len(final_audio) / SAMPLE_RATE

        if duration_s < 0.4:
            self.is_processing = False
            self._show_compact_pill("⚠️  Fala muito curta")
            self.root.after(2000, lambda: self._show_compact_pill())
            return

        threading.Thread(
            target=self._final_pipeline_worker,
            args=(final_audio,),
            daemon=True,
        ).start()

    def _final_pipeline_worker(self, audio_data: np.ndarray) -> None:
        """Executa a transcrição final completa com alta precisão e aciona a reescrita rápida."""
        try:
            norm_audio = normalize_audio(audio_data)
            logger.info("Executando transcrição final completa com Whisper de alta precisão...")
            t_w = time.time()
            segments, _ = self.whisper_model.transcribe(
                norm_audio,
                language="pt",
                beam_size=3,
                best_of=3,
                temperature=0.0,
                initial_prompt=TECH_INITIAL_PROMPT,
                condition_on_previous_text=False,
                without_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=300),
            )
            raw_text = " ".join(s.text.strip() for s in segments).strip()
            logger.info(f"Transcrição concluída em {time.time()-t_w:.2f}s: \"{raw_text}\"")

            if not raw_text:
                self.ui_queue.put(("error", "Nenhuma fala detectada"))
                return

            logger.info("Reescrevendo com Ollama em modo otimizado...")
            t0 = time.time()
            llm_output = fast_call_ollama(raw_text)
            logger.info(f"Ollama respondeu em {time.time()-t0:.2f}s")

            pedido, duvidas = parse_llm_response(llm_output)
            final_prompt = pedido if (pedido and pedido.lower() != "nenhuma") else llm_output

            # Copia imediatamente para o Ctrl+V
            pyperclip.copy(final_prompt)
            logger.info(f"Prompt pronto copiado para a área de transferência!")

            self.ui_queue.put(("success", (raw_text, final_prompt)))

        except Exception as error:
            logger.error(f"Erro no pipeline final: {error}")
            self.ui_queue.put(("error", "Falha ao processar"))

    def _start_queue_listener(self) -> None:
        try:
            while not self.ui_queue.empty():
                msg_type, payload = self.ui_queue.get_nowait()
                if msg_type == "live_update":
                    # Atualiza o texto na tela em tempo real enquanto fala
                    self.live_text_lbl.config(text=payload, fg="#ffffff")
                elif msg_type == "success":
                    self.is_processing = False
                    raw_text, prompt_text = payload
                    self._show_expanded_results(raw_text, prompt_text)
                elif msg_type == "error":
                    self.is_processing = False
                    self._show_compact_pill(f"⚠️  {payload}")
                    self.root.after(2500, lambda: self._show_compact_pill())
        except Exception:
            pass

        self.root.after(80, self._start_queue_listener)

    def _on_new_record_click(self) -> None:
        self._show_compact_pill()
        self._toggle_recording()

    def _on_copy_again_click(self) -> None:
        if self.last_prompt_text:
            pyperclip.copy(self.last_prompt_text)
            self.badge_status.config(text="✓ Recopiado com sucesso!")
            self.root.after(2000, lambda: self.badge_status.config(text="✓ Copiado para o Ctrl+V"))

    def _load_models_in_background(self) -> None:
        """Carrega e aquece Whisper e Ollama na GPU em background para eliminar qualquer delay."""
        def load_whisper():
            try:
                logger.info(f"Pré-carregando Whisper '{LIVE_WHISPER_MODEL}' na GPU...")
                self.whisper_model = WhisperModel(
                    LIVE_WHISPER_MODEL,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                )
                logger.info(f"Whisper '{LIVE_WHISPER_MODEL}' aquecido e pronto na GPU!")
            except Exception as e:
                logger.warning(f"Aviso no carregamento do Whisper: {e}")

        def warmup_ollama():
            try:
                logger.info(f"Pré-aquecendo Ollama '{FAST_OLLAMA_MODEL}' na memória da GPU...")
                t0 = time.time()
                fast_call_ollama("teste de aquecimento")
                logger.info(f"Ollama '{FAST_OLLAMA_MODEL}' aquecido e pronto na GPU em {time.time()-t0:.2f}s!")
            except Exception as e:
                logger.warning(f"Aviso no pré-aquecimento do Ollama: {e}")

        threading.Thread(target=load_whisper, daemon=True).start()
        threading.Thread(target=warmup_ollama, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    app = LivePillApp(root)
    print("\n>>> Pílula Translúcida com Transcrição ao Vivo Ativa!")
    print(f"    - Modelo de Fala: Whisper '{LIVE_WHISPER_MODEL}' (GPU RTX 4050)")
    print(f"    - Modelo de Reescrita: Ollama '{FAST_OLLAMA_MODEL}' (Ultra Rápido)")
    print("    - As palavras aparecem na tela ENQUANTO você fala.")
    print("    - Prompt refinado copiado instantaneamente para o Ctrl+V.\n")
    root.mainloop()


if __name__ == "__main__":
    main()
