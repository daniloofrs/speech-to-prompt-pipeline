"""Benchmark completo de velocidade: Áudio -> Whisper (GPU) -> EmbeddedLLM (GGUF In-Process) -> Prompt."""

import json
import os
import sys
import time
import urllib.request
import wave
from pathlib import Path

# Se executado no Python global (sem ativar o venv), reinicia automaticamente usando o venv do projeto
_project_root = Path(__file__).resolve().parent.parent
_venv_python = _project_root / "venv" / "Scripts" / "python.exe"
if _venv_python.exists() and Path(sys.executable).resolve() != _venv_python.resolve():
    import subprocess
    sys.exit(subprocess.call([str(_venv_python)] + sys.argv))

# Adiciona raiz ao sys.path
sys.path.insert(0, str(_project_root))

from faster_whisper import WhisperModel
from src.config import (
    EMBEDDED_MODEL_PATH,
    LLM_BACKEND,
    LIVE_OLLAMA_MODEL,
    OLLAMA_HOST,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)
from src.embedded_llm import EmbeddedLLM

TECH_PROMPT = "Python, Docker, FastAPI, SQL, PostgreSQL, PyTorch, pandas, DataFrame, Ollama, Whisper, API REST"
FAST_OLLAMA_MODEL = os.getenv("LIVE_OLLAMA_MODEL", LIVE_OLLAMA_MODEL)


def get_audio_duration(file_path: str) -> float:
    with wave.open(file_path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def call_ollama(text: str) -> str:
    endpoint = f"{OLLAMA_HOST.rstrip('/')}/api/chat"
    system_prompt = (
        "Você é um especialista em Prompt Engineering. "
        "Transforme a fala do usuário em um prompt direto e profissional para IA. "
        "Retorne APENAS o comando imperativo refinado sem aspas ou explicações."
    )
    payload = {
        "model": FAST_OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "options": {"temperature": 0.0, "num_predict": 60, "num_ctx": 256},
        "stream": False,
    }
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=req_data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as res:
        data = json.loads(res.read().decode("utf-8"))
        return data.get("message", {}).get("content", "").strip()


def run_benchmark(audio_path: str):
    duration = get_audio_duration(audio_path)
    print("=" * 70)
    print("BENCHMARK OFICIAL DE VELOCIDADE: SPEECH-TO-PROMPT PIPELINE")
    print("=" * 70)
    print(f"  Arquivo de Áudio   : {audio_path}")
    print(f"  Duração da Fala    : {duration:.2f} segundos")
    print(f"  GPU Dedicada       : NVIDIA GeForce RTX 4050 Laptop (6 GB VRAM)")
    print(f"  Modelo de Fala     : Faster-Whisper '{WHISPER_MODEL}' ({WHISPER_COMPUTE_TYPE} CUDA)")
    print(f"  Motor de Reescrita : EmbeddedLLM (In-Process GGUF Qwen 2.5 1.5B na GPU)")
    print("-" * 70)

    print("Inicializando modelos na VRAM...")
    t0_init = time.perf_counter()
    model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)
    embedded_llm = EmbeddedLLM()
    embedded_llm.load_model()
    embedded_llm.warmup()
    init_duration = time.perf_counter() - t0_init
    print(f"Modelos carregados e aquecidos na GPU em {init_duration:.2f}s!\n")

    # Medição Whisper (3 passadas)
    print(">>> 1. Medição do Faster-Whisper (Transcrição de Fala na GPU):")
    whisper_times = []
    transcription = ""
    for i in range(3):
        t0 = time.perf_counter()
        segments, _ = model.transcribe(
            audio_path,
            language="pt",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            initial_prompt=TECH_PROMPT,
            without_timestamps=True,
            vad_filter=True,
        )
        transcription = " ".join(s.text.strip() for s in segments)
        t_w = time.perf_counter() - t0
        whisper_times.append(t_w)
        print(f"    Passada #{i+1}: {t_w:.3f}s")

    avg_whisper = sum(whisper_times) / len(whisper_times)
    speed_x = duration / avg_whisper if avg_whisper > 0 else 0

    print()
    print(f"  Tempo Médio Whisper : {avg_whisper:.3f}s")
    print(f"  Fator Tempo Real    : {speed_x:.1f}x mais rápido que tempo real")
    print(f"  Texto Transcrito    : \"{transcription}\"")
    print()

    # Medição EmbeddedLLM
    print(">>> 2. Medição do EmbeddedLLM (Reescrita In-Process GGUF na GPU):")
    embedded_times = []
    final_prompt = ""
    for i in range(3):
        t0 = time.perf_counter()
        final_prompt = embedded_llm.rewrite_prompt(transcription)
        t_emb = time.perf_counter() - t0
        embedded_times.append(t_emb)
        print(f"    Passada #{i+1}: {t_emb:.3f}s")

    avg_embedded = sum(embedded_times) / len(embedded_times)
    print()
    print(f"  Tempo Médio EmbeddedLLM : {avg_embedded:.3f}s")
    print(f"  Prompt Final Refinado   : \"{final_prompt}\"")
    print()

    # Comparação Opcional com Ollama se estiver respondendo
    ollama_active = False
    avg_ollama = None
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as res:
            if res.status == 200:
                ollama_active = True
    except Exception:
        ollama_active = False

    if ollama_active:
        print(">>> 3. Comparativo com Servidor Ollama Legado:")
        try:
            ollama_times = []
            for i in range(3):
                t0 = time.perf_counter()
                call_ollama(transcription)
                t_o = time.perf_counter() - t0
                ollama_times.append(t_o)
                print(f"    Passada #{i+1} (Ollama): {t_o:.3f}s")
            avg_ollama = sum(ollama_times) / len(ollama_times)
            speedup = avg_ollama / avg_embedded if avg_embedded > 0 else 0
            print(f"  Tempo Médio Ollama : {avg_ollama:.3f}s")
            print(f"  Ganho de Velocidade: EmbeddedLLM é {speedup:.1f}x MAIS RÁPIDO que o Ollama!")
            print()
        except Exception as e:
            print(f"  Aviso ao testar Ollama: {e}\n")

    # Latência Total Ponta a Ponta
    total_time = avg_whisper + avg_embedded
    print("=" * 70)
    print("RESULTADO FINAL: LATÊNCIA PONTA A PONTA (FALA -> PROMPT REFINADO)")
    print("=" * 70)
    print(f"  Duração da Gravação       : {duration:.2f}s")
    print(f"  Transcrição Whisper       : {avg_whisper:.3f}s  ({(avg_whisper/total_time)*100:.1f}%)")
    print(f"  Reescrita EmbeddedLLM     : {avg_embedded:.3f}s  ({(avg_embedded/total_time)*100:.1f}%)")
    print(f"  Tempo Total de Espera     : {total_time:.3f}s")
    print(f"  Taxa de Aceleração Total  : {duration / total_time:.1f}x mais rápido que o áudio falado!")
    if avg_ollama:
        print(f"  Tempo Total com Ollama    : {avg_whisper + avg_ollama:.3f}s (Economia: {(avg_whisper + avg_ollama) - total_time:.2f}s!)")
    print("=" * 70)


if __name__ == "__main__":
    audio = sys.argv[1] if len(sys.argv) > 1 else "data/samples/exemplo_comando.wav"
    run_benchmark(audio)
