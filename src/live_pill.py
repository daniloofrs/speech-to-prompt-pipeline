"""Pílula Flutuante estilo Apple Dynamic Island (Transcrição ao Vivo + Prompt Instantâneo).

Inspirado nos princípios de design da Apple (WWDC Designing Fluid Interfaces):
- Física de molas (Springs com damping ratio e response) para transições contínuas e interrompíveis
- Resposta imediata no pointer-down (compressão tátil instantânea ao pressionar)
- Manipulação direta 1:1 com preservação do grab offset, projeção de momento e rubber-banding nas bordas
- Materiais translúcidos em camadas (Obsidian Dark Glass) com borda especular superior
- Ondas de voz reativas em tempo real (1:1 com a amplitude RMS do microfone)
"""

import json
import logging
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

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
    AUTO_STOP_ON_SILENCE,
    LIVE_HOTKEY,
    LIVE_OLLAMA_MODEL,
    LIVE_WHISPER_BEAM_SIZE,
    LIVE_WHISPER_MODEL,
    MAX_INITIAL_SILENCE_S,
    OLLAMA_HOST,
    SILENCE_THRESHOLD_RMS,
    SILENCE_TIMEOUT_S,
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
KEY_COLOR = "#010102"  # Cor-chave de recorte transparente no Windows


def play_chime(kind: str = "start") -> None:
    """Toca um chime sonoro suave nativo do Windows em background sem travar a interface."""
    def _play():
        if sys.platform != "win32":
            return
        try:
            import winsound
            if kind == "start":
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            elif kind == "success":
                winsound.MessageBeep(winsound.MB_OK)
            elif kind == "error":
                winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass
    threading.Thread(target=_play, daemon=True).start()


class Win32GlobalHotkey(threading.Thread):
    """Escuta teclas de atalho globais no Windows via Win32 RegisterHotKey em background."""
    def __init__(self, key_name: str, callback: Callable[[], None]):
        super().__init__(daemon=True)
        self.key_name = key_name.upper().strip()
        self.callback = callback
        self.running = True
        self.thread_id = None

    def run(self):
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        VK_MAP = {
            "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
            "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
            "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
            "SPACE": 0x20,
        }
        vk = VK_MAP.get(self.key_name, 0x77)  # Padrão: F8
        HOTKEY_ID = 101
        MOD_NOREPEAT = 0x4000

        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        # Tenta registrar com MOD_NOREPEAT (evita múltiplos disparos ao segurar)
        success = user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, vk)
        if not success:
            success = user32.RegisterHotKey(None, HOTKEY_ID, 0, vk)

        if not success:
            logger.warning(f"Não foi possível registrar o atalho global {self.key_name}. Pode estar em uso por outro aplicativo.")
            return

        logger.info(f"Atalho global '{self.key_name}' ativo! Pressione a qualquer momento no Windows para gravar/parar.")

        msg = wintypes.MSG()
        while self.running:
            res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            if msg.message == 0x0312:  # WM_HOTKEY
                try:
                    self.callback()
                except Exception as e:
                    logger.error(f"Erro ao disparar callback da hotkey {self.key_name}: {e}")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self):
        self.running = False
        if self.thread_id and sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT


# Modelos de alta precisão e baixa latência na GPU
FAST_OLLAMA_MODEL = os.getenv("LIVE_OLLAMA_MODEL", LIVE_OLLAMA_MODEL)

# Paleta de Materiais Apple Dark Mode (HIG System Colors)
APPLE_BG_MATERIAL = "#0d0d10"       # Superfície principal Dynamic Island
APPLE_BG_PRESSED = "#15151a"        # Superfície sob compressão (pointer-down)
APPLE_CARD_ELEVATED = "#18181d"     # Cartão interno elevado
APPLE_CARD_BORDER = "#28282f"       # Contorno sutil de cartão interno
APPLE_BORDER_OUTER = "#242429"      # Borda externa da cápsula
APPLE_SPECULAR_TOP = "#3d3d45"      # Brilho especular superior (luz incidindo no vidro)
APPLE_BTN_BG = "#26262c"            # Botão secundário elevado
APPLE_BTN_HOVER = "#32323a"         # Botão secundário hover
APPLE_BTN_ACTIVE = "#3e3e47"        # Botão secundário pressionado
APPLE_TEXT_PRIMARY = "#f5f5f7"      # Branco óptico Apple
APPLE_TEXT_SECONDARY = "#86868b"    # Cinza secundário Apple
APPLE_TEXT_TERTIARY = "#636366"     # Cinza terciário Apple
APPLE_RED = "#ff453a"               # System Red (Gravação)
APPLE_BLUE = "#0a84ff"              # System Blue (Ação primária / IA)
APPLE_BLUE_HOVER = "#2190ff"        # System Blue Hover
APPLE_BLUE_ACTIVE = "#0070e0"       # System Blue Active
APPLE_INDIGO = "#5e5ce6"            # System Indigo (Siri / Processamento)
APPLE_GREEN = "#30d158"             # System Green (Sucesso)
APPLE_GREEN_BG = "#0f2918"          # Fundo translúcido do badge de sucesso
APPLE_ORANGE = "#ff9f0a"            # System Orange (Aquecimento)

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
    audio = audio - np.mean(audio)
    peak = np.max(np.abs(audio))
    if peak > 0.01:
        audio = (audio / peak) * 0.95
    return audio.astype(np.float32)


def apple_rubberband(overshoot: float, dimension: float, constant: float = 0.55) -> float:
    """Equação oficial de rubber-banding da Apple (WWDC Designing Fluid Interfaces)."""
    if dimension <= 0:
        return 0.0
    return (overshoot * dimension * constant) / (dimension + constant * abs(overshoot))


def apple_project(initial_velocity: float, deceleration_rate: float = 0.992) -> float:
    """Projeção de momento da Apple para calcular ponto de repouso após um arremesso (flick)."""
    return (initial_velocity / 1000.0) * deceleration_rate / (1.0 - deceleration_rate)


class SpringValue:
    """Mola física parametrizada por damping ratio e response (padrão Apple UIKit/SwiftUI).

    Integra posição e velocidade continuamente, permitindo interrupção e redirecionamento
    em pleno voo sem descontinuidade de velocidade.
    """

    def __init__(self, initial: float, damping: float = 1.0, response: float = 0.32) -> None:
        self.value = float(initial)
        self.target = float(initial)
        self.velocity = 0.0
        self.damping = damping
        self.response = response

    def set_target(
        self,
        target: float,
        damping: Optional[float] = None,
        response: Optional[float] = None,
        add_velocity: float = 0.0,
    ) -> None:
        self.target = float(target)
        if damping is not None:
            self.damping = damping
        if response is not None:
            self.response = response
        if add_velocity != 0.0:
            self.velocity += add_velocity

    def snap(self, val: float) -> None:
        self.value = float(val)
        self.target = float(val)
        self.velocity = 0.0

    def step(self, dt: float) -> bool:
        """Avança a simulação física em dt segundos. Retorna True se ainda estiver em movimento."""
        dt = min(dt, 0.05)  # Estabilidade numérica
        if abs(self.target - self.value) < 0.15 and abs(self.velocity) < 1.5:
            self.value = self.target
            self.velocity = 0.0
            return False

        omega = 2.0 * math.pi / max(self.response, 0.04)
        stiffness = omega * omega
        damping_coeff = 2.0 * self.damping * omega

        # Integração semi-implícita de Euler
        force = -stiffness * (self.value - self.target) - damping_coeff * self.velocity
        self.velocity += force * dt
        self.value += self.velocity * dt
        return True


def build_rounded_polygon_points(
    x1: float, y1: float, x2: float, y2: float, radius: float, segments_per_corner: int = 10
) -> List[float]:
    """Gera vértices trigonométricos exatos para cápsulas e squircles sem distorção."""
    w = max(1.0, x2 - x1)
    h = max(1.0, y2 - y1)
    r = min(radius, w / 2.0, h / 2.0)

    corners = [
        (x2 - r, y1 + r, -math.pi / 2.0, 0.0),          # Superior direito
        (x2 - r, y2 - r, 0.0, math.pi / 2.0),           # Inferior direito
        (x1 + r, y2 - r, math.pi / 2.0, math.pi),       # Inferior esquerdo
        (x1 + r, y1 + r, math.pi, 3.0 * math.pi / 2.0), # Superior esquerdo
    ]

    pts: List[float] = []
    for cx, cy, start_ang, end_ang in corners:
        for i in range(segments_per_corner + 1):
            t = i / segments_per_corner
            ang = start_ang + t * (end_ang - start_ang)
            pts.append(cx + r * math.cos(ang))
            pts.append(cy + r * math.sin(ang))
    return pts


def draw_apple_surface(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    fill: str,
    outline: str = "",
    specular_top: str = "",
    outline_width: float = 1.0,
) -> None:
    """Renderiza uma superfície arredondada estilo Apple com brilho especular superior."""
    pts = build_rounded_polygon_points(x1, y1, x2, y2, radius)
    canvas.create_polygon(
        pts,
        smooth=False,
        fill=fill,
        outline=outline if outline else fill,
        width=outline_width,
    )
    if specular_top and (x2 - x1) > radius * 2.2:
        # Borda superior que capta a luz (Apple Material Depth §12)
        canvas.create_line(
            x1 + radius * 0.85,
            y1 + 1,
            x2 - radius * 0.85,
            y1 + 1,
            fill=specular_top,
            width=1,
        )


def ensure_ollama_running(host: str = OLLAMA_HOST) -> bool:
    """Verifica se o servidor Ollama está respondendo; se não, inicia em background silencioso (headless)."""
    try:
        req = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
        with urllib.request.urlopen(req, timeout=1.0) as res:
            if res.status == 200:
                return True
    except Exception:
        pass

    logger.info("Ollama não está aberto. Iniciando serviço em segundo plano (silencioso)...")
    try:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW

        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(24):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=1.0) as res:
                    if res.status == 200:
                        logger.info("Servidor Ollama ativo e respondendo em segundo plano!")
                        return True
            except Exception:
                pass
    except Exception as exc:
        logger.warning(f"Não foi possível iniciar o Ollama automaticamente: {exc}")

    return False


def fast_call_ollama(text: str) -> str:
    """Chamada ultra-otimizada ao Ollama: auto-start silencioso, keep_alive em VRAM e retorno imperativo direto."""
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
            "num_predict": 60,
            "num_ctx": 256,
            "stop": ["\n\n", "Nota:", "Observação:", "Explicação:", "DÚVIDAS:"],
        },
        "keep_alive": "60m",
        "stream": False,
    }

    req_data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url=endpoint,
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _execute():
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            raw_output = data.get("message", {}).get("content", "").strip()
            for prefix in ["PEDIDO:", "Prompt:", "prompt:"]:
                if raw_output.startswith(prefix):
                    raw_output = raw_output[len(prefix):].strip()
            return raw_output

    try:
        return _execute()
    except (urllib.error.URLError, ConnectionError):
        # Se o Ollama estava desligado, tenta iniciar silenciosamente em background e retenta
        if ensure_ollama_running():
            return _execute()
        raise


class LivePillApp:
    """Interface flutuante estilo Apple Dynamic Island com física de molas e transcrição em tempo real."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.state = "idle"  # 'idle' | 'recording' | 'processing' | 'result' | 'toast'
        self.toast_message = ""
        self.models_ready = False

        # Captura e modelos
        self.audio_chunks: List[np.ndarray] = []
        self.audio_stream: Optional[sd.InputStream] = None
        self.whisper_model: Optional[WhisperModel] = None
        self.record_start_time = 0.0
        self.current_rms = 0.0
        self.smoothed_rms = 0.0
        self.wave_bars = [0.2, 0.35, 0.5, 0.35, 0.2]
        self.transcribe_lock = threading.Lock()
        self.stop_recording_event = threading.Event()

        # Transcrição e resultado
        self.live_transcript = ""
        self.last_raw_text = ""
        self.last_prompt_text = ""
        self.copy_feedback_active = False

        # Detecção Inteligente de Silêncio (Hands-Free VAD)
        self.has_spoken: bool = False
        self.last_speech_time: float = 0.0
        self.is_silence_countdown: bool = False
        self.ambient_rms_floor: float = 0.003
        self.accumulated_speech_s: float = 0.0

        # IDs e caches de alta performance para o Canvas (Zero Garbage Collection por frame)
        self.wave_bar_ids: List[Tuple[int, float, float]] = []
        self.record_timer_id: Optional[int] = None
        self.last_timer_text: str = ""
        self.orb_outer_id: Optional[int] = None
        self.orb_inner_id: Optional[int] = None
        self.last_applied_geometry: Tuple[int, int, int, int] = (0, 0, 0, 0)

        self.ui_queue: queue.Queue = queue.Queue()

        # Geometria da tela
        self.screen_width = self.root.winfo_screenwidth()
        self.screen_height = self.root.winfo_screenheight()

        # Molas físicas (Posição ancorada na base central inferior)
        init_x = self.screen_width / 2.0
        init_bottom_y = self.screen_height - 60.0
        self.spring_x = SpringValue(init_x, damping=1.0, response=0.36)
        self.spring_bottom_y = SpringValue(init_bottom_y, damping=1.0, response=0.36)
        self.spring_w = SpringValue(300.0, damping=1.0, response=0.32)
        self.spring_h = SpringValue(46.0, damping=1.0, response=0.32)
        self.spring_r = SpringValue(23.0, damping=1.0, response=0.32)
        self.spring_scale = SpringValue(1.0, damping=0.9, response=0.14)

        # Estado de manipulação direta (Pointer / Gesture tracking)
        self.is_pointer_down = False
        self.has_dragged = False
        self.press_screen_x = 0.0
        self.press_screen_y = 0.0
        self.grab_offset_x = 0.0
        self.grab_offset_y = 0.0
        self.pointer_history: List[Tuple[float, float, float]] = []
        self.hovered_region: Optional[str] = None
        self.pressed_region: Optional[str] = None
        self.interactive_regions: Dict[str, Tuple[float, float, float, float, Callable[[], None]]] = {}

        self.last_frame_time = time.perf_counter()

        self._configure_window()
        self._build_canvas()
        self._transition_to("idle")
        # Aplica geometria inicial e renderiza imediatamente no inferior-meio
        self._apply_window_geometry()
        self._render_canvas()

        self._load_models_in_background()
        self._start_queue_listener()
        self._animation_tick()

        # Inicializa o atalho global Win32 (F8 por padrão)
        self.hotkey_listener: Optional[Win32GlobalHotkey] = None
        if sys.platform == "win32":
            try:
                self.hotkey_listener = Win32GlobalHotkey(
                    key_name=LIVE_HOTKEY,
                    callback=self._on_hotkey_toggle,
                )
                self.hotkey_listener.start()
            except Exception as e:
                logger.warning(f"Aviso ao inicializar atalho global: {e}")

        self.root.bind("<Escape>", lambda _e: self.destroy())
        self.root.protocol("WM_DELETE_WINDOW", self.destroy)

    def _on_hotkey_toggle(self) -> None:
        """Callback acionado quando a tecla global (ex: F8) é pressionada em qualquer app."""
        def _execute():
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            if self.state in ("idle", "toast", "result"):
                self._start_audio_capture()
            elif self.state == "recording":
                self._stop_audio_capture()
        self.root.after(0, _execute)

    def destroy(self) -> None:
        """Encerra a aplicação de forma limpa desregistrando hotkeys e parando streams."""
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass
        if self.audio_stream:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _configure_window(self) -> None:
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=KEY_COLOR)
        self.root.attributes("-transparentcolor", KEY_COLOR)
        self.root.attributes("-alpha", 0.96)

    def _build_canvas(self) -> None:
        self.canvas = tk.Canvas(
            self.root,
            bg=KEY_COLOR,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_pointer_down)
        self.canvas.bind("<B1-Motion>", self._on_pointer_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_pointer_up)
        self.canvas.bind("<Motion>", self._on_pointer_hover)
        self.canvas.bind("<Leave>", self._on_pointer_leave)

    def _transition_to(self, new_state: str, toast_text: str = "") -> None:
        """Altera o estado da Dynamic Island animando suavemente via molas criticamente amortecidas."""
        self.state = new_state
        if new_state == "idle":
            self.spring_w.set_target(300.0, damping=1.0, response=0.32)
            self.spring_h.set_target(46.0, damping=1.0, response=0.32)
            self.spring_r.set_target(23.0, damping=1.0, response=0.32)
        elif new_state == "recording":
            self.spring_w.set_target(470.0, damping=0.92, response=0.34)
            self.spring_h.set_target(118.0, damping=0.92, response=0.34)
            self.spring_r.set_target(28.0, damping=1.0, response=0.32)
        elif new_state == "processing":
            self.spring_w.set_target(330.0, damping=1.0, response=0.30)
            self.spring_h.set_target(50.0, damping=1.0, response=0.30)
            self.spring_r.set_target(25.0, damping=1.0, response=0.30)
        elif new_state == "result":
            # Ajusta altura dinamicamente conforme tamanho do prompt gerado
            prompt_lines = max(2, math.ceil(len(self.last_prompt_text) / 54))
            raw_lines = max(1, math.ceil(len(self.last_raw_text) / 62))
            target_h = min(340.0, 178.0 + prompt_lines * 19.0 + min(2, raw_lines) * 16.0)
            self.spring_w.set_target(510.0, damping=0.94, response=0.35)
            self.spring_h.set_target(target_h, damping=0.94, response=0.35)
            self.spring_r.set_target(28.0, damping=1.0, response=0.32)
        elif new_state == "toast":
            self.toast_message = toast_text
            self.spring_w.set_target(320.0, damping=0.9, response=0.28)
            self.spring_h.set_target(46.0, damping=0.9, response=0.28)
            self.spring_r.set_target(23.0, damping=1.0, response=0.28)

    # -------------------------------------------------------------------------
    # Eventos de Ponteiro (Apple Direct Manipulation, Hysteresis & Momentum)
    # -------------------------------------------------------------------------

    def _hit_test(self, x: float, y: float) -> Optional[str]:
        for name, (x1, y1, x2, y2, _) in self.interactive_regions.items():
            # Hit padding de +6px para conforto tátil (Apple Gesture Checklist §10)
            if (x1 - 6) <= x <= (x2 + 6) and (y1 - 6) <= y <= (y2 + 6):
                return name
        return None

    def _on_pointer_hover(self, event: tk.Event) -> None:
        region = self._hit_test(event.x, event.y)
        if region != self.hovered_region:
            self.hovered_region = region
            self._render_canvas()

    def _on_pointer_leave(self, _event: tk.Event) -> None:
        if self.hovered_region is not None:
            self.hovered_region = None
            self._render_canvas()

    def _on_pointer_down(self, event: tk.Event) -> None:
        """Feedback imediato no pointer-down (§1: Respond on pointer-down, not on release)."""
        self.is_pointer_down = True
        self.has_dragged = False
        self.press_screen_x = float(event.x_root)
        self.press_screen_y = float(event.y_root)

        # Preserva o offset exato de onde o usuário segurou (§2 Direct manipulation)
        self.grab_offset_x = self.press_screen_x - self.spring_x.value
        self.grab_offset_y = self.press_screen_y - self.spring_bottom_y.value

        now = time.perf_counter()
        self.pointer_history = [(now, self.press_screen_x, self.press_screen_y)]

        self.pressed_region = self._hit_test(event.x, event.y)

        # Compressão física sutil imediata ao tocar
        self.spring_scale.set_target(0.965, damping=0.95, response=0.12)
        self._render_canvas()

    def _on_pointer_move(self, event: tk.Event) -> None:
        if not self.is_pointer_down:
            return

        sx = float(event.x_root)
        sy = float(event.y_root)
        dx = sx - self.press_screen_x
        dy = sy - self.press_screen_y

        # Histerese de 8px antes de assumir arraste (§10 Gesture design details)
        if not self.has_dragged and math.hypot(dx, dy) > 8.0:
            self.has_dragged = True
            self.pressed_region = None
            # Restaura a escala normal enquanto flutua sob o ponteiro
            self.spring_scale.set_target(1.0, damping=0.95, response=0.16)

        if self.has_dragged:
            now = time.perf_counter()
            self.pointer_history.append((now, sx, sy))
            if len(self.pointer_history) > 6:
                self.pointer_history.pop(0)

            raw_target_x = sx - self.grab_offset_x
            raw_target_bottom_y = sy - self.grab_offset_y

            # Limites seguros da tela com rubber-banding progressivo (§9)
            half_w = self.spring_w.value / 2.0 + 16.0
            min_x = half_w
            max_x = self.screen_width - half_w
            min_by = self.spring_h.value + 20.0
            max_by = self.screen_height - 20.0

            if raw_target_x < min_x:
                raw_target_x = min_x - apple_rubberband(min_x - raw_target_x, 180.0)
            elif raw_target_x > max_x:
                raw_target_x = max_x + apple_rubberband(raw_target_x - max_x, 180.0)

            if raw_target_bottom_y < min_by:
                raw_target_bottom_y = min_by - apple_rubberband(min_by - raw_target_bottom_y, 180.0)
            elif raw_target_bottom_y > max_by:
                raw_target_bottom_y = max_by + apple_rubberband(raw_target_bottom_y - max_by, 180.0)

            # Rastreamento 1:1 durante o arraste
            self.spring_x.snap(raw_target_x)
            self.spring_bottom_y.snap(raw_target_bottom_y)
            self._apply_window_geometry()

    def _on_pointer_up(self, event: tk.Event) -> None:
        if not self.is_pointer_down:
            return
        self.is_pointer_down = False

        # Solta a compressão tátil de volta para 1.0
        self.spring_scale.set_target(1.0, damping=0.82, response=0.22)

        if self.has_dragged:
            # Calcula velocidade de soltura e projeta o momento (§5 e §6)
            vx, vy = self._compute_release_velocity()
            proj_x = self.spring_x.value + apple_project(vx, 0.990)
            proj_by = self.spring_bottom_y.value + apple_project(vy, 0.990)

            half_w = self.spring_w.value / 2.0 + 20.0
            clamped_x = max(half_w, min(self.screen_width - half_w, proj_x))
            clamped_by = max(self.spring_h.value + 24.0, min(self.screen_height - 24.0, proj_by))

            # Handoff de velocidade para as molas independentes X e Y (§3 e §5)
            self.spring_x.set_target(clamped_x, damping=0.82, response=0.38, add_velocity=vx)
            self.spring_bottom_y.set_target(clamped_by, damping=0.82, response=0.38, add_velocity=vy)
            return

        # Se foi um clique sem arraste, verifica se soltou sobre a mesma região pressionada
        release_region = self._hit_test(event.x, event.y)
        active_region = self.pressed_region
        self.pressed_region = None

        if active_region and active_region == release_region and active_region in self.interactive_regions:
            _, _, _, _, callback = self.interactive_regions[active_region]
            callback()
            return

        # Ação padrão ao tocar no corpo da Dynamic Island
        if self.state in ("idle", "toast"):
            self._start_audio_capture()
        elif self.state == "recording":
            self._stop_audio_capture()

    def _compute_release_velocity(self) -> Tuple[float, float]:
        if len(self.pointer_history) < 2:
            return 0.0, 0.0
        t0, x0, y0 = self.pointer_history[0]
        t1, x1, y1 = self.pointer_history[-1]
        dt = t1 - t0
        if dt <= 0.004 or (time.perf_counter() - t1) > 0.12:
            return 0.0, 0.0
        vx = max(-2200.0, min(2200.0, (x1 - x0) / dt))
        vy = max(-2200.0, min(2200.0, (y1 - y0) / dt))
        return vx, vy

    # -------------------------------------------------------------------------
    # Loop de Física e Renderização (60 FPS sincronizado)
    # -------------------------------------------------------------------------

    def _animation_tick(self) -> None:
        now = time.perf_counter()
        dt = now - self.last_frame_time
        self.last_frame_time = now

        moved_x = self.spring_x.step(dt)
        moved_y = self.spring_bottom_y.step(dt)
        moved_w = self.spring_w.step(dt)
        moved_h = self.spring_h.step(dt)
        moved_r = self.spring_r.step(dt)
        moved_s = self.spring_scale.step(dt)
        is_moving = moved_x or moved_y or moved_w or moved_h or moved_r or moved_s

        # Atualiza ondas de voz reativas durante gravação
        if self.state == "recording":
            wall_now = time.time()
            self.smoothed_rms = 0.72 * self.smoothed_rms + 0.28 * self.current_rms
            base_energy = min(1.0, self.smoothed_rms * 14.0)
            t = now * 9.5
            Multipliers = [0.55, 0.85, 1.15, 0.85, 0.55]
            for i in range(5):
                wave = 0.18 + base_energy * Multipliers[i] * (0.65 + 0.35 * math.sin(t + i * 1.1))
                self.wave_bars[i] = max(0.16, min(1.0, wave))

            # Calibra ruído de fundo nos primeiros 350ms (apenas com sons abaixo do limiar de fala)
            if (wall_now - self.record_start_time) < 0.35 and self.current_rms < SILENCE_THRESHOLD_RMS:
                self.ambient_rms_floor = min(
                    SILENCE_THRESHOLD_RMS * 0.75,
                    0.85 * self.ambient_rms_floor + 0.15 * self.current_rms,
                )

            # Detecção de silêncio inteligente (Hands-Free Auto-Stop)
            if AUTO_STOP_ON_SILENCE:
                speech_threshold = min(0.024, max(0.007, self.ambient_rms_floor * 2.2))
                if self.smoothed_rms > speech_threshold:
                    self.has_spoken = True
                    self.last_speech_time = wall_now
                    self.is_silence_countdown = False
                    self.accumulated_speech_s += dt
                elif self.has_spoken:
                    if self.accumulated_speech_s >= 0.5:
                        silence_elapsed = wall_now - self.last_speech_time
                        if silence_elapsed >= 0.75:
                            self.is_silence_countdown = True
                        if silence_elapsed >= SILENCE_TIMEOUT_S:
                            logger.info(
                                "Silêncio de %.1fs detectado após fala. Auto-concluindo transcrição...",
                                SILENCE_TIMEOUT_S,
                            )
                            self._stop_audio_capture()
                    else:
                        # Micro-blip que ainda não atingiu 0.5s de fala acumulada:
                        # Se passar muito tempo sem fala contínua, trata como inatividade inicial
                        if (wall_now - self.record_start_time) >= MAX_INITIAL_SILENCE_S:
                            logger.info(
                                "Tempo limite de inatividade inicial (%.1fs). Cancelando...",
                                MAX_INITIAL_SILENCE_S,
                            )
                            self._stop_audio_capture(reason_toast="Nenhuma fala detectada")
                else:
                    # Inatividade inicial prolongada (ex: pressionou F8 por engano)
                    if (wall_now - self.record_start_time) >= MAX_INITIAL_SILENCE_S:
                        logger.info(
                            "Tempo limite de inatividade inicial (%.1fs). Cancelando...",
                            MAX_INITIAL_SILENCE_S,
                        )
                        self._stop_audio_capture(reason_toast="Nenhuma fala detectada")

        if is_moving:
            self._apply_window_geometry()
            self._render_canvas()
        elif self.state == "recording":
            self._fast_update_recording_bars()
        elif self.state == "processing":
            self._fast_update_processing_spinner()

        self.root.after(16, self._animation_tick)

    def _apply_window_geometry(self) -> None:
        scale = self.spring_scale.value
        w = max(120, int(round(self.spring_w.value * scale)))
        h = max(36, int(round(self.spring_h.value * scale)))
        x = int(round(self.spring_x.value - w / 2.0))
        y = int(round(self.spring_bottom_y.value - h))
        if (w, h, x, y) != self.last_applied_geometry:
            self.last_applied_geometry = (w, h, x, y)
            self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _render_canvas(self) -> None:
        self.canvas.delete("all")
        self.interactive_regions.clear()

        scale = self.spring_scale.value
        w = max(120.0, self.spring_w.value * scale)
        h = max(36.0, self.spring_h.value * scale)
        r = max(14.0, self.spring_r.value * scale)

        bg_fill = APPLE_BG_PRESSED if (self.is_pointer_down and not self.has_dragged and not self.pressed_region) else APPLE_BG_MATERIAL

        # 1. Cápsula / Superfície Principal da Dynamic Island
        draw_apple_surface(
            self.canvas,
            x1=2,
            y1=2,
            x2=w - 2,
            y2=h - 2,
            radius=r,
            fill=bg_fill,
            outline=APPLE_BORDER_OUTER,
            specular_top=APPLE_SPECULAR_TOP,
            outline_width=1.2,
        )

        # Renderiza conteúdo conforme o estado atual
        if self.state == "idle":
            self._draw_idle_state(w, h)
        elif self.state == "toast":
            self._draw_toast_state(w, h)
        elif self.state == "recording":
            self._draw_recording_state(w, h)
        elif self.state == "processing":
            self._draw_processing_state(w, h)
        elif self.state == "result":
            self._draw_result_state(w, h)

    def _draw_close_button(self, x: float, y: float, radius: float = 11.0) -> None:
        is_hover = self.hovered_region == "close"
        is_press = self.pressed_region == "close"
        if is_hover or is_press:
            circle_fill = APPLE_BTN_ACTIVE if is_press else APPLE_BTN_BG
            self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=circle_fill, outline=""
            )
        fg = APPLE_TEXT_PRIMARY if (is_hover or is_press) else APPLE_TEXT_TERTIARY
        self.canvas.create_text(
            x, y, text="✕", fill=fg, font=("Segoe UI", 8, "bold")
        )
        self.interactive_regions["close"] = (
            x - radius, y - radius, x + radius, y + radius, self.destroy
        )

    def _draw_idle_state(self, w: float, h: float) -> None:
        cy = h / 2.0

        # Orbe esquerdo com indicador de status da GPU
        orb_x = 25.0
        self.canvas.create_oval(
            orb_x - 13, cy - 13, orb_x + 13, cy + 13,
            fill=APPLE_CARD_ELEVATED, outline=APPLE_CARD_BORDER
        )
        dot_color = APPLE_GREEN if self.models_ready else APPLE_ORANGE
        self.canvas.create_oval(
            orb_x - 3.5, cy - 3.5, orb_x + 3.5, cy + 3.5,
            fill=dot_color, outline=""
        )

        # Rótulo central com tipografia limpa e indicação da tecla de atalho
        label_text = f"Ditar comando ({LIVE_HOTKEY})" if self.models_ready else "Preparando motor neural..."
        self.canvas.create_text(
            w / 2.0 - 4,
            cy - 0.5,
            text=label_text,
            fill=APPLE_TEXT_PRIMARY,
            font=("Segoe UI Semibold", 10),
        )

        # Botão discreto de fechar à direita
        self._draw_close_button(w - 22.0, cy)

    def _draw_toast_state(self, w: float, h: float) -> None:
        cy = h / 2.0
        self.canvas.create_text(
            w / 2.0 - 8,
            cy,
            text=self.toast_message,
            fill=APPLE_ORANGE,
            font=("Segoe UI Semibold", 9),
        )
        self._draw_close_button(w - 22.0, cy)

    def _draw_recording_state(self, w: float, h: float) -> None:
        # Cabeçalho da Dynamic Island em gravação
        top_y = 22.0

        # Waveform Apple Voice Memos (5 barras dinâmicas em cache de IDs)
        start_wx = 24.0
        self.wave_bar_ids = []
        for i, val in enumerate(self.wave_bars):
            bx = start_wx + i * 5.5
            bar_h = max(4.0, val * 18.0)
            bar_id = self.canvas.create_line(
                bx, top_y - bar_h / 2.0, bx, top_y + bar_h / 2.0,
                fill=APPLE_RED, width=2.5, capstyle="round"
            )
            self.wave_bar_ids.append((bar_id, bx, top_y))

        elapsed = int(time.time() - self.record_start_time)
        mins, secs = elapsed // 60, elapsed % 60
        if self.is_silence_countdown:
            self.last_timer_text = "Concluindo..."
            timer_color = APPLE_ORANGE
        else:
            self.last_timer_text = f"Ouvindo  {mins}:{secs:02d}"
            timer_color = APPLE_RED

        self.record_timer_id = self.canvas.create_text(
            58.0,
            top_y,
            text=self.last_timer_text,
            fill=timer_color,
            anchor="w",
            font=("Segoe UI Semibold", 9),
        )

        # Chip "Concluir" no canto superior direito
        done_x2 = w - 38.0
        done_x1 = done_x2 - 64.0
        is_done_hover = self.hovered_region == "done_rec"
        done_bg = APPLE_BTN_HOVER if is_done_hover else APPLE_CARD_ELEVATED
        draw_apple_surface(
            self.canvas, done_x1, top_y - 10, done_x2, top_y + 10,
            radius=10, fill=done_bg, outline=APPLE_CARD_BORDER
        )
        self.canvas.create_text(
            (done_x1 + done_x2) / 2.0, top_y,
            text="Concluir", fill=APPLE_TEXT_PRIMARY, font=("Segoe UI Semibold", 8)
        )
        self.interactive_regions["done_rec"] = (
            done_x1, top_y - 10, done_x2, top_y + 10, self._stop_audio_capture
        )

        self._draw_close_button(w - 20.0, top_y)

        # Área de texto ao vivo (enquanto o usuário fala)
        has_words = bool(self.live_transcript.strip())
        display_text = self.live_transcript if has_words else "Fale naturalmente, suas palavras aparecem aqui..."
        text_color = APPLE_TEXT_PRIMARY if has_words else APPLE_TEXT_SECONDARY

        self.canvas.create_text(
            22.0,
            42.0,
            text=display_text,
            fill=text_color,
            anchor="nw",
            width=max(180, int(w - 44)),
            font=("Segoe UI Semibold" if has_words else "Segoe UI", 10),
        )

    def _fast_update_recording_bars(self) -> None:
        """Atualiza apenas as coordenadas das 5 barras de onda e do cronômetro sem destruir o canvas."""
        if not self.wave_bar_ids:
            return
        for i, (bar_id, bx, top_y) in enumerate(self.wave_bar_ids):
            if i < len(self.wave_bars):
                val = self.wave_bars[i]
                bar_h = max(4.0, val * 18.0)
                self.canvas.coords(bar_id, bx, top_y - bar_h / 2.0, bx, top_y + bar_h / 2.0)

        if self.record_timer_id:
            elapsed = int(time.time() - self.record_start_time)
            mins, secs = elapsed // 60, elapsed % 60
            if self.is_silence_countdown:
                timer_text = "Concluindo..."
                timer_color = APPLE_ORANGE
            else:
                timer_text = f"Ouvindo  {mins}:{secs:02d}"
                timer_color = APPLE_RED

            if self.last_timer_text != timer_text:
                self.last_timer_text = timer_text
                self.canvas.itemconfig(self.record_timer_id, text=timer_text, fill=timer_color)

    def _draw_processing_state(self, w: float, h: float) -> None:
        cy = h / 2.0
        # Indicador pulsante estilo Apple Intelligence / Siri
        t = time.perf_counter() * 5.0
        pulse_r = 6.5 + 1.8 * math.sin(t)
        orb_x = 28.0
        self.orb_outer_id = self.canvas.create_oval(
            orb_x - pulse_r - 3, cy - pulse_r - 3, orb_x + pulse_r + 3, cy + pulse_r + 3,
            fill="#191833", outline=""
        )
        self.orb_inner_id = self.canvas.create_oval(
            orb_x - pulse_r, cy - pulse_r, orb_x + pulse_r, cy + pulse_r,
            fill=APPLE_INDIGO, outline=APPLE_BLUE
        )

        self.canvas.create_text(
            w / 2.0 + 6,
            cy,
            text="Refinando comando...",
            fill=APPLE_TEXT_PRIMARY,
            font=("Segoe UI Semibold", 10),
        )

    def _fast_update_processing_spinner(self) -> None:
        """Atualiza a pulsação do orbe de processamento via coords sem recriar elementos."""
        if not self.orb_outer_id or not self.orb_inner_id:
            return
        t = time.perf_counter() * 5.0
        pulse_r = 6.5 + 1.8 * math.sin(t)
        scale = self.spring_scale.value
        h = max(36.0, self.spring_h.value * scale)
        cy = h / 2.0
        orb_x = 28.0
        self.canvas.coords(
            self.orb_outer_id,
            orb_x - pulse_r - 3, cy - pulse_r - 3,
            orb_x + pulse_r + 3, cy + pulse_r + 3,
        )
        self.canvas.coords(
            self.orb_inner_id,
            orb_x - pulse_r, cy - pulse_r,
            orb_x + pulse_r, cy + pulse_r,
        )

    def _draw_result_state(self, w: float, h: float) -> None:
        if w < 360 or h < 140:
            return

        # 1. Cabeçalho com confirmação de cópia automática
        top_y = 22.0
        badge_text = "✓ Copiado novamente" if self.copy_feedback_active else "✓ Copiado para o Ctrl+V"
        badge_w = 152.0
        draw_apple_surface(
            self.canvas, 18.0, top_y - 10, 18.0 + badge_w, top_y + 10,
            radius=10, fill=APPLE_GREEN_BG, outline="#194d2b"
        )
        self.canvas.create_text(
            18.0 + badge_w / 2.0, top_y,
            text=badge_text, fill=APPLE_GREEN, font=("Segoe UI Semibold", 8)
        )

        self._draw_close_button(w - 22.0, top_y)

        # 2. Cartão Principal: Comando Refinado (Hero Card)
        card_top = 42.0
        card_bottom = h - 86.0
        draw_apple_surface(
            self.canvas,
            x1=16.0,
            y1=card_top,
            x2=w - 16.0,
            y2=card_bottom,
            radius=16.0,
            fill=APPLE_CARD_ELEVATED,
            outline=APPLE_CARD_BORDER,
            specular_top="#303038",
        )

        self.canvas.create_text(
            28.0,
            card_top + 12.0,
            text=self.last_prompt_text,
            fill=APPLE_TEXT_PRIMARY,
            anchor="nw",
            width=max(200, int(w - 56)),
            font=("Segoe UI Semibold", 10),
        )

        # 3. Legenda discreta da fala original
        raw_preview = self.last_raw_text
        if len(raw_preview) > 78:
            raw_preview = raw_preview[:75] + "..."
        self.canvas.create_text(
            20.0,
            card_bottom + 10.0,
            text=f"Você disse: “{raw_preview}”",
            fill=APPLE_TEXT_SECONDARY,
            anchor="nw",
            width=max(200, int(w - 40)),
            font=("Segoe UI", 8),
        )

        # 4. Barra de Ações Inferior (Botões em cápsula com resposta tátil)
        btn_y1 = h - 44.0
        btn_y2 = h - 14.0

        # Botão Primário: Novo Ditado
        self._draw_pill_button(
            name="btn_new",
            x1=16.0, y1=btn_y1, x2=142.0, y2=btn_y2,
            label="🎙️ Novo Ditado",
            primary=True,
            callback=self._on_new_record_click,
        )

        # Botão Secundário: Copiar
        self._draw_pill_button(
            name="btn_copy",
            x1=150.0, y1=btn_y1, x2=248.0, y2=btn_y2,
            label="📋 Copiar",
            primary=False,
            callback=self._on_copy_again_click,
        )

        # Botão Terciário: Recolher à direita
        self._draw_pill_button(
            name="btn_collapse",
            x1=w - 104.0, y1=btn_y1, x2=w - 16.0, y2=btn_y2,
            label="Recolher",
            primary=False,
            callback=lambda: self._transition_to("idle"),
        )

    def _draw_pill_button(
        self,
        name: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        label: str,
        primary: bool,
        callback: Callable[[], None],
    ) -> None:
        is_hover = self.hovered_region == name
        is_press = self.pressed_region == name

        # Pequena contração óptica no botão pressionado (§1 Response on pointer-down)
        if is_press:
            x1 += 1.5
            y1 += 1.0
            x2 -= 1.5
            y2 -= 1.0

        if primary:
            fill = APPLE_BLUE_ACTIVE if is_press else (APPLE_BLUE_HOVER if is_hover else APPLE_BLUE)
            outline = "#409cff" if is_hover else APPLE_BLUE
        else:
            fill = APPLE_BTN_ACTIVE if is_press else (APPLE_BTN_HOVER if is_hover else APPLE_BTN_BG)
            outline = "#3a3a42" if is_hover else APPLE_CARD_BORDER

        r = (y2 - y1) / 2.0
        draw_apple_surface(
            self.canvas, x1, y1, x2, y2, radius=r, fill=fill, outline=outline
        )
        self.canvas.create_text(
            (x1 + x2) / 2.0,
            (y1 + y2) / 2.0,
            text=label,
            fill=APPLE_TEXT_PRIMARY,
            font=("Segoe UI Semibold", 8),
        )
        self.interactive_regions[name] = (x1, y1, x2, y2, callback)

    # -------------------------------------------------------------------------
    # Pipeline de Áudio, Transcrição ao Vivo e Reescrita
    # -------------------------------------------------------------------------

    def _start_audio_capture(self) -> None:
        if self.state == "processing":
            return

        self.audio_chunks = []
        self.live_transcript = ""
        self.current_rms = 0.0
        self.smoothed_rms = 0.0
        self.record_start_time = time.time()
        self.stop_recording_event.clear()
        self.has_spoken = False
        self.last_speech_time = time.time()
        self.is_silence_countdown = False
        self.accumulated_speech_s = 0.0

        self._transition_to("recording")
        play_chime("start")

        def audio_callback(indata, _frames, _time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            chunk = indata.copy()
            self.audio_chunks.append(chunk)
            self.current_rms = float(np.sqrt(np.mean(chunk ** 2)))

        try:
            self.audio_stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=audio_callback,
            )
            self.audio_stream.start()
            logger.info("Microfone ativo. Transcrição ao vivo iniciada...")

            threading.Thread(target=self._live_transcribe_loop, daemon=True).start()

        except Exception as error:
            logger.error(f"Erro ao abrir microfone: {error}")
            self._show_toast("Microfone indisponível")

    def _live_transcribe_loop(self) -> None:
        """Loop que transcreve blocos de fala em tempo real na GPU de forma não-bloqueante."""
        last_processed_len = 0
        while not self.stop_recording_event.is_set() and self.state == "recording":
            # Dorme em fatias curtas de 100ms para responder instantaneamente ao stop_recording_event
            for _ in range(8):  # ~0.8s
                if self.stop_recording_event.is_set() or self.state != "recording":
                    return
                time.sleep(0.1)

            if len(self.audio_chunks) < 3:
                continue

            try:
                audio_snapshot = np.concatenate(list(self.audio_chunks)).flatten()
                # Só processa se houver ao menos 0.7s de áudio total e 0.4s de novos dados
                if len(audio_snapshot) < SAMPLE_RATE * 0.7 or (len(audio_snapshot) - last_processed_len) < SAMPLE_RATE * 0.4:
                    continue
                if self.whisper_model is None:
                    continue

                # Tenta adquirir o lock sem bloquear; se o worker final estiver usando a GPU, pula
                if not self.transcribe_lock.acquire(blocking=False):
                    continue

                try:
                    if self.stop_recording_event.is_set() or self.state != "recording":
                        return

                    last_processed_len = len(audio_snapshot)
                    norm_audio = normalize_audio(audio_snapshot)

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
                        vad_parameters=dict(min_silence_duration_ms=250, speech_pad_ms=150),
                    )
                    partial_text = " ".join(s.text.strip() for s in segments).strip()

                    if partial_text and partial_text != self.live_transcript and not self.stop_recording_event.is_set():
                        self.live_transcript = partial_text
                        self.ui_queue.put(("live_update", partial_text))

                finally:
                    self.transcribe_lock.release()

            except Exception as e:
                logger.warning(f"Aviso na transcrição ao vivo: {e}")

    def _stop_audio_capture(self, reason_toast: Optional[str] = None) -> None:
        # Sinaliza IMEDIATAMENTE a parada para o loop de streaming liberar a GPU
        self.stop_recording_event.set()

        if self.audio_stream:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
            self.audio_stream = None

        if reason_toast is not None:
            self.audio_chunks.clear()
            self._show_toast(reason_toast)
            return

        if self.state != "recording":
            return

        if not self.audio_chunks:
            self._transition_to("idle")
            return

        final_audio = np.concatenate([c.reshape(-1) for c in self.audio_chunks])
        duration_s = len(final_audio) / SAMPLE_RATE

        if duration_s < 0.35:
            self.audio_chunks.clear()
            self._show_toast("Fala muito curta")
            return

        self._transition_to("processing")

        threading.Thread(
            target=self._final_pipeline_worker,
            args=(final_audio,),
            daemon=True,
        ).start()

    def _final_pipeline_worker(self, audio_data: np.ndarray) -> None:
        """Executa a transcrição final completa com alta velocidade na GPU e aciona a reescrita."""
        try:
            # Garante acesso exclusivo à GPU com o lock
            with self.transcribe_lock:
                wait_ticks = 0
                while self.whisper_model is None and wait_ticks < 60:
                    time.sleep(0.1)
                    wait_ticks += 1
                if self.whisper_model is None:
                    self.whisper_model = self._instantiate_whisper()

                norm_audio = normalize_audio(audio_data)
                logger.info(f"Executando transcrição final otimizada (beam_size={LIVE_WHISPER_BEAM_SIZE})...")
                t_w = time.time()
                segments, _ = self.whisper_model.transcribe(
                    norm_audio,
                    language="pt",
                    beam_size=LIVE_WHISPER_BEAM_SIZE,
                    best_of=LIVE_WHISPER_BEAM_SIZE,
                    temperature=0.0,
                    initial_prompt=TECH_INITIAL_PROMPT,
                    condition_on_previous_text=False,
                    without_timestamps=True,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=350, speech_pad_ms=250),
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

            pedido, _ = parse_llm_response(llm_output)
            final_prompt = pedido if (pedido and pedido.lower() != "nenhuma") else llm_output

            pyperclip.copy(final_prompt)
            logger.info("Prompt pronto copiado para a área de transferência!")

            self.ui_queue.put(("success", (raw_text, final_prompt)))

        except Exception as error:
            logger.error(f"Erro no pipeline final: {error}")
            self.ui_queue.put(("error", "Falha ao processar"))
            self.ui_queue.put(("error", "Falha ao processar"))

    def _show_toast(self, message: str) -> None:
        self._transition_to("toast", toast_text=message)
        self.root.after(2200, lambda: self._transition_to("idle") if self.state == "toast" else None)

    def _start_queue_listener(self) -> None:
        try:
            while not self.ui_queue.empty():
                msg_type, payload = self.ui_queue.get_nowait()
                if msg_type == "live_update" and self.state == "recording":
                    # Expande suavemente a altura da Dynamic Island se a fala for longa
                    lines = max(1, math.ceil(len(payload) / 48))
                    target_h = min(190.0, max(118.0, 68.0 + lines * 22.0))
                    self.spring_h.set_target(target_h, damping=1.0, response=0.28)
                elif msg_type == "models_ready":
                    self.models_ready = True
                    self._render_canvas()
                elif msg_type == "success":
                    raw_text, prompt_text = payload
                    self.last_raw_text = raw_text
                    self.last_prompt_text = prompt_text
                    play_chime("success")
                    self._transition_to("result")
                elif msg_type == "error":
                    self._show_toast(str(payload))
        except Exception:
            pass

        self.root.after(60, self._start_queue_listener)

    def _on_new_record_click(self) -> None:
        self._start_audio_capture()

    def _on_copy_again_click(self) -> None:
        if self.last_prompt_text:
            pyperclip.copy(self.last_prompt_text)
            play_chime("success")
            self.copy_feedback_active = True
            self._render_canvas()
            self.root.after(
                1800,
                lambda: (setattr(self, "copy_feedback_active", False), self._render_canvas()),
            )

    def _instantiate_whisper(self) -> WhisperModel:
        """Instancia o Whisper priorizando o cache local para evitar falhas ou latência de rede."""
        try:
            return WhisperModel(
                LIVE_WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                local_files_only=True,
            )
        except Exception:
            return WhisperModel(
                LIVE_WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                local_files_only=False,
            )

    def _load_models_in_background(self) -> None:
        """Carrega e aquece Whisper e Ollama na GPU em background."""
        def init_all():
            try:
                logger.info(f"Pré-carregando Whisper '{LIVE_WHISPER_MODEL}' na GPU...")
                self.whisper_model = self._instantiate_whisper()
                logger.info(f"Whisper '{LIVE_WHISPER_MODEL}' aquecido e pronto na GPU!")
            except Exception as e:
                logger.warning(f"Aviso no carregamento do Whisper: {e}")

            try:
                logger.info(f"Pré-aquecendo Ollama '{FAST_OLLAMA_MODEL}' na memória da GPU...")
                t0 = time.time()
                fast_call_ollama("teste de aquecimento")
                logger.info(f"Ollama '{FAST_OLLAMA_MODEL}' aquecido e pronto na GPU em {time.time()-t0:.2f}s!")
            except Exception as e:
                logger.warning(f"Aviso no pré-aquecimento do Ollama: {e}")

            self.ui_queue.put(("models_ready", True))

        threading.Thread(target=init_all, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    app = LivePillApp(root)
    print("\n>>> Dynamic Island (Apple Fluid Design) Ativa!")
    print(f"    - Atalho Global no Windows: [{LIVE_HOTKEY}] (grave e pare de qualquer app)")
    print(f"    - Modelo de Fala: Whisper '{LIVE_WHISPER_MODEL}' (GPU RTX 4050)")
    print(f"    - Modelo de Reescrita: Ollama '{FAST_OLLAMA_MODEL}' (Ultra Rápido)")
    print("    - Física de molas contínuas (60 FPS), ondas reativas de voz e projeção de momento.")
    print("    - Prompt refinado copiado instantaneamente para o Ctrl+V.\n")
    try:
        root.mainloop()
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
