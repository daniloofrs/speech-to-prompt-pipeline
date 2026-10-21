"""Script de reescrita de transcricao com LLM local via Ollama (Etapa 2).

Fluxo:
1. Obtem a transcricao salva no PostgreSQL (por ID ou a mais recente).
2. Envia a transcricao crua para o Ollama (qwen2.5:7b, temperature 0.1) com system prompt restritivo.
3. Extrai as secoes 'PEDIDO' e 'DUVIDAS' da resposta.
4. Salva o resultado na tabela 'prompts' com FK para 'transcricoes'.
5. Exibe no terminal a comparacao lado a lado entre o texto cru e o pedido reescrito.
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

# Garante que a raiz do projeto esteja no sys.path mesmo se executado diretamente como script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Garante saida em UTF-8 no terminal Windows para acentuacao correta
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg

from src.config import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
)
from src.database import (
    get_latest_transcription,
    get_transcription_by_id,
    init_db,
    insert_prompt,
)

# Configuracao de logs padronizada para terminal
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rewrite")

# System prompt exato definido para reescrita confiavel sem alucinacoes
SYSTEM_PROMPT = """Você recebe a transcrição de uma fala informal em português. Reescreva-a como um pedido claro e direto para uma IA.
Regras:
1. Use SOMENTE informações presentes na fala. Nunca invente detalhes, nomes, tecnologias ou requisitos.
2. Mantenha os termos técnicos exatamente como foram ditos.
3. Remova hesitações, repetições e voltas atrás. Se a pessoa se corrigiu, use a versão final.
4. Se algo estiver ambíguo ou faltando, NÃO preencha: liste em "Dúvidas".
5. Não responda ao pedido, apenas reescreva.
Formato da resposta:
PEDIDO: <texto reescrito, na primeira pessoa>
DÚVIDAS: <lista do que ficou ambíguo ou faltando, ou "nenhuma">"""


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


def call_ollama(
    text: str,
    model: str = OLLAMA_MODEL,
    temperature: float = OLLAMA_TEMPERATURE,
) -> str:
    """Chama a API do Ollama enviando o texto da transcricao com auto-start silencioso."""
    endpoint = f"{OLLAMA_HOST.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "options": {
            "temperature": temperature,
        },
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
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("message", {}).get("content", "").strip()

    try:
        logger.info(
            f"Chamando Ollama ({model}) em {OLLAMA_HOST} com temperature={temperature}..."
        )
        return _execute()
    except (urllib.error.URLError, ConnectionError):
        # Se o Ollama estava desligado, inicia silenciosamente e retenta
        if ensure_ollama_running():
            return _execute()
        logger.error(
            f"Erro ao conectar ao Ollama ({OLLAMA_HOST}). O serviço do Ollama está instalado?"
        )
        raise


def parse_llm_response(response_text: str) -> Tuple[str, str]:
    """Extrai os blocos 'PEDIDO:' e 'DUVIDAS:' do retorno do modelo."""
    pattern = re.compile(
        r"PEDIDO:\s*(.*?)(?:\n\s*D[ÚU]VIDAS:\s*(.*))?$",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(response_text.strip())

    if match:
        pedido = match.group(1).strip()
        duvidas = match.group(2).strip() if match.group(2) else "nenhuma"
    else:
        # Fallback se o modelo nao seguiu rigidamente a formatacao
        pedido = response_text.strip()
        duvidas = "nenhuma"

    return pedido, duvidas


def display_side_by_side(
    raw_text: str, pedido: str, duvidas: str, col_width: int = 44
) -> None:
    """Exibe no terminal a transcricao original e o pedido reescrito lado a lado."""
    raw_display = raw_text if raw_text.strip() else "(Transcricao vazia)"
    pedido_display = pedido if pedido.strip() else "(Pedido vazio)"

    left_lines = textwrap.wrap(raw_display, width=col_width)
    right_lines = textwrap.wrap(pedido_display, width=col_width)

    max_lines = max(len(left_lines), len(right_lines))
    left_lines += [""] * (max_lines - len(left_lines))
    right_lines += [""] * (max_lines - len(right_lines))

    total_width = col_width * 2 + 5
    border = "=" * total_width

    print("\n" + border)
    print(f"{'TRANSCRIÇÃO CRUA':<{col_width}} | {'PEDIDO REESCRITO (LLM)':<{col_width}}")
    print(border)
    for left, right in zip(left_lines, right_lines):
        print(f"{left:<{col_width}} | {right:<{col_width}}")
    print(border)
    print(f"DÚVIDAS LISTADAS: {duvidas}")
    print(border + "\n")


def run_rewrite(transcription_id: Optional[int] = None) -> None:
    """Executa a etapa de reescrita a partir do banco de dados."""
    # 1. Garante que as tabelas existam
    try:
        init_db()
    except psycopg.OperationalError:
        logger.error(
            "Conexao recusada pelo PostgreSQL! O container esta ativo? Rode: docker compose up -d"
        )
        sys.exit(1)

    # 2. Localiza a transcricao alvo
    if transcription_id is not None:
        record = get_transcription_by_id(transcription_id)
        if not record:
            logger.error(f"Transcricao com ID {transcription_id} nao encontrada no banco.")
            sys.exit(1)
    else:
        record = get_latest_transcription()
        if not record:
            logger.error(
                "Nenhuma transcricao encontrada no banco. Execute primeiro a Etapa 1: python src/ingest.py <audio>"
            )
            sys.exit(1)

    transc_id = record["id"]
    raw_text = record["texto"]
    file_name = record["arquivo"]

    logger.info(
        f"Processando transcricao ID {transc_id} (Arquivo: '{file_name}', Tamanho texto: {len(raw_text)} chars)"
    )

    # 3. Chama o LLM via Ollama
    try:
        llm_output = call_ollama(raw_text)
    except Exception as exc:
        logger.error(f"Falha ao gerar reescrita com LLM: {exc}")
        sys.exit(1)

    # 4. Processa a resposta
    pedido, duvidas = parse_llm_response(llm_output)

    # 5. Salva na tabela 'prompts'
    try:
        prompt_id = insert_prompt(
            transcription_id=transc_id,
            pedido=pedido,
            duvidas=duvidas,
            model_name=OLLAMA_MODEL,
        )
        logger.info(
            f"SUCESSO: Prompt salvo com ID {prompt_id} vinculado a transcricao ID {transc_id}."
        )
    except psycopg.OperationalError:
        logger.error("Erro ao gravar prompt no banco de dados.")
        sys.exit(1)

    # 6. Exibe a comparacao lado a lado no terminal
    display_side_by_side(raw_text=raw_text, pedido=pedido, duvidas=duvidas)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reescreve transcricoes de fala em pedidos claros para IA usando LLM local (Ollama)."
    )
    parser.add_argument(
        "--id",
        type=int,
        default=None,
        help="ID da transcricao a ser reescrita. Se omitido, utiliza a mais recente cadastrada.",
    )

    args = parser.parse_args()
    run_rewrite(transcription_id=args.id)


if __name__ == "__main__":
    main()
