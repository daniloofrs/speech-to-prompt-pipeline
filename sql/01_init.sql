-- Inicializacao da tabela de transcricoes
-- Armazena o resultado do processo de audio com Whisper e a chave unica (hash_audio) para garantir idempotencia

CREATE TABLE IF NOT EXISTS transcricoes (
    id SERIAL PRIMARY KEY,
    arquivo TEXT NOT NULL,
    hash_audio VARCHAR(64) NOT NULL UNIQUE,
    texto TEXT NOT NULL,
    idioma VARCHAR(10) NOT NULL,
    duracao_s NUMERIC(8, 2) NOT NULL,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
