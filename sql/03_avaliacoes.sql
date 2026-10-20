-- Tabelas para avaliacao de qualidade e metricas agregadas (Etapa 3)

-- 1. Registro individual de avaliacao humana de cada prompt gerado
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

-- 2. Tabela de metricas historicas calculadas (taxas de qualidade do pipeline)
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
