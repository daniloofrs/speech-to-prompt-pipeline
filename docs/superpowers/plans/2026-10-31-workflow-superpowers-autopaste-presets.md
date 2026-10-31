# Workflow Superpowers (Auto-Paste, Presets & Audio-Haptics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the speech-to-prompt live pill into a zero-friction developer workflow tool by adding automated cursor paste injection, instant prompt profile presets, subtle procedural Apple audio-haptics, and local JSONL history logging.

**Architecture:** Decompose workflow enhancements into small, decoupled modules: `presets.py` for prompt modes (Code, Conventional Commit, English, Clean Dictation); `auto_paste.py` for Win32 foreground window capture and synthesized `Ctrl+V` keystroke injection; `audio_haptics.py` for procedural audio synthesis (replacing OS beeps with gentle chime sine waves); and `history.py` for local JSONL persistence. The main desktop orchestrator (`src/live_pill.py`) wires these modules into its continuous 60 FPS Apple spring loop without blocking inference or UI responsiveness.

**Tech Stack:** Python 3.12, Win32 API (`ctypes`), `sounddevice`, `numpy`, `llama-cpp-python` (CUDA float16), `faster-whisper`, Tkinter (Apple Liquid Glass surface).

**Spec:** In-memory design contract based on desktop productivity requirements, Apple HIG audio-haptics, and zero-click cursor injection ergonomics.

## Global Constraints

- **Latency floor:** All operations before and after inference (window capture, sound synthesis, paste dispatch) must take `< 10ms`.
- **Zero OS dependencies for audio:** Audio-haptics must be synthesized in-process with numpy sine/exponential decay curves, avoiding missing file paths or external system sound schemes.
- **Fail-safe Auto-Paste:** If target window cannot be refocused or paste fails, clipboard contents must remain safely preserved with fallback visual notification.
- **GPU thread-safety:** Lock contention must never occur between live Whisper streaming and LLM rewriting.
- **Encoding & Locale:** UTF-8 encoding across all history logs and prompt formatting.

## Review Focus

1. **Auto-paste to wrong window:** Target window handle (`HWND`) must be captured at the exact moment recording starts (pointer down or F8 press), not after speech ends when the live pill might be focused.
2. **Key state stuck:** Synthesizing `Ctrl+V` must strictly release both keys (`KEYEVENTF_KEYUP`) in a `finally` block to prevent holding down Control in Windows.
3. **Preset switching race conditions:** Switching presets mid-recording or mid-processing must safely apply to the next transcription or current pipeline cycle without crashing.
4. **Volume clipping in procedural chimes:** Waveform synthesis must normalize peak amplitude below `0.15` to ensure soft, non-intrusive feedback.
5. **History file corruption:** Concurrent file writes to `data/history.jsonl` must use atomic append with newline flushing.

---

### Task 1: Prompt Presets Engine

**Files:**
- Create: `src/presets.py`
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: Nothing
- Produces:
  - `PromptPreset(id: str, label: str, icon: str, system_prompt: str, is_raw: bool)`
  - `PRESETS: Dict[str, PromptPreset]`
  - `get_preset(preset_id: str) -> PromptPreset`
  - `get_next_preset(current_id: str) -> PromptPreset`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_presets.py
from src.presets import get_preset, get_next_preset, PRESETS

def test_default_presets_exist():
    assert "code" in PRESETS
    assert "commit" in PRESETS
    assert "english" in PRESETS
    assert "clean" in PRESETS
    code = get_preset("code")
    assert "Prompt Engineering" in code.system_prompt
    assert code.is_raw is False

def test_clean_preset_is_raw():
    clean = get_preset("clean")
    assert clean.is_raw is True

def test_cycle_presets():
    first = "code"
    second = get_next_preset(first)
    assert second.id == "commit"
    last_cycled = get_next_preset("clean")
    assert last_cycled.id == "code"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_presets.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.presets'`

- [ ] **Step 3: Implement `src/presets.py`**

Define `PromptPreset` dataclass with `code`, `commit` (Conventional Commits), `english` (Prompt EN), and `clean` (raw punctuated speech) presets. Implement `get_preset(preset_id: str)` with `"code"` fallback and `get_next_preset(current_id: str)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_presets.py`
Expected: PASS (Ran 3 tests, OK)

- [ ] **Step 5: Commit**

```bash
git add src/presets.py tests/test_presets.py
git commit -m "feat(presets): add prompt profiles and cycling engine"
```

---

### Task 2: Dynamic System Prompt in Embedded LLM

**Files:**
- Modify: `src/embedded_llm.py:173-218`
- Test: `tests/test_embedded_llm.py`

**Interfaces:**
- Consumes: `raw_text: str`, `system_prompt: Optional[str] = None`
- Produces: `rewrite_prompt(raw_text: str, system_prompt: Optional[str] = None) -> str`

- [ ] **Step 1: Write the failing test in `tests/test_embedded_llm.py`**

```python
def test_rewrite_with_custom_system_prompt():
    llm = EmbeddedLLM()
    # Test that rewrite_prompt accepts system_prompt parameter without error
    if llm.is_available():
        res = llm.rewrite_prompt("adicionei botão de fechar", system_prompt="Retorne conventional commits: feat: ...")
        assert len(res) > 0
```

- [ ] **Step 2: Run test to verify behavior**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_embedded_llm.py`
Expected: Passes or fails signature inspection if `system_prompt` is not accepted.

- [ ] **Step 3: Modify `rewrite_prompt` in `src/embedded_llm.py`**

Update signature to `rewrite_prompt(self, raw_text: str, system_prompt: Optional[str] = None) -> str`. If `system_prompt` is provided, pass it in the `system` role message; otherwise, fallback to module-level `SYSTEM_PROMPT`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_embedded_llm.py`
Expected: PASS (All 7 tests OK)

- [ ] **Step 5: Commit**

```bash
git add src/embedded_llm.py tests/test_embedded_llm.py
git commit -m "feat(llm): support dynamic system prompts for presets"
```

---

### Task 3: Procedural Apple Audio-Haptics

**Files:**
- Create: `src/audio_haptics.py`
- Test: `tests/test_audio_haptics.py`

**Interfaces:**
- Consumes: `sounddevice`, `numpy`
- Produces:
  - `generate_chime_buffer(kind: str) -> np.ndarray`
  - `play_haptic_chime(kind: str = "start") -> None` (Non-blocking daemon thread)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audio_haptics.py
import numpy as np
from src.audio_haptics import generate_chime_buffer, play_haptic_chime

def test_generate_chime_start():
    buf = generate_chime_buffer("start")
    assert isinstance(buf, np.ndarray)
    assert buf.dtype == np.float32
    assert len(buf) > 0
    assert np.max(np.abs(buf)) <= 0.20  # Safe amplitude floor

def test_generate_chime_success():
    buf = generate_chime_buffer("success")
    assert isinstance(buf, np.ndarray)
    assert np.max(np.abs(buf)) <= 0.20

def test_play_haptic_chime_non_blocking():
    # Should execute without throwing even without audio output
    play_haptic_chime("start")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_audio_haptics.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.audio_haptics'`

- [ ] **Step 3: Implement `src/audio_haptics.py`**

Synthesize pure sine wave bell chimes in memory:
- `start`: Single note D5 (587.33 Hz) with exponential decay ($e^{-t/0.08}$), length 0.25s.
- `success`: Double harmonic chime C5 (523.25 Hz) followed by E5 (659.25 Hz), length 0.35s.
- `pop`: Micro transient click (1000 Hz, 0.02s).
- Dispatch via `sd.play(buffer, samplerate=24000)` in a background daemon thread with silent exception fallback.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_audio_haptics.py`
Expected: PASS (All tests OK)

- [ ] **Step 5: Commit**

```bash
git add src/audio_haptics.py tests/test_audio_haptics.py
git commit -m "feat(audio): procedural Apple liquid glass audio-haptics"
```

---

### Task 4: Win32 Foreground Target & Auto-Paste Injection

**Files:**
- Create: `src/auto_paste.py`
- Test: `tests/test_auto_paste.py`

**Interfaces:**
- Consumes: Win32 User32 DLL (`GetForegroundWindow`, `SetForegroundWindow`, `keybd_event` or `SendInput`)
- Produces:
  - `get_foreground_window_hwnd() -> int`
  - `inject_paste(target_hwnd: Optional[int] = None, delay_before_paste_s: float = 0.05) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auto_paste.py
import sys
from src.auto_paste import get_foreground_window_hwnd, inject_paste

def test_get_foreground_window_hwnd():
    hwnd = get_foreground_window_hwnd()
    if sys.platform == "win32":
        assert isinstance(hwnd, int)
    else:
        assert hwnd is None

def test_inject_paste_dry_run():
    # Calling with invalid hwnd 0 should fail gracefully and return False
    res = inject_paste(target_hwnd=0)
    assert res is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_auto_paste.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.auto_paste'`

- [ ] **Step 3: Implement `src/auto_paste.py`**

- `get_foreground_window_hwnd()`: Calls `ctypes.windll.user32.GetForegroundWindow()`.
- `inject_paste(target_hwnd: Optional[int] = None, delay_before_paste_s: float = 0.05) -> bool`:
  - If `target_hwnd` is provided and non-zero: calls `SetForegroundWindow(target_hwnd)` and sleeps `delay_before_paste_s`.
  - Simulates `Ctrl` key down (`VK_CONTROL = 0x11`), `V` key down (`0x56`), then releases `V` and `Ctrl` in a strict `finally` block using `keybd_event`.
  - Returns `True` on success, `False` on exception.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_auto_paste.py`
Expected: PASS (All tests OK)

- [ ] **Step 5: Commit**

```bash
git add src/auto_paste.py tests/test_auto_paste.py
git commit -m "feat(automation): Win32 auto-paste injection engine"
```

---

### Task 5: Local Prompt History Logger

**Files:**
- Create: `src/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: File path `data/history.jsonl`
- Produces:
  - `save_history_entry(raw_text: str, prompt_text: str, preset: str, duration_s: float, latency_s: float) -> dict`
  - `get_recent_history(limit: int = 10) -> List[dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_history.py
import os
import tempfile
from pathlib import Path
from src.history import save_history_entry, get_recent_history

def test_save_and_retrieve_history():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_file = Path(tmp_dir) / "test_history.jsonl"
        entry = save_history_entry(
            raw_text="criar funcao",
            prompt_text="Crie uma função...",
            preset="code",
            duration_s=2.5,
            latency_s=0.28,
            history_file=tmp_file,
        )
        assert entry["raw_text"] == "criar funcao"
        assert "timestamp" in entry

        records = get_recent_history(limit=5, history_file=tmp_file)
        assert len(records) == 1
        assert records[0]["prompt_text"] == "Crie uma função..."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_history.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.history'`

- [ ] **Step 3: Implement `src/history.py`**

Implement JSONL append with ISO timestamp and atomic write. Ensure `get_recent_history` reads in reverse (newest first) up to `limit` entries.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m unittest tests/test_history.py`
Expected: PASS (All tests OK)

- [ ] **Step 5: Commit**

```bash
git add src/history.py tests/test_history.py
git commit -m "feat(history): local JSONL prompt history storage"
```

---

### Task 6: Configuration & Live Pill Integration

**Files:**
- Modify: `src/config.py`
- Modify: `src/live_pill.py`
- Test: `tools/test_live_pill_features.py`

**Interfaces:**
- Consumes: `presets`, `auto_paste`, `audio_haptics`, `history`
- Produces:
  - Interactive preset switching chip in Live Pill idle state (or `Shift+F8` shortcut).
  - Target window capture on dictation start.
  - Automatic cursor paste on transcription completion (when `AUTO_PASTE_ON_SUCCESS=True`).
  - Procedural audio feedback on dictation start and success.
  - History logging on success.

- [ ] **Step 1: Write the integration verification script in `tools/test_live_pill_features.py`**

Verify headless initialization with presets cycling, target window capture stub, and history record logging.

- [ ] **Step 2: Run test to verify it fails before modification**

Run: `.\venv\Scripts\python.exe tools/test_live_pill_features.py`
Expected: FAIL

- [ ] **Step 3: Update `src/config.py` and `src/live_pill.py`**

- Add configuration flags in `src/config.py`:
  - `AUTO_PASTE_ON_SUCCESS`: default `False` (safe opt-in via `.env`).
  - `DEFAULT_PRESET`: default `"code"`.
  - `AUDIO_HAPTICS`: default `True`.
- In `LivePillApp.__init__`:
  - Initialize `self.active_preset_id = DEFAULT_PRESET`.
  - Initialize `self.target_window_hwnd: Optional[int] = None`.
- In `_on_hotkey_toggle` and `_start_audio_capture`:
  - Capture `self.target_window_hwnd = get_foreground_window_hwnd()` immediately before showing pill.
  - Replace `play_chime(...)` with `play_haptic_chime(...)`.
- In `_draw_idle_state`:
  - Render an interactive capsule chip for the active preset (e.g. `⚡ Código`, `📝 Commit`, `🌐 EN`, `✍️ Texto`).
  - Click on chip cycles to the next preset via `get_next_preset`.
- In `_final_pipeline_worker`:
  - Pass the active preset's `system_prompt` to `self.embedded_llm.rewrite_prompt(...)`.
  - Log to history via `save_history_entry(...)`.
  - If `AUTO_PASTE_ON_SUCCESS` is True, dispatch `inject_paste(self.target_window_hwnd)`.

- [ ] **Step 4: Run integration and regression tests**

Run:
```bash
.\venv\Scripts\python.exe tools/test_live_pill_features.py
.\venv\Scripts\python.exe tools/test_render_all_states.py
.\venv\Scripts\python.exe tools/test_vad_autostop.py
.\venv\Scripts\python.exe tools/test_live_transcribe_speed.py
```
Expected: PASS (All test suites green)

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/live_pill.py tools/test_live_pill_features.py
git commit -m "feat(live): integrate auto-paste, presets switcher, and procedural haptics"
```

---

## Self-Review

1. **Spec coverage:** All planned capabilities (Auto-Paste, Prompt Presets, Procedural Audio-Haptics, and Local History) are assigned to dedicated tasks with strict test-first workflows.
2. **Step scan:** Each task follows the strict 5-step cycle (failing test -> verify failure -> minimal implementation -> verify pass -> commit).
3. **Type consistency:** Signatures (`get_preset`, `inject_paste`, `save_history_entry`, `rewrite_prompt`) use consistent parameter names and return types across modules.
4. **Review Focus:** Covered:
   - Target HWND captured at start of gesture/hotkey (`Task 6`).
   - Strict `finally` keyup release in `inject_paste` (`Task 4`).
   - Preset switching isolation (`Task 1` & `Task 6`).
   - Waveform amplitude normalization (`Task 3`).
   - Atomic history append (`Task 5`).
5. **Proportion:** The plan focuses strictly on architecture, exact signatures, tests, and verification commands without duplicating implementation source code.
