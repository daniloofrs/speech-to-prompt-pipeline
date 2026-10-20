"""Modulo de conexao e operacoes no banco de dados PostgreSQL usando psycopg v3."""

import logging
from typing import Optional, Tuple
import psycopg
from psycopg.rows import dict_row

from src.config import get_db_connection_uri

logger = logging.getLogger(__name__)


def get_connection() -> psycopg.Connection:
    """Abre e retorna uma conexao com o PostgreSQL.
    
    Lanca OperationalError caso o banco esteja inacessivel ou fora do ar.
    Usa connect_timeout=3 para falhar rapidamente caso o container esteja desligado.
    """
    connection_uri = get_db_connection_uri()
    try:
        conn = psycopg.connect(connection_uri, autocommit=True, connect_timeout=3)
        return conn
    except psycopg.OperationalError as error:
        logger.error(
            "Falha ao conectar ao PostgreSQL. Verifique se o container Docker esta ativo (docker compose up -d)."
        )
        raise error


def init_db() -> None:
    """Garante que as tabelas 'transcricoes' e 'prompts' existam no banco de dados."""
    create_tables_sql = """
    CREATE TABLE IF NOT EXISTS transcricoes (
        id SERIAL PRIMARY KEY,
        arquivo TEXT NOT NULL,
        hash_audio VARCHAR(64) NOT NULL UNIQUE,
        texto TEXT NOT NULL,
        idioma VARCHAR(10) NOT NULL,
        duracao_s NUMERIC(8, 2) NOT NULL,
        criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS prompts (
        id SERIAL PRIMARY KEY,
        transcricao_id INTEGER NOT NULL REFERENCES transcricoes(id) ON DELETE CASCADE,
        pedido TEXT NOT NULL,
        duvidas TEXT NOT NULL,
        modelo VARCHAR(50) NOT NULL,
        criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_prompts_transcricao_id ON prompts(transcricao_id);
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(create_tables_sql)
        logger.info("Tabelas 'transcricoes' e 'prompts' verificadas/criadas com sucesso.")
    except psycopg.OperationalError as error:
        logger.error("Nao foi possivel inicializar o banco de dados. O servico esta offline?")
        raise error


def find_by_hash(audio_hash: str) -> Optional[dict]:
    """Busca uma transcricao existente pelo hash SHA-256 do arquivo de audio."""
    query = """
    SELECT id, arquivo, hash_audio, texto, idioma, duracao_s, criado_em
    FROM transcricoes
    WHERE hash_audio = %s;
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (audio_hash,))
            return cursor.fetchone()


def get_transcription_by_id(transcription_id: int) -> Optional[dict]:
    """Busca uma transcricao pelo seu ID primario."""
    query = """
    SELECT id, arquivo, hash_audio, texto, idioma, duracao_s, criado_em
    FROM transcricoes
    WHERE id = %s;
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (transcription_id,))
            return cursor.fetchone()


def get_latest_transcription() -> Optional[dict]:
    """Retorna a transcricao mais recente cadastrada no banco."""
    query = """
    SELECT id, arquivo, hash_audio, texto, idioma, duracao_s, criado_em
    FROM transcricoes
    ORDER BY id DESC
    LIMIT 1;
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query)
            return cursor.fetchone()


def insert_transcription(
    file_name: str,
    audio_hash: str,
    text: str,
    language: str,
    duration_s: float,
) -> Tuple[Optional[int], bool]:
    """Insere o resultado da transcricao no banco de forma idempotente.
    
    Retorna uma tupla (id, is_new):
    - is_new = True se o registro foi inserido com sucesso.
    - is_new = False se ja existia (conflito no hash_audio).
    """
    insert_sql = """
    INSERT INTO transcricoes (arquivo, hash_audio, texto, idioma, duracao_s)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (hash_audio) DO NOTHING
    RETURNING id;
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                insert_sql,
                (file_name, audio_hash, text, language, round(duration_s, 2)),
            )
            row = cursor.fetchone()
            if row is not None:
                record_id = row[0]
                return record_id, True
            
            # Se row is None, significa que ocorreu conflito (ON CONFLICT DO NOTHING)
            cursor.execute(
                "SELECT id FROM transcricoes WHERE hash_audio = %s;",
                (audio_hash,),
            )
            existing_row = cursor.fetchone()
            existing_id = existing_row[0] if existing_row else None
            return existing_id, False


def insert_prompt(
    transcription_id: int,
    pedido: str,
    duvidas: str,
    model_name: str,
) -> int:
    """Grava o pedido reescrito e as duvidas na tabela 'prompts'.
    
    Retorna o ID gerado para o prompt.
    """
    insert_sql = """
    INSERT INTO prompts (transcricao_id, pedido, duvidas, modelo)
    VALUES (%s, %s, %s, %s)
    RETURNING id;
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                insert_sql,
                (transcription_id, pedido, duvidas, model_name),
            )
            row = cursor.fetchone()
            return row[0]
