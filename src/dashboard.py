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
    <title>Speech-to-Prompt Pipeline | Apple Fluid Dashboard</title>
    <style>
        :root {{
            font: 100%/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI Variable Display", system-ui, sans-serif;
            --bg-color: #000000;
            --surface-glass: rgba(28, 28, 30, 0.68);
            --surface-elevated: rgba(44, 44, 46, 0.55);
            --border-subtle: rgba(255, 255, 255, 0.09);
            --specular-top: rgba(255, 255, 255, 0.22);
            --text-main: #f5f5f7;
            --text-muted: #86868b;
            --accent-blue: #0a84ff;
            --accent-green: #30d158;
            --accent-orange: #ff9f0a;
            --accent-red: #ff453a;
            --accent-purple: #bf5af2;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: radial-gradient(circle at 50% -10%, #1c1c24 0%, #000000 65%);
            color: var(--text-main);
            min-height: 100vh;
            padding: 0 24px 48px;
            -webkit-font-smoothing: antialiased;
        }}
        .container {{ max-width: 1240px; margin: 0 auto; }}
        header {{
            position: sticky;
            top: 16px;
            z-index: 20;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin: 16px 0 36px;
            padding: 18px 24px;
            background: rgba(22, 22, 24, 0.72);
            backdrop-filter: blur(24px) saturate(180%);
            -webkit-backdrop-filter: blur(24px) saturate(180%);
            border-radius: 20px;
            border: 1px solid var(--border-subtle);
            border-top: 1px solid var(--specular-top);
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.45);
        }}
        .title-group h1 {{
            font-size: clamp(1.35rem, 2.2vw, 1.75rem);
            font-weight: 700;
            line-height: 1.1;
            letter-spacing: -0.022em;
            font-optical-sizing: auto;
            color: var(--text-main);
        }}
        .title-group p {{
            color: var(--text-muted);
            font-size: 0.9rem;
            letter-spacing: -0.005em;
            margin-top: 2px;
        }}
        .tags {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .tag {{
            background: var(--surface-elevated);
            border: 1px solid var(--border-subtle);
            border-top: 1px solid var(--specular-top);
            padding: 5px 12px;
            border-radius: 999px;
            font-size: 0.78rem;
            letter-spacing: 0.01em;
            color: var(--text-main);
            font-weight: 500;
            user-select: none;
            transition: transform 100ms ease-out, background 150ms ease;
        }}
        .tag:active {{ transform: scale(0.96); }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 36px;
        }}
        .card {{
            background: var(--surface-glass);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px) saturate(180%);
            border: 1px solid var(--border-subtle);
            border-top: 1px solid var(--specular-top);
            border-radius: 20px;
            padding: 22px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
            transition: transform 120ms ease-out, background 150ms ease;
        }}
        .card:active {{
            transform: scale(0.975);
        }}
        .card-label {{
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.04em;
            margin-bottom: 10px;
        }}
        .card-value {{
            font-size: clamp(1.8rem, 3vw, 2.35rem);
            font-weight: 700;
            line-height: 1.05;
            letter-spacing: -0.03em;
            font-optical-sizing: auto;
            color: var(--text-main);
        }}
        .card-value.green {{ color: var(--accent-green); }}
        .card-value.orange {{ color: var(--accent-orange); }}
        .card-value.red {{ color: var(--accent-red); }}
        .card-value.blue {{ color: var(--accent-blue); }}
        .card-subtext {{
            font-size: 0.8rem;
            letter-spacing: 0.005em;
            color: var(--text-muted);
            margin-top: 6px;
        }}
        .section-title {{
            font-size: 1.25rem;
            font-weight: 600;
            letter-spacing: -0.018em;
            margin-bottom: 16px;
            color: var(--text-main);
        }}
        .table-container {{
            background: var(--surface-glass);
            backdrop-filter: blur(24px) saturate(180%);
            -webkit-backdrop-filter: blur(24px) saturate(180%);
            border: 1px solid var(--border-subtle);
            border-top: 1px solid var(--specular-top);
            border-radius: 20px;
            box-shadow: 0 14px 36px rgba(0, 0, 0, 0.4);
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
            text-align: left;
        }}
        th {{
            background: rgba(255, 255, 255, 0.03);
            padding: 15px 18px;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            font-weight: 600;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-subtle);
        }}
        td {{
            padding: 16px 18px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            vertical-align: top;
        }}
        tr:last-child td {{ border-bottom: none; }}
        .id-col {{ width: 64px; font-weight: 600; color: var(--text-muted); }}
        .text-col {{ max-width: 390px; word-break: break-word; line-height: 1.48; }}
        .status-col {{ width: 170px; }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 0.74rem;
            letter-spacing: 0.01em;
            font-weight: 600;
            margin-bottom: 4px;
        }}
        .badge-success {{ background: rgba(48, 209, 88, 0.16); color: #30d158; border: 1px solid rgba(48, 209, 88, 0.32); }}
        .badge-edit {{ background: rgba(255, 159, 10, 0.16); color: #ff9f0a; border: 1px solid rgba(255, 159, 10, 0.32); }}
        .badge-danger {{ background: rgba(255, 69, 58, 0.16); color: #ff453a; border: 1px solid rgba(255, 69, 58, 0.32); }}
        .badge-info {{ background: rgba(10, 132, 255, 0.16); color: #0a84ff; border: 1px solid rgba(10, 132, 255, 0.32); }}
        .badge-warning {{ background: rgba(191, 90, 242, 0.16); color: #bf5af2; border: 1px solid rgba(191, 90, 242, 0.32); }}
        footer {{
            margin-top: 36px;
            text-align: center;
            color: var(--text-muted);
            font-size: 0.82rem;
            letter-spacing: 0.01em;
        }}
        @media (prefers-reduced-motion: reduce) {{
            *, .card, .tag, .liquid-island {{
                transition: opacity 200ms ease !important;
                transform: none !important;
            }}
        }}
        @media (prefers-reduced-transparency: reduce) {{
            header, .card, .table-container, .liquid-island {{
                background: #1c1c1e !important;
                backdrop-filter: none !important;
                -webkit-backdrop-filter: none !important;
            }}
        }}

        /* --- Liquid Glass Refraction Shader (Shu Ding inspired) --- */
        .liquid-island {{
            position: fixed;
            top: 24px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 1000;
            width: 440px;
            max-width: calc(100vw - 32px);
            padding: 10px 18px;
            border-radius: 9999px;
            cursor: grab;
            user-select: none;
            touch-action: none;
            background: rgba(18, 18, 22, 0.48);
            backdrop-filter: url(#liquid-glass-filter) blur(0.25px) contrast(1.15) brightness(1.04) saturate(1.18);
            -webkit-backdrop-filter: url(#liquid-glass-filter) blur(0.25px) contrast(1.15) brightness(1.04) saturate(1.18);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-top: 1px solid rgba(255, 255, 255, 0.45);
            box-shadow: 0 20px 48px rgba(0, 0, 0, 0.65), 0 0 0 1px rgba(255, 255, 255, 0.08), inset 0 1px 2px rgba(255, 255, 255, 0.4);
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            transition: box-shadow 150ms ease, border-color 150ms ease, width 250ms cubic-bezier(0.16, 1, 0.3, 1);
        }}
        .liquid-island:active {{
            cursor: grabbing;
            box-shadow: 0 28px 64px rgba(0, 0, 0, 0.8), 0 0 0 1px rgba(255, 255, 255, 0.22);
        }}
        .liquid-island.no-svg {{
            backdrop-filter: blur(24px) saturate(180%) !important;
            -webkit-backdrop-filter: blur(24px) saturate(180%) !important;
        }}
        .liquid-island.expanded {{
            width: 580px;
            border-radius: 28px;
            padding: 16px 22px;
        }}
        .island-brand {{
            display: flex;
            align-items: center;
            gap: 9px;
            color: #f5f5f7;
            font-size: 0.82rem;
            font-weight: 600;
            letter-spacing: -0.01em;
            pointer-events: none;
        }}
        .island-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #30d158;
            box-shadow: 0 0 10px #30d158;
            animation: island-pulse 2s infinite ease-in-out;
        }}
        @keyframes island-pulse {{
            0%, 100% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: 0.4; transform: scale(0.8); }}
        }}
        .island-controls {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .island-btn {{
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-top: 1px solid rgba(255, 255, 255, 0.35);
            color: #f5f5f7;
            border-radius: 999px;
            padding: 5px 12px;
            font-size: 0.74rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 150ms ease;
        }}
        .island-btn:hover {{
            background: rgba(255, 255, 255, 0.22);
        }}
        .island-btn:active {{
            transform: scale(0.95);
        }}
        .island-btn.active {{
            background: #0a84ff;
            border-color: #0a84ff;
            color: #ffffff;
            box-shadow: 0 0 12px rgba(10, 132, 255, 0.4);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-group">
                <h1>Speech-to-Prompt Pipeline</h1>
                <p>Monitoramento de Qualidade & Telemetria de Engenharia de Dados</p>
            </div>
            <div class="tags">
                <span class="tag">PostgreSQL 16</span>
                <span class="tag">Faster-Whisper CUDA</span>
                <span class="tag">Ollama Qwen2.5</span>
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

    <!-- Filtro SVG com Displacement Map baseado no algoritmo de Liquid Glass (Shu Ding) -->
    <svg id="liquid-glass-svg" style="position: absolute; width: 0; height: 0; pointer-events: none; overflow: hidden;" aria-hidden="true">
        <defs>
            <filter id="liquid-glass-filter" filterUnits="userSpaceOnUse" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
                <feImage id="liquid-fe-image" href="" result="displacementMap" preserveAspectRatio="none" />
                <feDisplacementMap id="liquid-fe-disp" in="SourceGraphic" in2="displacementMap" xChannelSelector="R" yChannelSelector="G" scale="32" result="displaced" />
            </filter>
        </defs>
    </svg>

    <!-- Pílula Flutuante Interativa Liquid Glass -->
    <div class="liquid-island" id="liquidIsland" title="Arraste livremente pela tela para ver a refração física dos dados">
        <div class="island-brand">
            <span class="island-dot"></span>
            <span>Liquid Glass (SVG Lens)</span>
        </div>
        <div class="island-controls">
            <button type="button" class="island-btn active" id="toggleRefractBtn" title="Alternar entre Refração Óptica SVG e Blur Padrão">Refração: ON</button>
            <button type="button" class="island-btn" id="resizeIslandBtn" title="Alternar tamanho da lente">Expandir</button>
        </div>
    </div>

    <script>
        (function() {{
            // Algoritmo matematico de Signed Distance Field (SDF) para retangulo arredondado (Shu Ding)
            function roundedRectSDF(x, y, w, h, r) {{
                const qx = Math.abs(x) - w + r;
                const qy = Math.abs(y) - h + r;
                return Math.min(Math.max(qx, qy), 0) + Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) - r;
            }}

            // Geracao do mapa de deslocamento optico em Canvas 2D
            function createDisplacementMap(width, height, radius, bevel) {{
                const canvas = document.createElement('canvas');
                canvas.width = Math.max(1, width);
                canvas.height = Math.max(1, height);
                const ctx = canvas.getContext('2d');
                const imgData = ctx.createImageData(canvas.width, canvas.height);
                const data = imgData.data;

                const halfW = canvas.width / 2;
                const halfH = canvas.height / 2;
                const r = Math.min(radius, halfW, halfH);
                const b = Math.max(4, Math.min(bevel, halfH * 0.9));

                for (let y = 0; y < canvas.height; y++) {{
                    for (let x = 0; x < canvas.width; x++) {{
                        const idx = (y * canvas.width + x) * 4;
                        const px = x - halfW;
                        const py = y - halfH;
                        const dist = roundedRectSDF(px, py, halfW, halfH, r);

                        if (dist > 0) {{
                            // Fora do retangulo arredondado - sem deslocamento e transparente
                            data[idx] = 128;
                            data[idx + 1] = 128;
                            data[idx + 2] = 128;
                            data[idx + 3] = 0;
                        }} else if (dist >= -b) {{
                            // Faixa de curvatura da borda (borda refrativa de vidro)
                            const dx = roundedRectSDF(px + 1, py, halfW, halfH, r) - roundedRectSDF(px - 1, py, halfW, halfH, r);
                            const dy = roundedRectSDF(px, py + 1, halfW, halfH, r) - roundedRectSDF(px, py - 1, halfW, halfH, r);
                            const len = Math.hypot(dx, dy) || 1;
                            const nx = dx / len;
                            const ny = dy / len;

                            // Perfil senoidal da curvatura optica da lente
                            const depth = (dist + b) / b; // 0 no interior, 1 na extremidade
                            const factor = Math.sin(depth * Math.PI * 0.5);

                            // Codifica nos canais R (deslocamento X) e G (deslocamento Y), centralizado em 128 (0 offset)
                            data[idx] = Math.round(128 + nx * factor * 115);
                            data[idx + 1] = Math.round(128 + ny * factor * 115);
                            data[idx + 2] = 128;
                            data[idx + 3] = 255;
                        }} else {{
                            // Centro plano da lente - passagem sem distorcao
                            data[idx] = 128;
                            data[idx + 1] = 128;
                            data[idx + 2] = 128;
                            data[idx + 3] = 255;
                        }}
                    }}
                }}

                ctx.putImageData(imgData, 0, 0);
                return canvas.toDataURL();
            }}

            const island = document.getElementById('liquidIsland');
            const filter = document.getElementById('liquid-glass-filter');
            const feImage = document.getElementById('liquid-fe-image');
            const feDisp = document.getElementById('liquid-fe-disp');
            const toggleBtn = document.getElementById('toggleRefractBtn');
            const resizeBtn = document.getElementById('resizeIslandBtn');

            function updateShader() {{
                if (!island || !feImage || !feDisp || !filter) return;
                const rect = island.getBoundingClientRect();
                const w = Math.round(rect.width);
                const h = Math.round(rect.height);
                const isExp = island.classList.contains('expanded');
                const radius = isExp ? 28 : h / 2;
                const bevel = isExp ? 24 : Math.min(22, h * 0.45);

                const dataUrl = createDisplacementMap(w, h, radius, bevel);

                filter.setAttribute('x', '0');
                filter.setAttribute('y', '0');
                filter.setAttribute('width', w + 'px');
                filter.setAttribute('height', h + 'px');

                feImage.setAttribute('width', w + 'px');
                feImage.setAttribute('height', h + 'px');
                feImage.setAttribute('href', dataUrl);

                feDisp.setAttribute('scale', isExp ? '40' : '32');
            }}

            // Alterna entre refracao SVG real e backdrop-filter blur generico
            if (toggleBtn) {{
                toggleBtn.addEventListener('click', (e) => {{
                    e.stopPropagation();
                    const isNoSvg = island.classList.toggle('no-svg');
                    toggleBtn.classList.toggle('active', !isNoSvg);
                    toggleBtn.textContent = isNoSvg ? 'Refração: OFF' : 'Refração: ON';
                }});
            }}

            // Alterna dimensao da lente
            if (resizeBtn) {{
                resizeBtn.addEventListener('click', (e) => {{
                    e.stopPropagation();
                    const isExp = island.classList.toggle('expanded');
                    resizeBtn.classList.toggle('active', isExp);
                    resizeBtn.textContent = isExp ? 'Reduzir' : 'Expandir';
                    setTimeout(updateShader, 260);
                }});
            }}

            // Sistema de arrasto suave (Drag & Drop) com limites de janela
            let isDragging = false;
            let startX = 0;
            let startY = 0;
            let initialLeft = 0;
            let initialTop = 0;

            island.addEventListener('pointerdown', (e) => {{
                if (e.target.tagName === 'BUTTON') return;
                isDragging = true;
                island.setPointerCapture(e.pointerId);

                const rect = island.getBoundingClientRect();
                startX = e.clientX;
                startY = e.clientY;
                initialLeft = rect.left;
                initialTop = rect.top;

                // Transfere de transform center para posicionamento fixo pixelado
                island.style.left = initialLeft + 'px';
                island.style.top = initialTop + 'px';
                island.style.transform = 'none';
            }});

            window.addEventListener('pointermove', (e) => {{
                if (!isDragging) return;
                const dx = e.clientX - startX;
                const dy = e.clientY - startY;

                let newX = initialLeft + dx;
                let newY = initialTop + dy;

                // Restringe a visao dentro dos limites da janela
                const margin = 8;
                const maxW = window.innerWidth - island.offsetWidth - margin;
                const maxH = window.innerHeight - island.offsetHeight - margin;

                newX = Math.max(margin, Math.min(newX, maxW));
                newY = Math.max(margin, Math.min(newY, maxH));

                island.style.left = newX + 'px';
                island.style.top = newY + 'px';
            }});

            window.addEventListener('pointerup', (e) => {{
                if (isDragging) {{
                    isDragging = false;
                    try {{ island.releasePointerCapture(e.pointerId); }} catch (_) {{}}
                }}
            }});

            // Inicializa o shader e atualiza ao redimensionar a janela
            window.addEventListener('resize', updateShader);
            // Aguarda carregamento inicial de fontes/layout
            requestAnimationFrame(() => {{
                setTimeout(updateShader, 50);
            }});
        }})();
    </script>
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
