"""Motor de Inferência LLM Local Embutido (llama-cpp-python + GGUF).

Executa inferência in-process 100% dentro do Python usando CUDA na GPU,
eliminando a necessidade de qualquer servidor externo (como Ollama) ou portas de rede.
"""

import glob
import logging
import os
import re
import site
import sys
import threading
from pathlib import Path
from typing import Any, List, Optional

# Configura logs
logger = logging.getLogger("embedded_llm")

# Armazena referências aos handles _AddedDllDirectory no Windows para evitar garbage collection
_DLL_HANDLES: List[Any] = []


def _setup_cuda_dlls() -> None:
    """Garante que as DLLs da NVIDIA e do llama_cpp sejam encontradas no Windows."""
    if sys.platform != "win32":
        return

    candidate_paths = []

    # 1. Busca em site.getsitepackages()
    try:
        sp_list = site.getsitepackages()
    except Exception:
        sp_list = []

    for sp in sp_list:
        llama_lib = os.path.join(sp, "llama_cpp", "lib")
        if os.path.exists(llama_lib):
            candidate_paths.append(llama_lib)
        for nb in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            if os.path.exists(nb):
                candidate_paths.append(nb)

    # 2. Busca também em sys.path (site-packages do venv ativo)
    for p in sys.path:
        if "site-packages" in p:
            llama_lib = os.path.join(p, "llama_cpp", "lib")
            if os.path.exists(llama_lib) and llama_lib not in candidate_paths:
                candidate_paths.append(llama_lib)
            for nb in glob.glob(os.path.join(p, "nvidia", "*", "bin")):
                if os.path.exists(nb) and nb not in candidate_paths:
                    candidate_paths.append(nb)

    for path in candidate_paths:
        try:
            handle = os.add_dll_directory(path)
            _DLL_HANDLES.append(handle)
        except Exception:
            pass
        if path not in os.environ.get("PATH", ""):
            os.environ["PATH"] = path + ";" + os.environ.get("PATH", "")


# Prepara as DLLs antes da importação da biblioteca C
_setup_cuda_dlls()

try:
    import llama_cpp
    from llama_cpp import Llama
    _LLAMA_AVAILABLE = True
except Exception as _import_err:
    llama_cpp = None
    Llama = None
    _LLAMA_AVAILABLE = False
    logger.debug(f"llama_cpp não importado: {_import_err}")

from src.config import (
    BASE_DIR,
    EMBEDDED_MAX_TOKENS,
    EMBEDDED_MODEL_PATH,
    EMBEDDED_N_CTX,
    EMBEDDED_N_GPU_LAYERS,
)

SYSTEM_PROMPT = (
    "Você é um especialista em Prompt Engineering. "
    "Sua tarefa é transformar a transcrição de fala do usuário em um prompt direto, claro e profissional para uma IA.\n"
    "Regras:\n"
    "1. Retorne APENAS o comando imperativo refinado (ex: 'Crie uma função...', 'Escreva uma query...').\n"
    "2. Seja direto e objetivo, sem enrolação.\n"
    "3. Não inclua introduções, explicações, saudações, títulos, notas ou aspas."
)


class EmbeddedLLM:
    """Executor in-process thread-safe do modelo GGUF via llama-cpp-python."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        n_gpu_layers: Optional[int] = None,
        n_ctx: Optional[int] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        self.model_path = Path(model_path or EMBEDDED_MODEL_PATH)
        if not self.model_path.is_absolute():
            self.model_path = BASE_DIR / self.model_path

        self.n_gpu_layers = n_gpu_layers if n_gpu_layers is not None else EMBEDDED_N_GPU_LAYERS
        self.n_ctx = n_ctx if n_ctx is not None else EMBEDDED_N_CTX
        self.max_tokens = max_tokens if max_tokens is not None else EMBEDDED_MAX_TOKENS
        self._llm: Optional[Llama] = None
        self._is_loaded = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """Verifica se a biblioteca llama-cpp está instalada e o arquivo .gguf existe no disco."""
        return _LLAMA_AVAILABLE and self.model_path.is_file()

    def load_model(self) -> bool:
        """Carrega o modelo GGUF diretamente na VRAM da GPU de forma thread-safe."""
        with self._lock:
            if self._is_loaded and self._llm is not None:
                return True

            if not _LLAMA_AVAILABLE:
                logger.warning("llama-cpp-python não está disponível no ambiente.")
                return False

            if not self.model_path.is_file():
                logger.warning(f"Arquivo de modelo GGUF não encontrado em: {self.model_path}")
                return False

            try:
                # Verifica suporte a aceleração GPU
                if hasattr(llama_cpp, "llama_supports_gpu_offload"):
                    gpu_supported = llama_cpp.llama_supports_gpu_offload()
                    if not gpu_supported:
                        logger.warning("llama-cpp não possui suporte a GPU offload compilado; rodará em CPU!")

                logger.info(f"Carregando modelo GGUF in-process na GPU: {self.model_path.name}")
                self._llm = Llama(
                    model_path=str(self.model_path),
                    n_gpu_layers=self.n_gpu_layers,
                    n_ctx=self.n_ctx,
                    n_threads=4,
                    verbose=False,
                )
                self._is_loaded = True
                logger.info(f"Modelo GGUF {self.model_path.name} carregado com sucesso na GPU!")
                return True
            except Exception as exc:
                logger.error(f"Falha ao carregar modelo GGUF {self.model_path}: {exc}")
                self._llm = None
                self._is_loaded = False
                return False

    def warmup(self) -> None:
        """Executa um aquecimento rápido para compilar grafos CUDA na memória."""
        with self._lock:
            if not self._is_loaded or self._llm is None:
                return
            try:
                self._llm.create_chat_completion(
                    messages=[{"role": "user", "content": "olá"}],
                    max_tokens=1,
                    temperature=0.0,
                )
            except Exception as exc:
                logger.debug(f"Aquecimento do modelo GGUF: {exc}")

    def rewrite_prompt(self, raw_text: str) -> str:
        """Reescreve o texto cru da transcrição em um prompt imperativo de alta qualidade (Thread-Safe)."""
        raw_text = (raw_text or "").strip()
        if not raw_text:
            return ""

        if not self._is_loaded:
            success = self.load_model()
            if not success or self._llm is None:
                return raw_text

        with self._lock:
            try:
                response = self._llm.create_chat_completion(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": raw_text},
                    ],
                    temperature=0.0,
                    max_tokens=self.max_tokens,
                    stop=["\n\n", "Nota:", "Observação:", "Explicação:", "DÚVIDAS:", "User:", "Usuário:"],
                )

                choices = response.get("choices", [])
                if not choices:
                    return raw_text

                content = choices[0].get("message", {}).get("content", "").strip()

                # Remove prefixos indesejados via regex robusto (case-insensitive, com ou sem markdown/negrito)
                content = re.sub(
                    r"^[\*\s#_]*(?:pedido|prompt|comando|instrução)[\*\s#_]*:[\*\s#_]*",
                    "",
                    content,
                    flags=re.IGNORECASE,
                ).strip()

                # Remove aspas ao redor se a LLM colocou
                if (content.startswith('"') and content.endswith('"')) or (content.startswith("'") and content.endswith("'")):
                    content = content[1:-1].strip()

                return content or raw_text

            except Exception as exc:
                logger.warning(f"Erro durante reescrita com modelo GGUF embutido: {exc}")
                return raw_text
