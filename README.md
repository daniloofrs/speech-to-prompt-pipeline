<p align="center">
  <img src="assets/hero-banner.png" alt="Speech-to-Prompt Pipeline & Live Dynamic Island" width="100%" />
</p>

<p align="center">
  <img src="assets/app-icon.png" alt="Speech to Prompt App Logo" width="88" height="88" style="border-radius: 20px; box-shadow: 0 8px 24px rgba(0,0,0,0.6);" />
</p>

# 🎙️ Speech-to-Prompt Pipeline & Dynamic Island

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/PostgreSQL-16%20Alpine-336791?logo=postgresql&logoColor=white" alt="PostgreSQL 16" />
  <img src="https://img.shields.io/badge/Docker%20Compose-v2-2496ED?logo=docker&logoColor=white" alt="Docker Compose" />
  <img src="https://img.shields.io/badge/NVIDIA%20CUDA-12%20RTX%204050-76B900?logo=nvidia&logoColor=white" alt="CUDA 12" />
  <img src="https://img.shields.io/badge/Faster--Whisper-Medium%20FP16-orange" alt="Faster-Whisper" />
  <img src="https://img.shields.io/badge/Ollama-Qwen2.5-black?logo=ollama&logoColor=white" alt="Ollama" />
  <img src="https://img.shields.io/badge/UI-Apple%20Fluid%20Design-grey?logo=apple&logoColor=white" alt="Apple Design" />
</p>

> **Transforme comandos de voz em prompts estruturados prontos no seu `Ctrl + V`.** Pipeline de Engenharia de Dados & LLMOps com governança de qualidade (PostgreSQL) e assistente desktop flutuante estilo **Apple Dynamic Island** com física a 60 FPS e atalho global `F8`.

---

## ⚡ Início Rápido (Quickstart)

```powershell
# 1. Clonar e configurar ambiente
git clone https://github.com/daniloofrs/speech-to-prompt-pipeline.git
cd speech-to-prompt-pipeline
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt; cp .env.example .env

# 2. Baixar modelos locais no Ollama
ollama pull qwen2.5:3b
ollama pull qwen2.5:7b

# 3. Subir banco de dados (necessário apenas para modo batch/dashboard)
docker compose up -d
```

---

## 🍏 1. Modo Desktop: Live Dynamic Island

Assistente de produtividade *Always-on-Top* para ditar comandos enquanto você programa ou navega.

<p align="center">
  <img src="assets/showcase-setup.png" alt="Dynamic Island em Setup de Desenvolvimento" width="100%" />
</p>

- **Lançamento em 1 Clique:** Duplo-clique no atalho **`Speech to Prompt`** na Área de Trabalho (criado via `tools/criar_atalho.ps1`). Executa em segundo plano via `pythonw.exe` **sem janela preta de terminal**.
- **Atalho Global (`F8`):** Pressione **`F8`** em qualquer programa (VS Code, Chrome) para começar a falar. Pressione **`F8`** novamente para concluir.
- **Feedback Sonoro (Chimes):** Som suave do Windows ao iniciar a escuta e chime de confirmação quando o prompt estiver pronto no `Ctrl + V`.
- **Streaming & Física Apple:** Transcrição ao vivo na tela, equalizador reativo de voz e física de molas (60 FPS com arrasto e inércia).
- **Zero Dependências:** Roda 100% em memória RAM e GPU (RTX 4050), entregando o prompt refinado em ~1.5s no seu clipboard.

---

## 🏗️ 2. Arquitetura do Sistema & Trilha Batch

<p align="center">
  <img src="assets/pipeline-isometric.png" alt="Arquitetura 3D do Pipeline" width="100%" />
</p>

O projeto opera sob duas trilhas complementares de **Engenharia de Dados & IA**:

| Trilha | Objetivo | Stack Principal |
|---|---|---|
| **🍏 Live Desktop** | Produtividade diária em tempo real via atalho `F8` | Tkinter 60 FPS, Faster-Whisper Medium, Ollama Qwen2.5:3b, Pyperclip |
| **📊 Batch Pipeline** | Ingestão auditável em lote, idempotência e curadoria humana | SHA-256 streaming, PostgreSQL 16, Ollama Qwen2.5:7b, Docker |

### Comandos da Trilha Batch:
```powershell
# Ingestão de áudio com SHA-256 estrito (impede duplicatas)
python src/ingest.py data/samples/teste.wav

# Reescrita estruturada via LLM
python src/rewrite.py

# Auditoria humana e métricas (Aceitação Direta, Edição, Alucinação)
python src/evaluate.py --summary
```

---

## 🌐 3. Dashboard Web & SVG Liquid Glass Shader

Painel de telemetria analítica com refração óptica de vidro líquido baseada em *Signed Distance Fields* (SDF), inspirada em [Shu Ding](https://github.com/shuding/liquid-glass).

<p align="center">
  <img src="assets/liquid-glass-macro.png" alt="Macro Refração Liquid Glass" width="100%" />
</p>

```powershell
python src/dashboard.py --open
```
- Acesse em: `http://localhost:8080` (ou terminal puro com `--cli`).
- **Lente Óptica SVG:** Dynamic Island arrastável na página com refração física via `<feDisplacementMap>` e `<feGaussianBlur>` (52px).
- **Controles ao Vivo:** Botões integrados para alternar refração ON/OFF, nível de desfoque e opacidade de fundo.

---

## 💡 Destaques de Engenharia

- **Idempotência em Camadas:** Hash SHA-256 calculado em blocos de 64 KB na aplicação + restrição `UNIQUE(hash_audio)` no PostgreSQL.
- **Dimensionamento de VRAM (RTX 4050 6GB):** Whisper FP16 (~1.5 GB) + Ollama Q4_K_M (~1.9 GB) + buffers DWM (~0.8 GB) totalizam ~4.2 GB, prevenindo *CUDA Memory Paging* e garantindo inferência em ~1.5s.
- **Calibração Fonética:** `TECH_INITIAL_PROMPT` condiciona o Whisper para vocabulário de tecnologia em português (*Python, Docker, FastAPI, SQL, API REST*).

---

## 📁 Estrutura do Repositório

```text
speech-to-prompt-pipeline/
├── assets/                  # Identidade visual e imagens (Google Nano Banana)
│   ├── app-icon.ico         # Ícone oficial para o atalho Windows
│   ├── app-icon.png         # Logotipo em squircle (Favicon / Avatar)
│   ├── hero-banner.png      # Capa panorâmica do GitHub
│   ├── liquid-glass-macro.png # Macro da refração óptica SVG
│   ├── pipeline-isometric.png # Infográfico 3D da arquitetura
│   └── showcase-setup.png   # Mockup em setup de desenvolvimento
├── sql/                     # Migrações SQL (Raw, Refined e Analytics)
├── src/
│   ├── config.py            # Centralizador de variáveis (.env)
│   ├── database.py          # Conexão psycopg v3 e queries analíticas
│   ├── ingest.py            # Ingestão SHA-256 e Faster-Whisper
│   ├── rewrite.py           # Integração Ollama e prompt engineering
│   ├── evaluate.py          # Curadoria humana e métricas de qualidade
│   ├── dashboard.py         # Dashboard web com Liquid Glass e CLI
│   └── live_pill.py         # Dynamic Island desktop com molas e atalho F8
├── tools/criar_atalho.ps1   # Gerador do atalho na Área de Trabalho
├── iniciar_live.vbs         # Lançador silencioso (sem console)
├── docker-compose.yml       # Orquestração do PostgreSQL 16
└── README.md                # Documentação técnica resumida
```

---

## 👤 Autor

Desenvolvido por **Danilo Fernandes** como projeto de portfólio prático de **Engenharia de Dados e LLMOps**.
- GitHub: [@daniloofrs](https://github.com/daniloofrs)
