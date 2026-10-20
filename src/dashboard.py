"""Dashboard web e de terminal para monitoramento do pipeline (Etapa 4).

Exibe:
- Metricas de qualidade e taxas do LLM (Taxa de Aceitacao Direta, Edicao, Alucinacao)
- Volumetria de ingestao (Audios processados e duracao total)
- Tabela analitica com transcricoes, prompts reescritos e avaliacoes humanas
"""

import argparse
import html
import http.server
import logging
import socketserver
import sys
import webbrowser
from pathlib import Path

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

from src.database import get_dashboard_data, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dashboard")


def render_html_dashboard(data: dict) -> str:
    """Gera o HTML moderno e responsivo do painel de monitoramento."""
    metrics = data["latest_metrics"]
    audio_stats = data["audio_stats"]
    prompts_stats = data["prompts_stats"]
    items = data["recent_items"]

    # Gera as linhas da tabela
    table_rows = []
    for item in items:
        status_badges = []
        if item["avaliacao_id"] is None:
            if item["prompt_id"] is None:
                status_badges.append(
                    '<span class="badge badge-warning">Aguardando LLM</span>'
                )
            else:
                status_badges.append(
                    '<span class="badge badge-info">Pendente de Avaliação</span>'
                )
        else:
            if not item["foi_editado"] and not item["houve_alucinacao"]:
                status_badges.append(
                    '<span class="badge badge-success">Aceito Direto</span>'
                )
            if item["foi_editado"]:
                status_badges.append(
                    '<span class="badge badge-edit">Editado</span>'
                )
            if item["houve_alucinacao"]:
                status_badges.append(
                    '<span class="badge badge-danger">Alucinação</span>'
                )

        raw_text = html.escape(item["transcricao_crua"] or "(vazio)")
        prompt_text = html.escape(item["pedido"] or "(não reescrito)")
        file_name = html.escape(item["arquivo"])

        row_html = f"""
        <tr>
            <td class="id-col">#{item['transcricao_id']}</td>
            <td><strong>{file_name}</strong><br><small class="text-muted">{item['duracao_s']:.1f}s</small></td>
            <td class="text-col">{raw_text}</td>
            <td class="text-col">{prompt_text}</td>
            <td class="status-col">{" ".join(status_badges)}</td>
        </tr>
        """
        table_rows.append(row_html)

    rows_str = (
        "".join(table_rows)
        if table_rows
        else "<tr><td colspan='5' class='text-center'>Nenhum registro encontrado.</td></tr>"
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Speech-to-Prompt Pipeline | Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --border-color: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-blue: #38bdf8;
            --accent-green: #34d399;
            --accent-orange: #fbbf24;
            --accent-red: #f87171;
            --accent-purple: #a78bfa;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            padding: 32px 24px;
            line-height: 1.5;
        }}
        .container {{ max-width: 1280px; margin: 0 auto; }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 28px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
        }}
        .title-group h1 {{ font-size: 1.75rem; font-weight: 700; color: #fff; }}
        .title-group p {{ color: var(--text-muted); font-size: 0.95rem; }}
        .tags {{ display: flex; gap: 8px; }}
        .tag {{
            background: #0f172a;
            border: 1px solid var(--border-color);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.8rem;
            color: var(--accent-blue);
            font-weight: 500;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        .card {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
        }}
        .card-label {{
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
        }}
        .card-value {{
            font-size: 2rem;
            font-weight: 700;
            color: #fff;
        }}
        .card-value.green {{ color: var(--accent-green); }}
        .card-value.orange {{ color: var(--accent-orange); }}
        .card-value.red {{ color: var(--accent-red); }}
        .card-value.blue {{ color: var(--accent-blue); }}
        .card-subtext {{
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 4px;
        }}
        .section-title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 16px;
            color: #fff;
        }}
        .table-container {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
            text-align: left;
        }}
        th {{
            background-color: #172033;
            padding: 14px 16px;
            font-weight: 600;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-color);
        }}
        td {{
            padding: 16px;
            border-bottom: 1px solid #24324a;
            vertical-align: top;
        }}
        tr:last-child td {{ border-bottom: none; }}
        .id-col {{ width: 60px; font-weight: 600; color: var(--text-muted); }}
        .text-col {{ max-width: 380px; word-break: break-word; line-height: 1.45; }}
        .status-col {{ width: 160px; }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-bottom: 4px;
        }}
        .badge-success {{ background: rgba(52, 211, 153, 0.15); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.3); }}
        .badge-edit {{ background: rgba(251, 191, 36, 0.15); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.3); }}
        .badge-danger {{ background: rgba(248, 113, 113, 0.15); color: #f87171; border: 1px solid rgba(248, 113, 113, 0.3); }}
        .badge-info {{ background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }}
        .badge-warning {{ background: rgba(167, 139, 250, 0.15); color: #a78bfa; border: 1px solid rgba(167, 139, 250, 0.3); }}
        footer {{
            margin-top: 32px;
            text-align: center;
            color: var(--text-muted);
            font-size: 0.85rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-group">
                <h1>Speech-to-Prompt Pipeline</h1>
                <p>Dashboard de Monitoramento de Qualidade & Métricas de Engenharia de Dados</p>
            </div>
            <div class="tags">
                <span class="tag">PostgreSQL 16</span>
                <span class="tag">Faster-Whisper CUDA</span>
                <span class="tag">Ollama Qwen2.5:7b</span>
            </div>
        </header>

        <div class="grid">
            <div class="card">
                <div class="card-label">Áudios Ingeridos</div>
                <div class="card-value blue">{audio_stats['total_audios']}</div>
                <div class="card-subtext">{audio_stats['total_segundos']:.1f}s total processados</div>
            </div>
            <div class="card">
                <div class="card-label">Prompts Gerados</div>
                <div class="card-value blue">{prompts_stats['total_prompts']}</div>
                <div class="card-subtext">{metrics['total_avaliado']} avaliados manualmente</div>
            </div>
            <div class="card">
                <div class="card-label">Aceitação Direta</div>
                <div class="card-value green">{metrics['taxa_aceitacao_direta_pct']:.1f}%</div>
                <div class="card-subtext">Sem edição nem alucinação</div>
            </div>
            <div class="card">
                <div class="card-label">Taxa de Edição</div>
                <div class="card-value orange">{metrics['taxa_edicao_pct']:.1f}%</div>
                <div class="card-subtext">{metrics['total_editados']} prompts editados</div>
            </div>
            <div class="card">
                <div class="card-label">Taxa de Alucinação</div>
                <div class="card-value red">{metrics['taxa_alucinacao_pct']:.1f}%</div>
                <div class="card-subtext">{metrics['total_alucinacoes']} com dados inventados</div>
            </div>
        </div>

        <h2 class="section-title">Últimas Transcrições e Pedidos Processados</h2>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Arquivo</th>
                        <th>Transcrição Crua</th>
                        <th>Pedido Reescrito (LLM)</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_str}
                </tbody>
            </table>
        </div>

        <footer>
            Speech-to-Prompt Pipeline &bull; Portfólio de Engenharia de Dados &bull; Danilo Fernandes
        </footer>
    </div>
</body>
</html>
"""


class DashboardRequestHandler(http.server.BaseHTTPRequestHandler):
    """Handler HTTP para servir a interface do dashboard."""

    def do_GET(self) -> None:
        try:
            data = get_dashboard_data()
            html_content = render_html_dashboard(data).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_content)))
            self.end_headers()
            self.wfile.write(html_content)
        except Exception as error:
            logger.error(f"Erro ao processar requisicao do dashboard: {error}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Erro interno ao carregar dados do banco.")

    def log_message(self, format: str, *args) -> None:
        # Suprime logs verbosos de cada request HTTP no terminal
        pass


def run_cli_dashboard() -> None:
    """Exibe o painel analitico diretamente no terminal."""
    data = get_dashboard_data()
    m = data["latest_metrics"]
    audio = data["audio_stats"]
    prompts = data["prompts_stats"]
    items = data["recent_items"]

    border = "=" * 80
    sub_border = "-" * 80

    print("\n" + border)
    print("           SPEECH-TO-PROMPT PIPELINE | DASHBOARD DE QUALIDADE")
    print(border)
    print(
        f"Áudios Ingeridos: {audio['total_audios']} ({audio['total_segundos']}s) | "
        f"Prompts Gerados: {prompts['total_prompts']} | "
        f"Avaliados: {m['total_avaliado']}"
    )
    print(sub_border)
    print(f"Taxa de Aceitação Direta: {m['taxa_aceitacao_direta_pct']:>6.2f}%  (Sem edição nem alucinação)")
    print(f"Taxa de Edição Humana:    {m['taxa_edicao_pct']:>6.2f}%  ({m['total_editados']} intervenções manuais)")
    print(f"Taxa de Alucinação:       {m['taxa_alucinacao_pct']:>6.2f}%  ({m['total_alucinacoes']} dados inventados)")
    print(border)
    print("ÚLTIMOS REGISTROS DO PIPELINE:")
    for item in items[:5]:
        status = "Pendente"
        if item["avaliacao_id"]:
            if not item["foi_editado"] and not item["houve_alucinacao"]:
                status = "Aceito Direto"
            elif item["foi_editado"]:
                status = "Editado"
            elif item["houve_alucinacao"]:
                status = "Alucinação"

        print(f"#{item['transcricao_id']} [{item['arquivo']}] - Status: {status}")
        raw_snippet = item["transcricao_crua"][:60].replace("\n", " ") + "..." if item["transcricao_crua"] else "(vazio)"
        prompt_snippet = item["pedido"][:60].replace("\n", " ") + "..." if item["pedido"] else "(não reescrito)"
        print(f"  Fala:   {raw_snippet}")
        print(f"  Prompt: {prompt_snippet}\n")
    print(border + "\n")


def run_web_dashboard(port: int = 8080, open_browser: bool = False) -> None:
    """Inicia o servidor web local na porta especificada."""
    try:
        init_db()
    except psycopg.OperationalError:
        logger.error("Falha ao conectar com o PostgreSQL. O container esta rodando?")
        sys.exit(1)

    url = f"http://localhost:{port}"
    logger.info(f"Dashboard iniciado com sucesso! Acesse no navegador: {url}")
    print(f"\n>>> Dashboard disponível em: {url} (pressione Ctrl+C para encerrar)\n")

    if open_browser:
        webbrowser.open(url)

    # Permite reutilizar a porta imediatamente ao reiniciar
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), DashboardRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Encerrando servidor do dashboard...")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dashboard de métricas e volumetria do Speech-to-Prompt Pipeline."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Porta para o servidor web do dashboard (padrão: 8080).",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Exibe o dashboard no formato de terminal em vez de subir o servidor web.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Abre o navegador automaticamente ao iniciar o dashboard web.",
    )

    args = parser.parse_args()
    if args.cli:
        run_cli_dashboard()
    else:
        run_web_dashboard(port=args.port, open_browser=args.open)


if __name__ == "__main__":
    main()
