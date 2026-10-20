"""Modulo de conexao e operacoes no banco de dados PostgreSQL usando psycopg v3."""

import logging
from typing import List, Optional, Tuple
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
    """Garante que todas as tabelas do pipeline existam no banco de dados."""
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

    CREATE TABLE IF NOT EXISTS avaliacoes (
        id SERIAL PRIMARY KEY,
        prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE UNIQUE,
        foi_editado BOOLEAN NOT NULL,
        pedido_editado TEXT,
        houve_alucinacao BOOLEAN NOT NULL,
        detalhes_alucinacao TEXT,
        avaliado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_avaliacoes_prompt_id ON avaliacoes(prompt_id);

    CREATE TABLE IF NOT EXISTS metricas_qualidade (
        id SERIAL PRIMARY KEY,
        total_avaliado INTEGER NOT NULL,
        total_editados INTEGER NOT NULL,
        total_alucinacoes INTEGER NOT NULL,
        taxa_edicao_pct NUMERIC(5, 2) NOT NULL,
        taxa_alucinacao_pct NUMERIC(5, 2) NOT NULL,
        taxa_aceitacao_direta_pct NUMERIC(5, 2) NOT NULL,
        calculado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(create_tables_sql)
        logger.info("Estrutura do banco de dados verificada/atualizada com sucesso.")
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
    """Grava o pedido reescrito e as duvidas na tabela 'prompts'."""
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


def get_prompt_with_transcription(prompt_id: int) -> Optional[dict]:
    """Retorna os dados completos do prompt e da transcricao correspondente."""
    query = """
    SELECT 
        p.id AS prompt_id,
        p.transcricao_id,
        p.pedido,
        p.duvidas,
        p.modelo,
        p.criado_em AS prompt_criado_em,
        t.arquivo,
        t.texto AS texto_cru,
        t.duracao_s
    FROM prompts p
    JOIN transcricoes t ON t.id = p.transcricao_id
    WHERE p.id = %s;
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (prompt_id,))
            return cursor.fetchone()


def get_unreviewed_prompts() -> List[dict]:
    """Retorna os prompts que ainda nao foram avaliados manualmente."""
    query = """
    SELECT 
        p.id AS prompt_id,
        p.transcricao_id,
        p.pedido,
        p.duvidas,
        p.modelo,
        t.arquivo,
        t.texto AS texto_cru
    FROM prompts p
    JOIN transcricoes t ON t.id = p.transcricao_id
    LEFT JOIN avaliacoes a ON a.prompt_id = p.id
    WHERE a.id IS NULL
    ORDER BY p.id ASC;
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query)
            return cursor.fetchall()


def save_evaluation(
    prompt_id: int,
    foi_editado: bool,
    pedido_editado: Optional[str],
    houve_alucinacao: bool,
    detalhes_alucinacao: Optional[str],
) -> int:
    """Salva a avaliacao humana de qualidade com suporte a reavaliacao idempotente."""
    upsert_sql = """
    INSERT INTO avaliacoes (prompt_id, foi_editado, pedido_editado, houve_alucinacao, detalhes_alucinacao)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (prompt_id) DO UPDATE SET
        foi_editado = EXCLUDED.foi_editado,
        pedido_editado = EXCLUDED.pedido_editado,
        houve_alucinacao = EXCLUDED.houve_alucinacao,
        detalhes_alucinacao = EXCLUDED.detalhes_alucinacao,
        avaliado_em = CURRENT_TIMESTAMP
    RETURNING id;
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                upsert_sql,
                (
                    prompt_id,
                    foi_editado,
                    pedido_editado,
                    houve_alucinacao,
                    detalhes_alucinacao,
                ),
            )
            row = cursor.fetchone()
            return row[0]


def calculate_and_save_metrics() -> dict:
    """Calcula as taxas de qualidade a partir de 'avaliacoes' e registra um snapshot em 'metricas_qualidade'."""
    calc_sql = """
    SELECT 
        COUNT(*)::INTEGER AS total_avaliado,
        COUNT(*) FILTER (WHERE foi_editado)::INTEGER AS total_editados,
        COUNT(*) FILTER (WHERE houve_alucinacao)::INTEGER AS total_alucinacoes,
        ROUND(COALESCE(COUNT(*) FILTER (WHERE foi_editado) * 100.0 / NULLIF(COUNT(*), 0), 0), 2) AS taxa_edicao_pct,
        ROUND(COALESCE(COUNT(*) FILTER (WHERE houve_alucinacao) * 100.0 / NULLIF(COUNT(*), 0), 0), 2) AS taxa_alucinacao_pct,
        ROUND(COALESCE(COUNT(*) FILTER (WHERE NOT foi_editado AND NOT houve_alucinacao) * 100.0 / NULLIF(COUNT(*), 0), 0), 2) AS taxa_aceitacao_direta_pct
    FROM avaliacoes;
    """
    insert_sql = """
    INSERT INTO metricas_qualidade (
        total_avaliado,
        total_editados,
        total_alucinacoes,
        taxa_edicao_pct,
        taxa_alucinacao_pct,
        taxa_aceitacao_direta_pct
    )
    VALUES (%s, %s, %s, %s, %s, %s)
    RETURNING id, total_avaliado, total_editados, total_alucinacoes, taxa_edicao_pct, taxa_alucinacao_pct, taxa_aceitacao_direta_pct, calculado_em;
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(calc_sql)
            stats = cursor.fetchone()
            
            cursor.execute(
                insert_sql,
                (
                    stats["total_avaliado"],
                    stats["total_editados"],
                    stats["total_alucinacoes"],
                    stats["taxa_edicao_pct"],
                    stats["taxa_alucinacao_pct"],
                    stats["taxa_aceitacao_direta_pct"],
                ),
            )
            saved = cursor.fetchone()
            return saved


def get_latest_metrics() -> Optional[dict]:
    """Retorna o snapshot mais recente de metricas calculadas."""
    query = """
    SELECT id, total_avaliado, total_editados, total_alucinacoes, taxa_edicao_pct, taxa_alucinacao_pct, taxa_aceitacao_direta_pct, calculado_em
    FROM metricas_qualidade
    ORDER BY id DESC
    LIMIT 1;
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query)
            return cursor.fetchone()
