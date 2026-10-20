-- Tabela de prompts reescritos pelo LLM
-- Armazena o pedido claro e direto gerado a partir de uma transcricao existente

CREATE TABLE IF NOT EXISTS prompts (
    id SERIAL PRIMARY KEY,
    transcricao_id INTEGER NOT NULL REFERENCES transcricoes(id) ON DELETE CASCADE,
    pedido TEXT NOT NULL,
    duvidas TEXT NOT NULL,
    modelo VARCHAR(50) NOT NULL,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompts_transcricao_id ON prompts(transcricao_id);
