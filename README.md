# 🎙️ Speech-to-Prompt Pipeline

> **Pipeline local de Engenharia de Dados e LLMOps:** Ingestão de áudios em português, transcrição com Whisper acelerado por GPU, reescrita estruturada de prompts via LLM local (Ollama) e governança com métricas de qualidade persistidas em PostgreSQL.

---

## 📌 Visão Geral e Objetivo

Em fluxos de trabalho reais, instruções faladas por usuários costumam conter hesitações, termos informais, contradições e ambiguidades. Este projeto resolve esse problema implementando um pipeline robusto e local que:

1. **Ingere áudio** de forma estritamente idempotente (garantindo que o mesmo áudio nunca seja reprocessado desnecessariamente).
2. **Transcreve a fala** em português usando o modelo `faster-whisper` (medium) com aceleração CUDA nativa na GPU.
3. **Reescreve o pedido** em primeira pessoa usando um LLM local (`qwen2.5:7b` via Ollama) com um system prompt restritivo anti-alucinação.
4. **Registra métricas de qualidade (LLMOps)** através de avaliação humana (taxa de edição manual, taxa de alucinação e taxa de aceitação direta).
5. **Disponibiliza um Dashboard analítico** em tempo real para monitoramento do fluxo e volumetria.

---

## 🏗️ Arquitetura do Pipeline

```text
               +-------------------------------------------------------------+
               |                       MÁQUINA HOST                          |
               |                                                             |
               |  [ Áudio .wav / .mp3 ]                                      |
               |            |                                                |
               |            v (Streaming SHA-256)                            |
               |     [ src/ingest.py ]                                       |
               |            |                                                |
               |      (Inference) ---> [ Faster-Whisper (CUDA float16) ]     |
               |            |                     |                          |
               |            +----------+          v                          |
               |                       |      (Texto Cru)                    |
               |                       v          |                          |
               |             [ src/rewrite.py ] <--+                         |
               |                       |                                     |
               |                 (REST API) ---> [ Ollama (Qwen2.5:7b) ]     |
               |                       |                                     |
               |                       v                                     |
               |             [ src/evaluate.py ] (Curadoria Humana)          |
               |                       |                                     |
               |                       v                                     |
               |             [ src/dashboard.py ] (Web / CLI)                |
               +-----------------------|-------------------------------------+
                                       | (psycopg v3)
                                       v
                     +-----------------------------------+
                     |       CONTAINER DOCKER            |
                     |                                   |
                     |         PostgreSQL 16             |
                     |   ├── transcricoes (RAW)          |
                     |   ├── prompts (REFINED)           |
                     |   ├── avaliacoes (CURATION)       |
                     |   └── metricas_qualidade (ANALYT.)|
                     +-----------------------------------+
```

---

## 💡 Decisões Técnicas de Engenharia de Dados

### 1. Idempotência em Duas Camadas
- **Nível de Aplicação:** O hash SHA-256 do arquivo de áudio é calculado lendo blocos de 64 KB (streaming), evitando carregar arquivos grandes inteiros na memória RAM. Se o hash já existir no banco, a execução do Whisper é cancelada na hora, poupando processamento de GPU.
- **Nível de Banco:** A coluna `hash_audio VARCHAR(64) UNIQUE` possui uma restrição única. A inserção utiliza `INSERT INTO ... ON CONFLICT (hash_audio) DO NOTHING`, assegurando consistência atômica mesmo em cenários de concorrência.

### 2. Desacoplamento de Infraestrutura
- O **PostgreSQL** roda isolado dentro de um container Docker Compose com volume persistente (`postgres_data`).
- O **Whisper** e o **Ollama** rodam diretamente no sistema operacional host, aproveitando a aceleração direta da GPU NVIDIA GeForce RTX 4050 (6GB VRAM) sem a sobrecarga de containers GPU.

### 3. Modelagem Relacional Normalizada
O esquema segue o conceito de camadas de dados:
- **`transcricoes` (Raw Layer):** Armazena o arquivo de origem, hash SHA-256, duração em segundos e o texto cru gerado pelo Whisper.
- **`prompts` (Refined Layer):** Contém o pedido reescrito, lista de dúvidas gerada e o modelo utilizado, com chave estrangeira (`FK`) apontando para a transcrição.
- **`avaliacoes` (Curation Layer):** Registra a revisão manual humana indicando se o pedido foi alterado ou se houve alucinação.
- **`metricas_qualidade` (Analytics Layer):** Armazena snapshots históricos das taxas de assertividade (Taxa de Edição Humana, Taxa de Alucinação e Taxa de Aceitação Direta), calculadas atomicamente no banco com `COUNT(*) FILTER (...)`.

### 4. Engenharia de Prompts e Baixa Temperatura
O LLM é acionado com `temperature = 0.1` e um system prompt que proíbe terminantemente a inserção de tecnologias ou requisitos não mencionados na fala. Quando algo está ambíguo ou incompleto, o modelo é obrigado a listar o ponto na seção `DÚVIDAS:`.

---

## 🛠️ Tecnologias Utilizadas

| Componente | Tecnologia | Finalidade |
|---|---|---|
| **Linguagem** | Python 3.12 | Linguagem principal do pipeline |
| **Banco de Dados** | PostgreSQL 16 Alpine | Armazenamento relacional e métricas |
| **Containerização** | Docker & Docker Compose | Infraestrutura do banco de dados isolada |
| **Driver de Banco** | psycopg v3 (`psycopg[binary]`) | Driver moderno e performático para PostgreSQL |
| **Transcrição** | faster-whisper (CTranslate2) | Transcrição de áudio acelerada por CUDA float16 |
| **Aceleração GPU** | CUDA 12 (`nvidia-*-cu12`) | Execução na RTX 4050 sem necessidade de Toolkit |
| **LLM Local** | Ollama (`qwen2.5:7b`) | Reescrita estruturada de linguagem natural |
| **Configuração** | python-dotenv | Separação estrita de credenciais (.env) |

---

## 🚀 Como Executar o Projeto

### 1. Pré-requisitos
- **Docker Desktop** instalado e em execução.
- **Python 3.11+** instalado.
- **Ollama** instalado na máquina. Baixe o modelo executando:
  ```bash
  ollama pull qwen2.5:7b
  ```

### 2. Configurar o Ambiente Local
Clone o repositório e crie o ambiente virtual:
```powershell
# Clone o projeto
git clone https://github.com/daniloofrs/speech-to-prompt-pipeline.git
cd speech-to-prompt-pipeline

# Crie e ative o ambiente virtual
python -m venv venv
.\venv\Scripts\Activate.ps1

# Instale as dependências
pip install -r requirements.txt
```

Crie o arquivo `.env` baseado no modelo (já vem pré-configurado com os valores padrão):
```powershell
cp .env.example .env
```

### 3. Subir o Banco de Dados
```powershell
docker compose up -d
```
*(Confirme a inicialização saudável com `docker compose ps`)*

---

## 📋 Executando as Etapas do Pipeline

### Etapa 1: Ingestão de Áudio
Coloque seu arquivo de áudio na pasta `data/samples/` e execute:
```powershell
python src/ingest.py data/samples/seu_audio.wav
```
- **Teste de Idempotência:** Execute o mesmo comando uma segunda vez. O pipeline detectará o hash SHA-256 e impedirá a duplicação sem gastar processamento de GPU.

### Etapa 2: Reescrita com LLM
Transforme a transcrição em um pedido claro e direto para IA:
```powershell
# Processa a transcrição mais recente
python src/rewrite.py

# Ou especifique o ID de uma transcrição
python src/rewrite.py --id 1
```
*(O terminal exibirá a comparação da fala crua e do pedido reescrito lado a lado)*

### Etapa 3: Avaliação de Qualidade
Realize a auditoria humana da qualidade da resposta:
```powershell
# Modo interativo (pergunta no terminal)
python src/evaluate.py

# Apenas visualizar o painel atual de métricas
python src/evaluate.py --summary
```

### Etapa 4: Dashboard de Monitoramento
Abra a interface web do pipeline:
```powershell
python src/dashboard.py --open
```
- Acesse no navegador: **`http://localhost:8080`**
- Ou visualize diretamente no terminal: `python src/dashboard.py --cli`

---

## 📁 Estrutura de Pastas

```text
speech-to-prompt-pipeline/
├── data/
│   └── samples/             # Pasta de amostras de áudio locais (.wav, .mp3)
├── sql/
│   ├── 01_init.sql          # DDL da tabela 'transcricoes' (Raw Layer)
│   ├── 02_prompts.sql       # DDL da tabela 'prompts' (Refined Layer)
│   └── 03_avaliacoes.sql    # DDL de 'avaliacoes' e 'metricas_qualidade'
├── src/
│   ├── __init__.py
│   ├── config.py            # Centraliza variáveis do .env e conexões
│   ├── database.py          # Camada de acesso a dados (psycopg v3)
│   ├── ingest.py            # Etapa 1: Ingestão, hash e Whisper GPU
│   ├── rewrite.py           # Etapa 2: Chamada ao Ollama e formatação
│   ├── evaluate.py          # Etapa 3: Curadoria humana e cálculo de taxas
│   └── dashboard.py         # Etapa 4: Interface web e CLI de monitoramento
├── .env.example             # Template seguro de credenciais
├── .gitignore               # Ignora .env, venv/, caches e áudios locais
├── docker-compose.yml       # Orquestração do container PostgreSQL
├── requirements.txt         # Dependências do projeto
└── README.md                # Documentação técnica do projeto
```

---

## 📊 Consultas SQL de Auditoria

Para inspecionar os dados diretamente dentro do container do PostgreSQL:

```bash
# Consultar todas as transcrições
docker exec -it speech_pipeline_postgres psql -U pipeline_user -d speech_pipeline -c "SELECT id, arquivo, hash_audio, duracao_s, criado_em FROM transcricoes;"

# Consultar pedidos gerados pelo LLM
docker exec -it speech_pipeline_postgres psql -U pipeline_user -d speech_pipeline -c "SELECT id, transcricao_id, pedido, modelo FROM prompts;"

# Consultar o histórico de métricas de qualidade (LLMOps)
docker exec -it speech_pipeline_postgres psql -U pipeline_user -d speech_pipeline -c "SELECT total_avaliado, taxa_aceitacao_direta_pct, taxa_edicao_pct, taxa_alucinacao_pct, calculado_em FROM metricas_qualidade;"
```

---

## 👤 Autor

Desenvolvido por **Danilo Fernandes** como projeto de portfólio para estágio em **Engenharia de Dados**.
- GitHub: [@daniloofrs](https://github.com/daniloofrs)
