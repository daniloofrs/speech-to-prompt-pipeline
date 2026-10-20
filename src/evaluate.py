"""Script de avaliacao de qualidade humana e calculo de metricas (Etapa 3).

Fluxo:
1. Permite avaliar prompts pendentes ou um prompt especifico por ID.
2. Registra se houve edicao manual e se houve alucinacao do modelo.
3. Salva a marcacao na tabela 'avaliacoes'.
4. Recalcula as taxas globais (edicao, alucinacao e aceitacao direta) e grava em 'metricas_qualidade'.
5. Exibe um relatorio visual formatado no terminal.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

# Garante que a raiz do projeto esteja no sys.path mesmo se executado diretamente como script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Garante saida em UTF-8 no terminal Windows para acentuacao correta
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg

from src.database import (
    calculate_and_save_metrics,
    get_latest_metrics,
    get_prompt_with_transcription,
    get_unreviewed_prompts,
    init_db,
    save_evaluation,
)

# Configuracao de logs padronizada para terminal
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("evaluate")


def display_metrics_panel(metrics: dict) -> None:
    """Imprime um painel formatado no terminal com as metricas calculadas."""
    border = "=" * 70
    sub_border = "-" * 70

    print("\n" + border)
    print("                  PAINEL DE QUALIDADE DO PIPELINE")
    print(border)
    print(f"Total de Prompts Avaliados:     {metrics['total_avaliado']}")
    print(f"Total com Edição Manual:        {metrics['total_editados']}")
    print(f"Total com Alucinação Detectada: {metrics['total_alucinacoes']}")
    print(sub_border)
    print(
        f"Taxa de Edição Humana:          {metrics['taxa_edicao_pct']:>6.2f}%  (intervenção manual)"
    )
    print(
        f"Taxa de Alucinação:             {metrics['taxa_alucinacao_pct']:>6.2f}%  (dados inventados)"
    )
    print(
        f"Taxa de Aceitação Direta:       {metrics['taxa_aceitacao_direta_pct']:>6.2f}%  (sucesso de primeira)"
    )
    print(border)
    print(f"Snapshot gravado em 'metricas_qualidade' às {metrics['calculado_em']}\n")


def evaluate_single_prompt(
    prompt: dict,
    foi_editado: Optional[bool] = None,
    pedido_editado: Optional[str] = None,
    houve_alucinacao: Optional[bool] = None,
    detalhes_alucinacao: Optional[str] = None,
) -> None:
    """Executa a marcacao manual para um prompt especifico."""
    prompt_id = prompt["prompt_id"]

    border = "-" * 70
    print("\n" + border)
    print(f"AVALIANDO PROMPT ID #{prompt_id} (Arquivo: '{prompt['arquivo']}')")
    print(border)
    print(f"TRANSCRIÇÃO ORIGINAL:\n  \"{prompt['texto_cru']}\"\n")
    print(f"PEDIDO GERADO PELA IA:\n  \"{prompt['pedido']}\"\n")
    print(f"DÚVIDAS LISTADAS:\n  \"{prompt['duvidas']}\"")
    print(border)

    # Coleta interativa caso os parametros nao tenham sido passados via CLI
    if houve_alucinacao is None:
        resp = input("O modelo inventou algo que NÃO estava na fala (alucinação)? [s/N]: ").strip().lower()
        houve_alucinacao = resp in ["s", "sim", "y", "yes"]

    if houve_alucinacao and detalhes_alucinacao is None:
        detalhes_alucinacao = input("Descreva resumidamente o que foi inventado (opcional): ").strip()

    if foi_editado is None:
        resp = input("O pedido final precisou ser editado por você? [s/N]: ").strip().lower()
        foi_editado = resp in ["s", "sim", "y", "yes"]

    if foi_editado and pedido_editado is None:
        print("Digite o texto final editado (ou pressione Enter para usar o gerado):")
        custom_text = input("> ").strip()
        pedido_editado = custom_text if custom_text else prompt["pedido"]

    # 1. Salva a marcacao na tabela 'avaliacoes'
    eval_id = save_evaluation(
        prompt_id=prompt_id,
        foi_editado=foi_editado,
        pedido_editado=pedido_editado,
        houve_alucinacao=houve_alucinacao,
        detalhes_alucinacao=detalhes_alucinacao or None,
    )
    logger.info(f"Avaliação registrada com ID {eval_id} para o Prompt #{prompt_id}.")

    # 2. Recalcula as taxas e grava um snapshot na tabela 'metricas_qualidade'
    updated_metrics = calculate_and_save_metrics()
    display_metrics_panel(updated_metrics)


def run_evaluation(
    prompt_id: Optional[int] = None,
    show_summary_only: bool = False,
    foi_editado: Optional[bool] = None,
    pedido_editado: Optional[str] = None,
    houve_alucinacao: Optional[bool] = None,
    detalhes_alucinacao: Optional[str] = None,
) -> None:
    """Controlador do fluxo de avaliacao."""
    try:
        init_db()
    except psycopg.OperationalError:
        logger.error("Falha ao conectar com o PostgreSQL. O container esta rodando?")
        sys.exit(1)

    if show_summary_only:
        metrics = get_latest_metrics()
        if metrics:
            display_metrics_panel(metrics)
        else:
            print("Nenhuma avaliação foi registrada até o momento.")
        return

    # Se um ID especifico foi solicitado
    if prompt_id is not None:
        prompt = get_prompt_with_transcription(prompt_id)
        if not prompt:
            logger.error(f"Prompt com ID {prompt_id} não encontrado no banco de dados.")
            sys.exit(1)
        evaluate_single_prompt(
            prompt=prompt,
            foi_editado=foi_editado,
            pedido_editado=pedido_editado,
            houve_alucinacao=houve_alucinacao,
            detalhes_alucinacao=detalhes_alucinacao,
        )
        return

    # Se nenhum ID foi passado, busca prompts pendentes de avaliacao
    unreviewed = get_unreviewed_prompts()
    if not unreviewed:
        logger.info("Não há prompts pendentes de avaliação no momento!")
        metrics = get_latest_metrics()
        if metrics:
            display_metrics_panel(metrics)
        return

    logger.info(f"Encontrado(s) {len(unreviewed)} prompt(s) pendente(s) de avaliação.")
    for prompt in unreviewed:
        evaluate_single_prompt(
            prompt=prompt,
            foi_editado=foi_editado,
            pedido_editado=pedido_editado,
            houve_alucinacao=houve_alucinacao,
            detalhes_alucinacao=detalhes_alucinacao,
        )
        # Se for interativo e houver mais itens, pergunta se deseja continuar
        if len(unreviewed) > 1 and prompt != unreviewed[-1]:
            continuar = input("\nDeseja avaliar o próximo prompt? [S/n]: ").strip().lower()
            if continuar in ["n", "nao", "não", "no"]:
                break


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Avaliação de qualidade humana e cálculo de taxas de assertividade (Etapa 3)."
    )
    parser.add_argument(
        "--prompt-id",
        type=int,
        default=None,
        help="ID específico do prompt a avaliar.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Apenas exibe o resumo atual das taxas sem realizar novas avaliações.",
    )
    # Flags opcionais para execucao automatizada (nao interativa)
    parser.add_argument(
        "--edited",
        dest="foi_editado",
        action="store_true",
        default=None,
        help="Marca que o pedido foi editado manualmente.",
    )
    parser.add_argument(
        "--no-edited",
        dest="foi_editado",
        action="store_false",
        help="Marca que o pedido NÃO precisou de edição.",
    )
    parser.add_argument(
        "--edited-text",
        type=str,
        default=None,
        help="Texto da versão editada manualmente.",
    )
    parser.add_argument(
        "--hallucination",
        dest="houve_alucinacao",
        action="store_true",
        default=None,
        help="Marca que houve alucinação do modelo.",
    )
    parser.add_argument(
        "--no-hallucination",
        dest="houve_alucinacao",
        action="store_false",
        help="Marca que NÃO houve alucinação.",
    )
    parser.add_argument(
        "--hallucination-details",
        type=str,
        default=None,
        help="Descrição dos detalhes da alucinação.",
    )

    args = parser.parse_args()
    run_evaluation(
        prompt_id=args.prompt_id,
        show_summary_only=args.summary,
        foi_editado=args.foi_editado,
        pedido_editado=args.edited_text,
        houve_alucinacao=args.houve_alucinacao,
        detalhes_alucinacao=args.hallucination_details,
    )


if __name__ == "__main__":
    main()
