"""Benchmark completo de velocidade: Áudio -> Whisper (GPU) -> Ollama (LLM) -> Prompt."""

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

from faster_whisper import WhisperModel

# Injeta DLLs da NVIDIA no PATH no Windows
site_packages = Path(sys.prefix) / "Lib" / "site-packages"
for nvidia_bin in site_packages.glob("nvidia/**/bin"):
    if nvidia_bin.is_dir():
        os.environ["PATH"] = str(nvidia_bin) + os.pathsep + os.environ["PATH"]
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(nvidia_bin))
            except OSError:
                pass

TECH_PROMPT = "Python, Docker, FastAPI, SQL, PostgreSQL, PyTorch, pandas, DataFrame, Ollama, Whisper, API REST"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
FAST_OLLAMA_MODEL = os.getenv("LIVE_OLLAMA_MODEL", "qwen2.5:3b")


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
    print(f"  Modelo de Fala     : Faster-Whisper 'medium' (Float16 CUDA)")
    print(f"  Modelo de LLM      : Ollama '{FAST_OLLAMA_MODEL}' (Q4_K_M na VRAM)")
    print("-" * 70)

    print("Inicializando modelos na VRAM...")
    model = WhisperModel("medium", device="cuda", compute_type="float16")

    # Aquecimento completo da VRAM com o áudio real
    _warm_segments, _ = model.transcribe(audio_path, language="pt", beam_size=1, without_timestamps=True)
    list(_warm_segments)
    try:
        call_ollama("teste de aquecimento")
    except Exception:
        pass
    print("GPU pronta e aquecida!\n")

    # Medição Whisper (3 passadas estáveis)
    print(">>> 1. Medição do Faster-Whisper (Transcrição de Fala na GPU):")
    whisper_times = []
    transcription = ""
    for i in range(3):
        t0 = time.perf_counter()
        segments, info = model.transcribe(
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
    rtf = avg_whisper / duration
    speed_x = duration / avg_whisper

    print()
    print(f"  Tempo Médio Whisper : {avg_whisper:.3f}s")
    print(f"  Fator Tempo Real    : {speed_x:.1f}x mais rápido que tempo real (RTF = {rtf:.4f})")
    print(f"  Texto Transcrito    : \"{transcription}\"")
    print()

    # Medição Ollama
    print(">>> 2. Medição do Ollama (Reescrita Estruturada do Prompt):")
    llm_times = []
    final_prompt = ""
    for i in range(3):
        t0 = time.perf_counter()
        final_prompt = call_ollama(transcription)
        t_llm = time.perf_counter() - t0
        llm_times.append(t_llm)
        print(f"    Passada #{i+1}: {t_llm:.3f}s")

    avg_llm = sum(llm_times) / len(llm_times)
    print()
    print(f"  Tempo Médio Ollama  : {avg_llm:.3f}s")
    print(f"  Prompt Final Refinado: \"{final_prompt}\"")
    print()

    # Latência Total Ponta a Ponta
    total_time = avg_whisper + avg_llm
    print("=" * 70)
    print("RESULTADO FINAL: LATÊNCIA PONTA A PONTA (FALA -> CLIPBOARD)")
    print("=" * 70)
    print(f"  Duração da Gravação       : {duration:.2f}s")
    print(f"  Transcrição Whisper       : {avg_whisper:.3f}s  ({(avg_whisper/total_time)*100:.1f}%)")
    print(f"  Reescrita Ollama          : {avg_llm:.3f}s  ({(avg_llm/total_time)*100:.1f}%)")
    print(f"  Tempo Total de Espera     : {total_time:.3f}s")
    print(f"  Taxa de Aceleração Total  : {duration / total_time:.1f}x mais rápido que o áudio falado!")
    print("=" * 70)


if __name__ == "__main__":
    audio = sys.argv[1] if len(sys.argv) > 1 else "data/samples/exemplo_comando.wav"
    run_benchmark(audio)
