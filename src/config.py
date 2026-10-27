"""Configuracoes do pipeline carregadas a partir de variaveis de ambiente (.env)."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega o arquivo .env a partir da raiz do projeto
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Configuracoes do PostgreSQL
POSTGRES_DB = os.getenv("POSTGRES_DB", "speech_pipeline")
POSTGRES_USER = os.getenv("POSTGRES_USER", "pipeline_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "pipeline_password")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))

# Configuracoes do Whisper
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")

# Configuracoes do Ollama (LLM Legado)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
LIVE_OLLAMA_MODEL = os.getenv("LIVE_OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))

# Backend do LLM: 'embedded' (GGUF in-process na GPU), 'direct' (Whisper puro sem LLM) ou 'ollama' (legado HTTP)
LLM_BACKEND = os.getenv("LLM_BACKEND", "embedded").lower()

# Configuracoes do LLM Embutido (llama-cpp-python + GGUF)
EMBEDDED_MODEL_PATH = os.getenv("EMBEDDED_MODEL_PATH", str(BASE_DIR / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"))
EMBEDDED_N_GPU_LAYERS = int(os.getenv("EMBEDDED_N_GPU_LAYERS", "-1"))
EMBEDDED_N_CTX = int(os.getenv("EMBEDDED_N_CTX", "512"))
EMBEDDED_MAX_TOKENS = int(os.getenv("EMBEDDED_MAX_TOKENS", "128"))

# Configuracoes de Usabilidade da Pílula Live
LIVE_HOTKEY = os.getenv("LIVE_HOTKEY", "F8")
LIVE_WHISPER_MODEL = os.getenv("LIVE_WHISPER_MODEL", WHISPER_MODEL)
LIVE_WHISPER_BEAM_SIZE = int(os.getenv("LIVE_WHISPER_BEAM_SIZE", "1"))

# Detecção Automática de Silêncio (Hands-Free VAD)
AUTO_STOP_ON_SILENCE = os.getenv("AUTO_STOP_ON_SILENCE", "true").lower() == "true"
SILENCE_TIMEOUT_S = float(os.getenv("SILENCE_TIMEOUT_S", "3.0"))
SILENCE_THRESHOLD_RMS = float(os.getenv("SILENCE_THRESHOLD_RMS", "0.008"))
MAX_INITIAL_SILENCE_S = float(os.getenv("MAX_INITIAL_SILENCE_S", "6.0"))


def get_db_connection_uri() -> str:
    """Retorna a URI de conexao para o PostgreSQL."""
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
