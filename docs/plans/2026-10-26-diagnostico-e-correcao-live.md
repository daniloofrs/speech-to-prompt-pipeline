# Plano de Diagnóstico e Correção Definitiva do Live Assistant (Dynamic Island)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolver os 5 gargalos identificados que fazem o assistente flutuante de voz (`live_pill.py`) falhar, travar ou cortar áudio, garantindo inicialização 100% confiável, VAD adaptativo sem vazamento de stream de áudio e latência sub-1.5s na RTX 4050.

**Architecture:** 
1. Adicionar preflight e download atômico dos modelos neurais (Whisper `medium` e Ollama `qwen2.5:3b`) eliminando concorrência e corrupção de cache.
2. Corrigir a ordem crítica de transição de estado no VAD para fechar o stream de áudio do PortAudio antes de exibir notificações toast.
3. Tornar o limiar de RMS adaptativo ao ruído ambiente para evitar falso silêncio em microfones convencionais.
4. Desanexar o processo do Ollama (`DETACHED_PROCESS` no Win32) para manter o serviço ativo mesmo com lançadores silenciosos.

**Tech Stack:** Python 3.12, Faster-Whisper (CUDA FP16), Ollama (Qwen 2.5), SoundDevice / PortAudio, Tkinter, Win32 API.

**Spec:** Baseado na arquitetura do `README.md` e nos requisitos de usabilidade hands-free com atalho F8.

---

## Diagnóstico Completo dos Problemas Encontrados

| # | Sintoma | Causa Raiz Técnica | Impacto |
|---|---------|---------------------|---------|
| **1** | *"Preparando motor neural..." não sai da tela ou dá "Falha ao processar"* | O modelo Whisper `medium` (~1.53 GB) nunca terminou de baixar. No cache do HuggingFace havia 4 arquivos `.incomplete` corrompidos porque a thread em segundo plano e a thread do pipeline disparavam downloads simultâneos que colidiam. | Ao apertar F8 antes do download terminar, a thread final esperava 6s e tentava baixar de novo, travando a GPU e dando erro. |
| **2** | *Microfone trava ou não grava após timeout de inatividade* | No método `_animation_tick()`, ao atingir `MAX_INITIAL_SILENCE_S`, o código chamava `self._show_toast()` **antes** de `self._stop_audio_capture()`. O toast mudava o estado para `"toast"`, fazendo com que `_stop_audio_capture()` abortasse imediatamente sem fechar o stream do microfone. | O microfone ficava aberto indefinidamente em background, causando vazamento de recursos no PortAudio/WASAPI e impedindo novas gravações. |
| **3** | *Corta antes de terminar de falar ou diz "Nenhuma fala detectada" enquanto fala* | O limiar estático `SILENCE_THRESHOLD_RMS = 0.016` estava com `max(0.016, ...)`. Em microfones convencionais de notebook (Realtek sem ganho boost), a fala humana fica frequentemente na faixa de 0.008 a 0.014 RMS. | O sistema nunca marcava `has_spoken = True`, e após 6s cancelava a gravação do usuário achando que era silêncio. |
| **4** | *Ollama não responde ou fecha silenciosamente* | O comando `subprocess.Popen(["ollama", "serve"])` não usava as flags `DETACHED_PROCESS` e `CREATE_NEW_PROCESS_GROUP` no Windows. | Se o processo pai encerrava ou o terminal fechava, o serviço do Ollama caía junto. |
| **5** | *Disparo duplicado na fila de UI* | Linha 1354 de `src/live_pill.py` inseria `self.ui_queue.put(("error", "Falha ao processar"))` duas vezes seguidas no mesmo bloco `except`. | Envio de mensagens redundantes e poluição de log/interface. |

---

## Global Constraints

- Manter 100% de compatibilidade com Windows 11 e placas NVIDIA RTX (CUDA float16).
- Não quebrar o atalho global `F8` via Win32 `RegisterHotKey`.
- Manter o tempo total do pipeline (fala -> transcrição -> Ollama -> clipboard) abaixo de 1.8 segundos.
- Não usar bibliotecas externas novas desnecessárias (YAGNI / Manter `requirements.txt` enxuto).
- Todos os testes devem rodar via `./venv/Scripts/python.exe`.

## Review Focus

1. **Microfone mudo ou sem permissão:** Quando o dispositivo de áudio falha ou não há entrada, a aplicação não deve travar nem abrir streams fantasmas.
2. **Download interrompido:** Se a conexão cair durante o download do Whisper, a aplicação deve alertar claramente e permitir retomada sem corromper arquivos.
3. **Pausas na fala:** Pausa natural entre 0.8s e 1.9s não deve cortar a fala; se o usuário voltar a falar dentro da janela de 2s, o cronômetro deve reiniciar imediatamente.
4. **Fechamento do app (Escape / Alt+F4):** Todos os recursos (streams de áudio, hotkey listener e threads) devem ser liberados instantaneamente.
5. **Execução silenciosa via atalho Desktop (`pythonw.exe`):** O aplicativo deve rodar sem janela preta de console e sem estourar exceções de `sys.stdout` nulo.

---

## Tarefas de Implementação

### Task 1: Script de Pré-carregamento e Verificação Atômica dos Modelos

**Files:**
- Create: `tools/preparar_modelos.py`
- Test: `tools/preparar_modelos.py`

**Interfaces:**
- Consumes: `faster_whisper.WhisperModel`, `src.config`
- Produces: Modelos verificados e prontos em disco sem arquivos `.incomplete`.

- [ ] **Step 1: Criar o script de verificação e download explícito**
  Implementar `tools/preparar_modelos.py` com:
  - Limpeza prévia de arquivos `.incomplete` corrompidos em `.cache/huggingface/hub/models--Systran--faster-whisper-medium`.
  - Download com barra de progresso do `Systran/faster-whisper-medium` na GPU.
  - Verificação do serviço Ollama com modelo `qwen2.5:3b`.
  - Exibição de diagnóstico visual claro no terminal.

- [ ] **Step 2: Executar o download e verificar conclusão sem erros**
  Run: `.\venv\Scripts\python.exe tools/preparar_modelos.py`
  Expected: Download concluído com sucesso e status `[PASS] Modelos prontos na GPU`.

- [ ] **Step 3: Commit**
  ```bash
  git add tools/preparar_modelos.py
  git commit -m "feat(tools): add automated model prefetch and cache integrity tool"
  ```

---

### Task 2: Correção do Vazamento de Stream e Máquina de Estados no VAD

**Files:**
- Modify: `src/live_pill.py:760-800`
- Modify: `src/live_pill.py:1270-1310`
- Test: `tools/test_vad_autostop.py`

**Interfaces:**
- Consumes: `_animation_tick()`, `_stop_audio_capture()`
- Produces: Stream de áudio sempre liberado antes de qualquer transição de estado.

- [ ] **Step 1: Escrever teste de regressão para garantir que timeout de inatividade para o stream**
  Adicionar asserção em `tools/test_vad_autostop.py` verificando que `app.audio_stream is None` após inatividade inicial.

- [ ] **Step 2: Executar teste para verificar falha no código atual**
  Run: `.\venv\Scripts\python.exe tools/test_vad_autostop.py`
  Expected: Falha no código atual (confirmada no diagnóstico).

- [ ] **Step 3: Refatorar `_stop_audio_capture` para gerenciar encerramento atômico**
  - No `_animation_tick()`: chamar `self._stop_audio_capture(reason_toast="Nenhuma fala detectada")`.
  - No `_stop_audio_capture(reason_toast: Optional[str] = None)`:
    - Parar `self.audio_stream` e setar `self.stop_recording_event` **imediatamente**.
    - Se `reason_toast` for fornecido, transicionar direto para `toast` sem iniciar worker.
    - Se a gravação foi normal, transicionar para `processing` e disparar worker.

- [ ] **Step 4: Executar teste e validar que agora passa**
  Run: `.\venv\Scripts\python.exe tools/test_vad_autostop.py`
  Expected: PASS em todos os cenários sem streams abertos.

- [ ] **Step 5: Commit**
  ```bash
  git add src/live_pill.py tools/test_vad_autostop.py
  git commit -m "fix(live): prevent audio stream leaks and fix state machine transitions on auto-stop"
  ```

---

### Task 3: Calibração Dinâmica e Adaptativa de RMS para Sensibilidade de Microfone

**Files:**
- Modify: `src/config.py:30-40`
- Modify: `src/live_pill.py:745-775`
- Test: `tools/test_vad_autostop.py`

**Interfaces:**
- Consumes: `self.current_rms`, `self.ambient_rms_floor`
- Produces: `speech_threshold` adaptado ao microfone do usuário (piso de 0.007 em vez de 0.016 rígido).

- [ ] **Step 1: Atualizar limiares no `src/config.py` e lógica em `src/live_pill.py`**
  - Ajustar `SILENCE_THRESHOLD_RMS` para `0.008` (nível padrão de voz confortável).
  - Em `_animation_tick()`: calcular `speech_threshold = min(0.024, max(0.007, self.ambient_rms_floor * 2.2))`.
  - Garantir que se a pessoa falar baixo, o sistema registra voz (`has_spoken = True`).
  - Proteger contra corte acidental: exigir ao menos 0.5s de fala acumulada antes de ativar a contagem regressiva de silêncio de 2.0s.

- [ ] **Step 2: Rodar teste automatizado de VAD**
  Run: `.\venv\Scripts\python.exe tools/test_vad_autostop.py`
  Expected: PASS com validação de sensibilidade ajustada.

- [ ] **Step 3: Commit**
  ```bash
  git add src/config.py src/live_pill.py tools/test_vad_autostop.py
  git commit -m "fix(live): calibrate adaptive RMS speech threshold for quiet microphones"
  ```

---

### Task 4: Proteção contra Concorrência no Carregamento e Bloqueio de F8 enquanto Modelos Carregam

**Files:**
- Modify: `src/live_pill.py:505-520`
- Modify: `src/live_pill.py:1300-1360`
- Modify: `src/live_pill.py:1415-1440`

**Interfaces:**
- Consumes: `self.models_ready`, `_on_hotkey_toggle`
- Produces: Feedback imediato na UI se F8 for acionado antes dos modelos estarem prontos; eliminação de re-instanciação simultânea do Whisper.

- [ ] **Step 1: Bloquear gravação com aviso se modelos ainda estiverem carregando**
  - Em `_on_hotkey_toggle`: se `not self.models_ready`, exibir toast amigável: `"Aguarde: inicializando motor neural na GPU..."` e não abrir microfone.
  - Na chamada de clique do mouse na Dynamic Island: aplicar o mesmo bloqueio se `not self.models_ready`.

- [ ] **Step 2: Eliminar re-instanciação redundante em `_final_pipeline_worker`**
  - Se `self.whisper_model is None`, aguardar com lock único e timeout de 15s sem disparar um segundo download em paralelo.
  - Remover a duplicidade do erro na fila de UI (`self.ui_queue.put(("error", "Falha ao processar"))`).

- [ ] **Step 3: Commit**
  ```bash
  git add src/live_pill.py
  git commit -m "fix(live): prevent concurrent model loads and guard recording until neural engine is ready"
  ```

---

### Task 5: Robustez do Processo Ollama em Background no Windows

**Files:**
- Modify: `src/live_pill.py:320-355`

**Interfaces:**
- Consumes: `subprocess.Popen`
- Produces: Servidor Ollama independente rodando em segundo plano sem morrer ao fechar janelas.

- [ ] **Step 1: Adicionar flags Win32 completas para desanexação de processo**
  - Configurar `creationflags = subprocess.CREATE_NO_WINDOW | 0x00000008 | 0x00000200` (`DETACHED_PROCESS` + `CREATE_NEW_PROCESS_GROUP`).
  - Adicionar retry com verificação rápida de `/api/tags`.

- [ ] **Step 2: Testar inicialização autônoma do Ollama**
  Run: `.\venv\Scripts\python.exe -c "from src.live_pill import ensure_ollama_running; print(ensure_ollama_running())"`
  Expected: `True` com Ollama respondendo normalmente.

- [ ] **Step 3: Commit**
  ```bash
  git add src/live_pill.py
  git commit -m "fix(live): spawn ollama with win32 detached process flags"
  ```

---

### Task 6: Verificação Completa e Benchmark de Ponta a Ponta

**Files:**
- Test: `tools/benchmark_whisper.py`
- Test: `tools/test_vad_autostop.py`

**Interfaces:**
- Consumes: Arquivo de áudio de teste + GPU CUDA + Ollama
- Produces: Relatório oficial de latência e confirmação de estabilidade sem travamentos.

- [ ] **Step 1: Executar o benchmark de ponta a ponta**
  Run: `.\venv\Scripts\python.exe tools/benchmark_whisper.py`
  Expected: Whisper medium em <0.6s, Ollama em <0.9s, Latência total <1.5s na RTX 4050.

- [ ] **Step 2: Testar inicialização completa da Dynamic Island**
  Run: `.\venv\Scripts\python.exe src/live_pill.py` (ou execução breve com verificação de log limpo).
  Expected: Janela abre perfeitamente, atalho F8 ativo, modelos prontos (ponto verde).

- [ ] **Step 3: Commit final e atualização de status**
  ```bash
  git commit --allow-empty -m "chore(release): verified live assistant stability and latency bar"
  ```
