"""Script de ingestao e transcricao de audio (Etapa 1).

Fluxo:
1. Valida se o arquivo de audio existe.
2. Calcula o hash SHA-256 do arquivo (chave unica de deduplicacao).
3. Conecta ao PostgreSQL (garante idempotencia e trata erros se o banco estiver offline).
4. Transcreve o audio em portugues usando faster-whisper (com aceleracao CUDA na RTX 4050).
5. Salva na tabela 'transcricoes' usando ON CONFLICT (hash_audio) DO NOTHING.
"""

import argparse
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Tuple

# Garante que a raiz do projeto esteja no sys.path mesmo se executado diretamente como script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# No Windows, adiciona os diretorios de DLLs da NVIDIA instaladas via pip ao PATH
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

import psycopg
from faster_whisper import WhisperModel

from src.config import (
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)
from src.database import find_by_hash, init_db, insert_transcription

# Configuracao de logs padronizada para terminal
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ingest")


def compute_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    """Calcula o hash SHA-256 de um arquivo lendo em blocos.
    
    A leitura em blocos (streaming) evita carregar arquivos grandes inteiros na memoria RAM.
    """
    hasher = hashlib.sha256()
    with open(file_path, "rb") as audio_file:
        while chunk := audio_file.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_whisper_model() -> WhisperModel:
    """Inicializa o modelo faster-whisper com fallback gracioso para CPU."""
    try:
        logger.info(
            f"Carregando Whisper (modelo: {WHISPER_MODEL}, device: {WHISPER_DEVICE}, compute_type: {WHISPER_COMPUTE_TYPE})..."
        )
        return WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    except Exception as error:
        if WHISPER_DEVICE == "cuda":
            logger.warning(
                f"Nao foi possivel carregar no dispositivo CUDA ({error}). Tentando fallback para CPU (int8)..."
            )
            return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        raise error


def transcribe_audio(
    model: WhisperModel, audio_path: Path
) -> Tuple[str, str, float]:
    """Transcreve o arquivo de audio usando faster-whisper em portugues (pt).
    
    Retorna uma tupla contendo (texto_transcrito, idioma_detectado, duracao_segundos).
    """
    logger.info(f"Iniciando transcricao do arquivo: {audio_path.name}")
    try:
        segments, info = model.transcribe(
            str(audio_path),
            language="pt",
            beam_size=5,
        )

        collected_text = []
        for segment in segments:
            collected_text.append(segment.text.strip())

        full_text = " ".join(collected_text).strip()
        detected_lang = info.language or "pt"
        duration = info.duration or 0.0

    except Exception as exc:
        # Se ocorrer erro em execucao na GPU (ex: cublas faltando), tenta fallback em CPU
        if model.model.device == "cuda":
            logger.warning(f"Erro durante execucao no CUDA ({exc}). Reexecutando transcricao em CPU...")
            cpu_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            return transcribe_audio(cpu_model, audio_path)
        raise exc

    logger.info(
        f"Transcricao concluida: {len(full_text)} caracteres | Duracao: {duration:.2f}s | Idioma: {detected_lang}"
    )
    return full_text, detected_lang, duration


def run_ingestion(audio_file_path: str, force: bool = False) -> None:
    """Executa o fluxo completo de ingestao com validacoes de erro e idempotencia."""
    path = Path(audio_file_path).resolve()

    # 1. Validacao do arquivo de entrada
    if not path.is_file():
        logger.error(f"Arquivo de audio nao encontrado: '{path}'")
        sys.exit(1)

    logger.info(f"Processando arquivo: {path.name} ({path.stat().st_size / 1024:.1f} KB)")

    # 2. Calculo do Hash SHA-256
    audio_hash = compute_sha256(path)
    logger.info(f"Hash SHA-256 do arquivo: {audio_hash}")

    # 3. Validacao de conexao com o banco de dados
    try:
        init_db()
    except psycopg.OperationalError:
        logger.error(
            "Conexao recusada pelo PostgreSQL! O container esta ativo? Rode: docker compose up -d"
        )
        sys.exit(1)

    # 4. Verificacao de idempotencia (se ja existe, evitamos gasto inutil de GPU)
    existing_record = find_by_hash(audio_hash)
    if existing_record and not force:
        logger.warning(
            f"IDEMPOTENCIA: O audio '{path.name}' ja foi ingerido anteriormente (ID: {existing_record['id']})."
        )
        logger.info(
            f"Texto gravado no banco: \"{existing_record['texto'][:80]}...\""
        )
        logger.info("Nenhuma nova linha foi adicionada. Concluido com sucesso!")
        return

    # 5. Execucao do Whisper
    model = load_whisper_model()
    transcribed_text, language, duration_s = transcribe_audio(model, path)

    # 6. Gravacao no PostgreSQL com ON CONFLICT DO NOTHING
    try:
        record_id, is_new = insert_transcription(
            file_name=path.name,
            audio_hash=audio_hash,
            text=transcribed_text,
            language=language,
            duration_s=duration_s,
        )

        if is_new:
            logger.info(
                f"SUCESSO: Novo registro criado com ID {record_id} na tabela 'transcricoes'."
            )
        else:
            logger.warning(
                f"CONFLITO DETECTADO: Registro com hash {audio_hash} ja existia (ID {record_id}). ON CONFLICT manteve a tabela integra."
            )
    except psycopg.OperationalError:
        logger.error("Erro ao gravar dados: Conexao perdida com o PostgreSQL.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingestao de audio e transcricao com Whisper salva no PostgreSQL."
    )
    parser.add_argument(
        "audio_path",
        type=str,
        help="Caminho para o arquivo de audio (ex: data/samples/audio.wav)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Forca a transcricao mesmo se o hash ja existir no banco para testar o ON CONFLICT no banco.",
    )

    args = parser.parse_args()
    run_ingestion(audio_file_path=args.audio_path, force=args.force)


if __name__ == "__main__":
    main()
