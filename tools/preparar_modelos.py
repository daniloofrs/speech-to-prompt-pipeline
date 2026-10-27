"""Script de Pré-carregamento e Verificação Atômica dos Modelos de IA.

Verifica e prepara:
1. Limpeza de arquivos temporários (.incomplete) no cache do Hugging Face.
2. Download e validação atômica do modelo Faster-Whisper (Systran/faster-whisper-medium) na GPU.
3. Disponibilidade do servidor Ollama e inferência de teste com qwen2.5:3b na GPU.
4. Diagnóstico de prontidão: [PASS] Modelos prontos na GPU.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Se executado no Python global (sem ativar o venv), reinicia automaticamente usando o venv do projeto
_project_root = Path(__file__).resolve().parent.parent
_venv_python = _project_root / "venv" / "Scripts" / "python.exe"
if _venv_python.exists() and Path(sys.executable).resolve() != _venv_python.resolve():
    sys.exit(subprocess.call([str(_venv_python)] + sys.argv))

# Injeta DLLs da NVIDIA no PATH no Windows para CTranslate2 / CUDA
site_packages = Path(sys.prefix) / "Lib" / "site-packages"
for nvidia_bin in site_packages.glob("nvidia/**/bin"):
    if nvidia_bin.is_dir():
        os.environ["PATH"] = str(nvidia_bin) + os.pathsep + os.environ["PATH"]
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(nvidia_bin))
            except OSError:
                pass

# Adiciona raiz do projeto ao sys.path para importar src.config
sys.path.insert(0, str(_project_root))

from faster_whisper import WhisperModel
import huggingface_hub
from huggingface_hub import hf_hub_download, snapshot_download
from huggingface_hub.constants import HF_HUB_CACHE
from src.config import (
    BASE_DIR,
    EMBEDDED_MODEL_PATH,
    LLM_BACKEND,
    LIVE_OLLAMA_MODEL,
    OLLAMA_HOST,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)
from src.embedded_llm import EmbeddedLLM

REQUIRED_SNAPSHOT_FILES = [
    "config.json",
    "tokenizer.json",
    "vocabulary.txt",
    "model.bin",
]


def cleanup_incomplete_cache(repo_id: str = "Systran/faster-whisper-medium") -> tuple[int, int]:
    """Remove arquivos *.incomplete deixados por downloads interrompidos no cache HF."""
    repo_folder_name = "models--" + repo_id.replace("/", "--")
    cache_root = Path(HF_HUB_CACHE)
    model_dir = cache_root / repo_folder_name
    blobs_dir = model_dir / "blobs"

    removed_count = 0
    freed_bytes = 0

    if model_dir.exists():
        for incomplete_file in model_dir.glob("**/*.incomplete"):
            try:
                size = incomplete_file.stat().st_size
                incomplete_file.unlink()
                removed_count += 1
                freed_bytes += size
                print(f"  [REMOVIDO] {incomplete_file.relative_to(model_dir)} ({size / (1024 * 1024):.1f} MB)")
            except OSError as e:
                print(f"  [AVISO] Falha ao remover {incomplete_file.name}: {e}")

    return removed_count, freed_bytes


def verify_snapshot_integrity(snapshot_path: Path) -> dict[str, int]:
    """Verifica a presença e integridade dos arquivos obrigatórios no snapshot."""
    missing = []
    file_sizes = {}
    for filename in REQUIRED_SNAPSHOT_FILES:
        target = snapshot_path / filename
        if not target.exists() or target.stat().st_size == 0:
            missing.append(filename)
        else:
            file_sizes[filename] = target.stat().st_size

    if missing:
        raise FileNotFoundError(
            f"Snapshot incompleto em {snapshot_path}. Arquivos ausentes ou vazios: {', '.join(missing)}"
        )

    return file_sizes


def prepare_whisper(
    model_size: str = WHISPER_MODEL,
    device: str = WHISPER_DEVICE,
    compute_type: str = WHISPER_COMPUTE_TYPE,
) -> WhisperModel:
    """Baixa arquivos do Faster-Whisper, valida integridade e carrega na GPU."""
    repo_id = f"Systran/faster-whisper-{model_size}"
    print(f"\n[1/3] Verificando e baixando Faster-Whisper '{model_size}' ({repo_id})...")

    # Download atômico com barra de progresso via Hugging Face Hub
    snapshot_dir = snapshot_download(
        repo_id=repo_id,
        allow_patterns=[
            "config.json",
            "preprocessor_config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.*",
        ],
    )
    snapshot_path = Path(snapshot_dir)
    print(f"[OK] Diretório do modelo verificado: {snapshot_path}")

    # Validação de integridade atômica
    file_sizes = verify_snapshot_integrity(snapshot_path)
    for fname, size in file_sizes.items():
        print(f"  - {fname}: {size / (1024 * 1024):.2f} MB")

    # Instanciação na GPU via faster_whisper.WhisperModel
    print(f"[INFO] Instanciando WhisperModel('{model_size}', device='{device}', compute_type='{compute_type}')...")
    t0 = time.perf_counter()
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    load_time = time.perf_counter() - t0
    print(f"[OK] WhisperModel('{model_size}') pronto na GPU ({device} / {compute_type}) em {load_time:.2f}s!")
    return model


def prepare_embedded_llm(model_path_str: str = EMBEDDED_MODEL_PATH) -> EmbeddedLLM:
    """Garante que o modelo GGUF esteja na pasta models/ e realiza teste de inferência na GPU."""
    model_path = Path(model_path_str)
    if not model_path.is_absolute():
        model_path = BASE_DIR / model_path

    model_dir = model_path.parent
    model_dir.mkdir(parents=True, exist_ok=True)

    filename = model_path.name
    repo_id = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"

    print(f"\n[2/3] Verificando e preparando LLM local embutido ({filename})...")

    # Se o arquivo não existir ou for menor que 500 MB (corrompido/incompleto)
    if not model_path.exists() or model_path.stat().st_size < 500 * 1024 * 1024:
        print(f"[INFO] Baixando modelo GGUF de '{repo_id}' para '{model_dir}'...")
        downloaded_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(model_dir),
        )
        print(f"[OK] Download concluído com sucesso: {downloaded_path}")
    else:
        size_mb = model_path.stat().st_size / (1024 * 1024)
        print(f"[OK] Arquivo GGUF existente validado: {model_path.name} ({size_mb:.1f} MB)")

    # Carrega e valida inferência na GPU
    print("[INFO] Instanciando EmbeddedLLM na GPU RTX...")
    t0 = time.perf_counter()
    llm = EmbeddedLLM(model_path=str(model_path))
    if not llm.load_model():
        raise RuntimeError("Falha ao inicializar o motor llama-cpp na GPU.")

    load_time = time.perf_counter() - t0
    print(f"[OK] EmbeddedLLM carregado na VRAM em {load_time:.2f}s!")

    # Teste de inferência
    print("[INFO] Executando inferência de teste com EmbeddedLLM...")
    t1 = time.perf_counter()
    test_prompt = llm.rewrite_prompt("cria uma funcao em python pra ordenar uma lista com quicksort")
    inf_time = time.perf_counter() - t1
    print(f"[OK] Prompt refinado gerado em {inf_time:.2f}s:")
    print(f"     -> \"{test_prompt}\"")

    return llm


def ensure_ollama_running(host: str = OLLAMA_HOST, timeout_seconds: int = 15) -> bool:
    """Verifica se o servidor Ollama está ativo ou tenta iniciá-lo."""
    endpoint = f"{host.rstrip('/')}/api/tags"

    def is_alive() -> bool:
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    if is_alive():
        return True

    print(f"[INFO] Servidor Ollama não respondeu em {host}. Tentando iniciar 'ollama serve'...")
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as e:
        print(f"[AVISO] Não foi possível iniciar automaticamente 'ollama serve': {e}")
        return False

    # Polling até responder
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        time.sleep(1)
        if is_alive():
            print("[OK] Servidor Ollama iniciado com sucesso!")
            return True

    return False


def verify_ollama_model(model_name: str = LIVE_OLLAMA_MODEL, host: str = OLLAMA_HOST) -> bool:
    """Verifica se o Ollama possui o modelo e realiza um teste de inferência rápida."""
    print(f"\n[2/3] Verificando serviço Ollama e modelo '{model_name}'...")
    if not ensure_ollama_running(host):
        print(f"[FALHA] Ollama não está respondendo em {host}.")
        return False

    print(f"[OK] Conectado ao Ollama em {host}")

    # Verificar se o modelo está listado
    endpoint_tags = f"{host.rstrip('/')}/api/tags"
    req = urllib.request.Request(endpoint_tags, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode())
        installed_models = [m.get("name") for m in data.get("models", [])]

    model_matched = any(model_name in m for m in installed_models)
    if not model_matched:
        print(f"[AVISO] Modelo '{model_name}' não encontrado nos modelos instalados: {installed_models}")
        print(f"[INFO] Baixando '{model_name}' via 'ollama pull'...")
        res = subprocess.run(["ollama", "pull", model_name])
        if res.returncode != 0:
            print(f"[FALHA] Falha ao baixar '{model_name}' via Ollama.")
            return False

    # Teste de inferência rápida
    print(f"[INFO] Executando inferência de aquecimento com '{model_name}'...")
    endpoint_chat = f"{host.rstrip('/')}/api/chat"
    payload = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint_chat,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as resp:
        chat_resp = json.loads(resp.read().decode())
        duration = time.perf_counter() - t0
        content = chat_resp.get("message", {}).get("content", "").strip()
        print(f"[OK] Resposta recebida em {duration:.2f}s: \"{content[:60]}...\"")

    return True


def main() -> None:
    print("=" * 70)
    print("  Speech-to-Prompt Pipeline: Preparação e Verificação dos Modelos")
    print(f"  Backend LLM Ativo: {LLM_BACKEND.upper()}")
    print("=" * 70)

    # Fase 0: Limpeza do cache
    repo_id = f"Systran/faster-whisper-{WHISPER_MODEL}"
    print(f"\n[0/3] Limpeza de arquivos corrompidos/incompletos no cache HF ({repo_id})...")
    removed_count, freed_bytes = cleanup_incomplete_cache(repo_id)
    if removed_count > 0:
        print(
            f"[OK] Limpeza concluída: {removed_count} arquivo(s) .incomplete removido(s) "
            f"({freed_bytes / (1024 * 1024):.1f} MB liberados)."
        )
    else:
        print("[OK] Cache limpo: nenhum arquivo .incomplete encontrado.")

    # Fase 1: Whisper
    try:
        prepare_whisper()
    except Exception as e:
        print(f"\n[FALHA] Erro ao carregar/verificar Faster-Whisper: {e}")
        sys.exit(1)

    # Fase 2: LLM (Embutido, Ollama ou Direto)
    if LLM_BACKEND == "embedded":
        try:
            prepare_embedded_llm()
        except Exception as e:
            print(f"\n[FALHA] Erro ao carregar/verificar EmbeddedLLM: {e}")
            sys.exit(1)
    elif LLM_BACKEND == "ollama":
        try:
            ollama_ok = verify_ollama_model()
            if not ollama_ok:
                print("\n[FALHA] Falha na verificação do Ollama.")
                sys.exit(1)
        except Exception as e:
            print(f"\n[FALHA] Erro ao verificar Ollama: {e}")
            sys.exit(1)
    else:
        print(f"\n[2/3] LLM_BACKEND='{LLM_BACKEND}' ativo (Whisper puro sem LLM). Etapa concluída.")

    print("\n" + "=" * 70)
    print("[PASS] Modelos prontos na GPU")
    print("=" * 70)


if __name__ == "__main__":
    main()
