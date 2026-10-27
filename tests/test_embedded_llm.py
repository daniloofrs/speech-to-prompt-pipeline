"""Testes unitários e de integração para o motor EmbeddedLLM (GGUF in-process)."""

import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

# Garante que a raiz do projeto esteja no sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import unittest
from unittest.mock import MagicMock, patch

from src.embedded_llm import EmbeddedLLM, _DLL_HANDLES


class TestEmbeddedLLM(unittest.TestCase):
    def test_dll_handles_retained(self):
        """Verifica se os handles de DLLs do Windows são retidos no módulo para evitar GC."""
        if sys.platform == "win32":
            self.assertIsInstance(_DLL_HANDLES, list)

    def test_empty_raw_text_returns_empty(self):
        """Entrada vazia ou somente espaços deve retornar string vazia imediatamente."""
        llm = EmbeddedLLM()
        self.assertEqual(llm.rewrite_prompt(""), "")
        self.assertEqual(llm.rewrite_prompt("   "), "")
        self.assertEqual(llm.rewrite_prompt(None), "")

    def test_fallback_when_model_file_missing(self):
        """Quando o arquivo de modelo não existe, deve retornar o texto original sem quebrar."""
        llm = EmbeddedLLM(model_path="models/non_existent_model.gguf")
        self.assertFalse(llm.is_available())
        result = llm.rewrite_prompt("cria um script em python")
        self.assertEqual(result, "cria um script em python")

    def test_prefix_stripping_regex(self):
        """Valida a remoção de prefixos indesejados como PEDIDO:, **Prompt:**, etc."""
        llm = EmbeddedLLM()
        mock_llama = MagicMock()
        llm._llm = mock_llama
        llm._is_loaded = True

        test_cases = [
            ("PEDIDO: Crie uma função em Python.", "Crie uma função em Python."),
            ("pedido:   Escreva uma query SQL.", "Escreva uma query SQL."),
            ("**Prompt:** Desenvolva uma API REST.", "Desenvolva uma API REST."),
            ("**Comando**: Crie um Dockerfile.", "Crie um Dockerfile."),
            ('"Crie uma classe com tipagem estrita."', "Crie uma classe com tipagem estrita."),
            ("'Gere um script bash.'", "Gere um script bash."),
        ]

        for raw_llm_output, expected_clean in test_cases:
            mock_llama.create_chat_completion.return_value = {
                "choices": [{"message": {"content": raw_llm_output}}]
            }
            res = llm.rewrite_prompt("teste de entrada")
            self.assertEqual(res, expected_clean)

    def test_thread_safety_concurrent_calls(self):
        """Verifica se chamadas concorrentes ao EmbeddedLLM respeitam o Lock interno."""
        llm = EmbeddedLLM()
        mock_llama = MagicMock()
        mock_llama.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "Crie um teste unitário."}}]
        }
        llm._llm = mock_llama
        llm._is_loaded = True

        results = []
        errors = []

        def worker():
            try:
                for _ in range(10):
                    res = llm.rewrite_prompt("teste concorrente")
                    results.append(res)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 50)
        self.assertTrue(all(r == "Crie um teste unitário." for r in results))

    def test_real_embedded_llm_inference(self):
        """Teste de integração real com o modelo GGUF presente na pasta models/."""
        llm = EmbeddedLLM()
        if not llm.is_available():
            self.skipTest("Modelo GGUF não disponível no disco.")

        loaded = llm.load_model()
        self.assertTrue(loaded, "Falha ao carregar modelo GGUF real")

        output = llm.rewrite_prompt("hum eh queria que voce fizesse uma funcao pra ordenar lista em python")
        self.assertIsInstance(output, str)
        self.assertGreater(len(output), 5)
        self.assertFalse(output.lower().startswith("pedido:"))
        self.assertFalse(output.lower().startswith("prompt:"))
        print(f"\n[PASS] Saída real do EmbeddedLLM: {output}")


if __name__ == "__main__":
    unittest.main()
