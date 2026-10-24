"""Painel Dash com duas abas:

- **Brasil — MJ/Sinesp**: BancoVDE 2016–2026 por UF e, quando o evento é
  detalhado por município, pelos municípios da UF escolhida (malhas do IBGE).
  Série mensal (com previsão SARIMA dos 12 meses seguintes) e anual de todo o
  período, mapa de calor local × ano, e mapa, ranking e tabela do intervalo de
  anos escolhido;
- **Distrito Federal — SSP-DF**: balanços 2015–2026 por Região Administrativa,
  com os mapas folium de `tcc.mapas` (coroplético e bolhas) abertos já com a
  seleção dos filtros do painel, série mensal (com previsão) e anual de todo o
  período, mapa de calor RA × ano, e ranking e tabela do intervalo de anos
  escolhido.
"""

import dataclasses
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
from dash import ALL, Dash, Input, Output, Patch, State, ctx, dash_table, dcc, html, no_update

from tcc.malhas import malha_municipios, malha_uf
from tcc.mapas import mapa_bolhas, mapa_coropletico, preparar_dados
from tcc.painel.dados import (
    BASE_TAXA_MJ,
    HOMICIDIO_SSP,
    NOMES_SSP,
    MESES,
    BaseMJ,
    BaseSSP,
    carregar_mj,
    carregar_ssp,
    ssp_por_ra_ano,
)
from tcc.populacao import BASE_TAXA, RAS_DESMEMBRADAS
from tcc.previsao import HORIZONTE, MEDIA_MIN, MESES_MIN, ZEROS_MAX, motivo_sem_previsao, prever

BRASIL = "BR"
UF_DF = "DF"
EVENTO_PADRAO = "Homicídio doloso"

# Por que o MJ e a aba Distrito Federal (SSP-DF) dão números diferentes no DF
# para eventos de mesmo nome. Feminicídio, latrocínio, lesão corporal seguida de
# morte e roubo de veículo batem e não estão aqui.
DIFERENCA_DF_MJ: dict[str, str] = {
    "Homicídio doloso": (
        "O MJ conta vítimas, sem o feminicídio (desde 2023, é o mesmo número de vítimas "
        "do balanço do DF). A aba Distrito Federal conta ocorrências e inclui o feminicídio, que os "
        "balanços das RAs não separam: lá o número é maior."),
    "Tentativa de homicídio": (
        "O MJ conta vítimas, sem a tentativa de feminicídio. A aba Distrito Federal conta "
        "ocorrências e inclui a tentativa de feminicídio: lá o número é maior."),
    "Estupro": (
        "Até 2024, o estupro dos balanços das RAs inclui o estupro de vulnerável. Para "
        "comparar com a aba Distrito Federal nesses anos, use “Estupro (total)”."),
    "Estupro de vulnerável": (
        "Na aba Distrito Federal, o estupro de vulnerável só aparece separado a partir de "
        "2025; antes está dentro do estupro. Para comparar, use “Estupro (total)”."),
    "Estupro (total)": (
        "Comparável a estupro + estupro de vulnerável da aba Distrito Federal, mas só bate "
        "(até 5% de diferença) em 2016, 2019, 2022, 2023 e 2026. Em 2018, 2020, 2021 e "
        "2024 o MJ tem 14% a 24% a mais, e em 2025, 12% a menos, sem causa identificada; "
        "2024 e 2025 somados quase coincidem, o que sugere casos datados em anos "
        "diferentes nas duas fontes."),
    "Tráfico de drogas": (
        "A aba Distrito Federal não tem os registros da Polícia Federal; por isso, no DF, "
        "o filtro de abrangência abre só com a Estadual. Assim, os dois batem exatamente "
        "de 2016 a 2021 e com até 2% de diferença depois, menos em 2025: o MJ tem 5% a "
        "mais, sem causa identificada."),
}

# Na taxa por município, cidades muito pequenas dominam o ranking com uma ou
# duas ocorrências. O ranking (só ele — mapa e tabela mostram todas) ignora as
# abaixo deste porte.
POP_MIN_RANKING = 20_000
TOP_RANKING = 15

# Paleta: série única em azul; rampa sequencial azul (claro → escuro) nos mapas
# plotly; o laranja é o segundo slot categórico (RA sem delegacia).
AZUL = "#2a78d6"
AZUL_CLARO = "#9ec5f4"
LARANJA = "#eb6834"
CINZA = "#c9c7c0"
TEXTO_2 = "#52514e"
GRADE = "#ebe9e4"
# Fundo das células sem dado no mapa de calor.
LACUNA = "#e4e2dc"
RAMPA_AZUL = [
    [0.0, "#e8f1fc"], [0.15, "#cde2fb"], [0.3, "#9ec5f4"], [0.45, "#6da7ec"],
    [0.6, "#3987e5"], [0.75, "#256abf"], [0.9, "#184f95"], [1.0, "#0d366b"],
]
FONTE = "Inter, system-ui, -apple-system, Segoe UI, sans-serif"

MARCAS_MESES = {i + 1: m.capitalize() for i, m in enumerate(MESES)}
MESES_EXTENSO = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                 "agosto", "setembro", "outubro", "novembro", "dezembro"]


# ----------------------------------------------------------------------------
# Formatação e peças de layout
# ----------------------------------------------------------------------------


def fmt(v: float | None, casas: int = 0) -> str:
    """Número no padrão brasileiro (1.234,56)."""
    if v is None:
        return "—"
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _casas(unidade: str) -> int:
    return 1 if unidade == "kg" else 0


def kpi(titulo: str, valor: str, nota: str = "") -> html.Div:
    return html.Div(
        [
            html.Div(titulo, className="kpi__titulo"),
            html.Div(valor, className="kpi__valor"),
            html.Div(nota, className="kpi__nota"),
        ],
        className="kpi",
    )


def cartao(titulo, subtitulo, *filhos, id_titulo=None, id_subtitulo=None) -> html.Section:
    kw_t = {"id": id_titulo} if id_titulo else {}
    kw_s = {"id": id_subtitulo} if id_subtitulo else {}
    return html.Section(
        [html.H3(titulo, **kw_t), html.P(subtitulo, className="subtitulo", **kw_s), *filhos],
        className="cartao",
    )


def filtro(rotulo: str, componente, largo: bool = False) -> html.Div:
    return html.Div(
        [html.Label(rotulo, className="rotulo"), componente],
        className="filtro filtro--largo" if largo else "filtro",
    )


def grafico(id_: str, altura: int) -> dcc.Loading:
    return dcc.Loading(
        dcc.Graph(id=id_, config={"displayModeBar": False, "locale": "pt-BR"},
                  style={"height": f"{altura}px"}),
        type="circle", color=AZUL,
    )


def _estilo(fig: go.Figure, altura: int, **kw) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        height=altura,
        margin=dict(l=8, r=8, t=8, b=8),
        font=dict(family=FONTE, size=12, color=TEXTO_2),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(bgcolor="white", bordercolor=GRADE, font=dict(family=FONTE, color="#0b0b0b")),
        separators=",.",
        **{"showlegend": False, **kw},
    )
    fig.update_xaxes(gridcolor=GRADE, linecolor=GRADE, zeroline=False)
    fig.update_yaxes(gridcolor=GRADE, linecolor=GRADE, zeroline=False)
    return fig


def _vazio(altura: int, msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(size=13, color=TEXTO_2),
                       xref="paper", yref="paper", x=0.5, y=0.5)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _estilo(fig, altura)


def _sequencias(valores: list[int]) -> list[tuple[int, int]]:
    """[2015, 2016, 2017, 2023] → [(2015, 2017), (2023, 2023)]."""
    seqs, ini = [], valores[0]
    for v, prox in zip(valores, [*valores[1:], None]):
        if prox != v + 1:
            seqs.append((ini, v))
            ini = prox
    return seqs


def _faixas(valores: list[int], rotulo=str) -> str:
    """[2015, 2016, 2017, 2023] → "2015–2017 e 2023"; `rotulo` escreve cada valor
    (ex.: o nome do mês)."""
    faixas = [rotulo(a) if a == b else f"{rotulo(a)}–{rotulo(b)}" for a, b in _sequencias(valores)]
    return faixas[0] if len(faixas) == 1 else ", ".join(faixas[:-1]) + " e " + faixas[-1]


TODOS_MESES = list(range(1, 13))


def _seletor(id_: str, opcoes: list[dict], valor: list[int], atalhos: list[tuple[str, str]]):
    """Botões que ligam e desligam cada valor (anos ou meses), em qualquer
    combinação, e os atalhos `(sufixo do id, rótulo)` abaixo deles."""
    return html.Div([
        dcc.Checklist(opcoes, valor, id=id_, inline=True, className="chips"),
        html.Div([html.Button(r, id=f"{id_}-{suf}", className="botao") for suf, r in atalhos],
                 className="botoes"),
    ])


def _seletor_anos(id_: str, anos: list[int], ultimo_mes: dict[int, int], padrao: int):
    # Ano incompleto (o corrente) leva asterisco.
    opcoes = [{"label": str(a) + ("" if ultimo_mes[a] == 12 else "*"), "value": a} for a in anos]
    return _seletor(id_, opcoes, [padrao],
                    [("todos", "Todos"), ("ultimo", "Último completo"), ("limpar", "Limpar")])


def _seletor_meses(id_: str):
    opcoes = [{"label": MARCAS_MESES[m], "value": m} for m in TODOS_MESES]
    return _seletor(id_, opcoes, TODOS_MESES, [("todos", "Ano todo"), ("limpar", "Limpar")])


def _registrar_seletores(app: Dash, prefixo: str, anos: list[int], ultimo_mes: dict[int, int]):
    """Atalhos dos seletores de anos e meses, e o clique num ano da série, das
    barras anuais ou do mapa de calor, que seleciona só aquele ano."""
    completo = max(a for a in anos if ultimo_mes[a] == 12)

    @app.callback(
        Output(f"{prefixo}-anos", "value"),
        Input(f"{prefixo}-anos-todos", "n_clicks"),
        Input(f"{prefixo}-anos-ultimo", "n_clicks"),
        Input(f"{prefixo}-anos-limpar", "n_clicks"),
        Input(f"{prefixo}-serie", "clickData"),
        Input(f"{prefixo}-anual", "clickData"),
        Input(f"{prefixo}-calor", "clickData"),
        prevent_initial_call=True,
    )
    def _anos(*_):
        disparo = ctx.triggered_id
        if disparo == f"{prefixo}-anos-todos":
            return anos
        if disparo == f"{prefixo}-anos-ultimo":
            return [completo]
        if disparo == f"{prefixo}-anos-limpar":
            return []
        click = ctx.triggered[0]["value"]
        if not click:
            return no_update
        ano = int(str(click["points"][0]["x"])[:4])
        return [ano] if ano in ultimo_mes else no_update

    @app.callback(
        Output(f"{prefixo}-periodo", "value"),
        Input(f"{prefixo}-periodo-todos", "n_clicks"),
        Input(f"{prefixo}-periodo-limpar", "n_clicks"),
        prevent_initial_call=True,
    )
    def _meses(*_):
        return TODOS_MESES if ctx.triggered_id == f"{prefixo}-periodo-todos" else []


def _selecao(anos_sel: list[int] | None, meses_sel: list[int] | None, ultimo_mes: dict[int, int]):
    """Anos e meses escolhidos, em ordem, e o que deriva deles: o último ano
    (`a1`), o ano de referência da variação (o anterior ao último, com um ano
    só; o primeiro, com vários), os meses do último ano que já têm dados
    (`meses_a1`, que a variação usa nos dois anos) e se não há nenhum
    (`sem_meses`). Sem nenhum ano ou mês escolhido, `None`."""
    anos_per, meses = sorted(anos_sel or []), sorted(meses_sel or [])
    if not anos_per or not meses:
        return None
    a1 = anos_per[-1]
    unico = len(anos_per) == 1
    ref = a1 - 1 if unico else anos_per[0]
    meses_a1 = [m for m in meses if m <= ultimo_mes[a1]]
    return anos_per, meses, a1, unico, ref, meses_a1 or meses, not meses_a1


def _faixas_serie(anos_per: list[int], meses: list[int], ultimo_mes: dict[int, int]):
    """Trechos da série mensal a destacar: os meses escolhidos de cada ano
    escolhido, com os meses seguidos (inclusive de dezembro para janeiro)
    num trecho só."""
    def data(i):  # índice do mês desde o ano 0 → primeiro dia do mês
        return f"{i // 12}-{i % 12 + 1:02d}-01"

    indices = [a * 12 + m - 1 for a in anos_per for m in meses if m <= ultimo_mes[a]]
    return [(data(i), data(j + 1)) for i, j in _sequencias(indices)] if indices else []


SEM_SELECAO = "Nenhum ano ou mês marcado: escolha ao menos um de cada no filtro acima."


def _caixa_avisos(avisos: list[tuple[str, str]], bloqueio: str | None = None) -> list:
    """Avisos como lista: cada um é (resumo em negrito, explicação). `bloqueio`
    (ex.: nada selecionado no filtro) substitui a lista inteira."""
    if bloqueio:
        return [html.Strong(bloqueio)]
    if not avisos:
        return []
    return [
        html.Strong("Atenção ao ler os números deste período"),
        html.Ul([html.Li([html.Strong(t), " ", txt]) for t, txt in avisos]),
    ]


def _barras_ranking(nomes, valores, hover, cores, altura) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=valores, y=nomes, orientation="h", marker=dict(color=cores, cornerradius=4),
        customdata=hover, hovertemplate="%{customdata}<extra></extra>",
    ))
    _estilo(fig, altura, bargap=0.25)
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)", ticksuffix="  ")
    return fig


def _serie_mensal(meses: list[int], valores: list[float], hover: list[str], altura: int) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=[MARCAS_MESES[m] for m in meses], y=valores, mode="lines+markers",
        line=dict(color=AZUL, width=2), marker=dict(size=8, color=AZUL, line=dict(color="white", width=2)),
        customdata=hover, hovertemplate="%{x}: %{customdata}<extra></extra>",
    ))
    _estilo(fig, altura, hovermode="x")
    fig.update_yaxes(rangemode="tozero")
    return fig


# ----------------------------------------------------------------------------
# Aba Brasil — MJ/Sinesp
# ----------------------------------------------------------------------------

# Uma UF com o ano inteiro zerado num evento que ela costuma registrar (média de
# pelo menos este tanto por ano, nos anos com registro) provavelmente não enviou
# os dados: o ano vira lacuna, não zero. Em eventos raros (roubo a instituição
# financeira, morte de agente) um ano zerado é plausível e fica como zero.
MEDIA_MIN_SEM_ENVIO = 12
TOP_CALOR_MUNICIPIOS = 20


def _anos_txt(anos: list[int]) -> str:
    return _faixas(anos)


def _meses_txt(meses: list[int]) -> str:
    """[1, 2, 3, 12] → "Jan–Mar e Dez"."""
    return _faixas(meses, MARCAS_MESES.get)


def _meses_extenso(meses: list[int]) -> str:
    return _faixas(meses, lambda m: MESES_EXTENSO[m - 1])


def _periodo_txt(anos: list[int], meses: list[int]) -> str:
    """Os anos escolhidos, só os `meses` de cada um."""
    anos_txt = _anos_txt(anos)
    if meses == TODOS_MESES:
        return anos_txt
    m = _meses_txt(meses)
    return f"{m} de {anos_txt}" if len(anos) == 1 else f"{m} de cada ano, {anos_txt}"


def _recorte_txt(meses: list[int]) -> str:
    """Meses da comparação anual: "ano inteiro" ou "só Fev e Dez"."""
    return "ano inteiro" if meses == TODOS_MESES else f"só {_meses_txt(meses)}"


def _variacao(atual: float | None, anterior: float | None) -> float | None:
    if atual is None or not anterior:
        return None
    return 100 * (atual - anterior) / anterior


def _fmt_var(v: float | None) -> str:
    return "—" if v is None else ("+" if v > 0 else "") + fmt(v, 1) + "%"


def _ufs_sem_envio(f: pl.DataFrame, ufs: pl.DataFrame, anos: list[int]) -> pl.DataFrame:
    """(`sigla_uf`, `ano`) em que a UF não tem nenhum registro do evento no ano
    inteiro, sendo um evento que ela registra com frequência nos outros anos."""
    anual = f.group_by("sigla_uf", "ano").agg(pl.col("valor").sum())
    media = anual.group_by("sigla_uf").agg(pl.col("valor").mean().alias("media"))
    return (
        ufs.select("sigla_uf")
        .join(pl.DataFrame({"ano": anos}), how="cross")
        .join(anual, on=["sigla_uf", "ano"], how="left")
        .filter(pl.col("valor").is_null())
        .join(media.filter(pl.col("media") >= MEDIA_MIN_SEM_ENVIO), on="sigla_uf")
        .select("sigla_uf", "ano")
    )


def _serie_historica(datas: list[str], valores: list, media: list, hover: list[str],
                     faixas: list[tuple[str, str]], altura: int) -> go.Figure:
    fig = go.Figure([
        go.Scatter(
            x=datas, y=valores, name="Mês", mode="lines", connectgaps=False,
            line=dict(color=AZUL_CLARO, width=1.5),
            customdata=hover, hovertemplate="%{customdata}<extra></extra>",
        ),
        go.Scatter(
            x=datas, y=media, name="Média de 12 meses", mode="lines", connectgaps=False,
            line=dict(color=AZUL, width=2.5), hoverinfo="skip",
        ),
    ])
    for x0, x1 in faixas:
        fig.add_vrect(x0=x0, x1=x1, fillcolor=AZUL, opacity=0.08, line_width=0)
    _estilo(fig, altura, hovermode="x", showlegend=True,
            legend=dict(orientation="h", x=0, y=1.08, font=dict(size=12), traceorder="normal"))
    fig.update_xaxes(dtick="M12", tickformat="%Y", hoverformat="%b/%Y")
    fig.update_yaxes(rangemode="tozero")
    return fig


def _cartao_serie(titulo: str, prefixo: str) -> html.Section:
    """Cartão da série mensal. A previsão chega depois, pelo callback de
    :func:`_registrar_previsao`, a partir da série guardada em `<prefixo>-serie-modelo`."""
    return cartao(titulo, "", grafico(f"{prefixo}-serie", 320),
                  html.Div(id=f"{prefixo}-previsao-nota", className="subtitulo"),
                  dcc.Store(id=f"{prefixo}-serie-modelo"),
                  id_subtitulo=f"{prefixo}-serie-sub")


def _registrar_previsao(app: Dash, prefixo: str) -> None:
    """Acrescenta ao gráfico `<prefixo>-serie` a previsão SARIMA dos
    :data:`HORIZONTE` meses seguintes. Fica num callback à parte porque o
    ajuste leva alguns segundos e não deve segurar o resto da página."""

    @app.callback(
        Output(f"{prefixo}-serie", "figure", allow_duplicate=True),
        Output(f"{prefixo}-previsao-nota", "children"),
        Input(f"{prefixo}-serie-modelo", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def _prever(dados):
        if not dados:
            return no_update, ""
        datas, valores = dados["datas"], dados["valores"]
        unidade, casas = dados["unidade"], dados["casas"]
        # O callback da aba pode trazer um motivo que a série sozinha não mostra.
        motivo = dados.get("motivo") or motivo_sem_previsao(valores)
        obs = dados.get("obs", "")
        if motivo:
            return no_update, html.Div([
                html.Strong(f"Sem previsão para este filtro: {motivo}."),
                f" A previsão só é feita para séries com pelo menos {MESES_MIN} meses com "
                f"dado, dado no último mês, média de {fmt(MEDIA_MIN)} ou mais por mês e no "
                f"máximo {fmt(100 * ZEROS_MAX)}% de meses zerados. Abaixo disso, o modelo "
                "passa a prever ruído.",
            ], className="aviso aviso--previsao")
        prev = prever(valores)

        a, m = int(datas[-1][:4]), int(datas[-1][5:7])
        futuras = []
        for _ in range(HORIZONTE):
            a, m = (a + 1, 1) if m == 12 else (a, m + 1)
            futuras.append(f"{a}-{m:02d}-01")
        hover = [f"<b>{MARCAS_MESES[int(d[5:7])]}/{d[:4]}</b><br>previsão: {fmt(v, casas)} "
                 f"{unidade}<br>intervalo de 95%: {fmt(lo, casas)}–{fmt(hi, casas)}"
                 for d, v, lo, hi in zip(futuras, prev.media, prev.inferior, prev.superior)]
        # A faixa e o trecho pontilhado partem do último mês observado, para a
        # previsão não ficar solta da série.
        x = [datas[-1], *futuras]
        y0 = valores[-1]
        fig = Patch()
        fig["data"].extend([
            go.Scatter(x=x, y=[y0, *prev.superior], mode="lines", line=dict(width=0),
                       hoverinfo="skip", showlegend=False),
            go.Scatter(x=x, y=[y0, *prev.inferior], mode="lines", line=dict(width=0),
                       fill="tonexty", fillcolor="rgba(235,104,52,0.15)",
                       name="Intervalo de 95%", hoverinfo="skip"),
            go.Scatter(x=x[:2], y=[y0, prev.media[0]], mode="lines",
                       line=dict(color=LARANJA, width=2, dash="dot"),
                       hoverinfo="skip", showlegend=False),
            go.Scatter(x=futuras, y=prev.media, mode="lines", name="Previsão (SARIMA)",
                       line=dict(color=LARANJA, width=2, dash="dot"),
                       customdata=hover, hovertemplate="%{customdata}<extra></extra>"),
        ])

        nota = (f"Previsão dos próximos {HORIZONTE} meses: {prev.descricao} sobre o log da "
                "contagem mensal, com as ordens escolhidas pelo AIC para este filtro; a faixa "
                "é o intervalo de 95%.")
        if prev.erro is not None:
            nota += (f" Ajustado sem os últimos {HORIZONTE} meses, o modelo errou neles em "
                     f"média {fmt(prev.erro, casas)} {unidade} por mês; repetir o mesmo mês "
                     f"do ano anterior errou {fmt(prev.erro_ingenuo, casas)}")
            nota += ("." if prev.supera_ingenuo else
                     ". Neste filtro o modelo não supera essa regra simples: leia a previsão "
                     "com cautela.")
        return fig, f"{nota} {obs}".strip()


def _layout_mj(mj: BaseMJ) -> html.Div:
    # O Dropdown não tem optgroup: a categoria entra como opção desabilitada.
    opcoes_evento = []
    for cat, eventos in mj.categorias.items():
        opcoes_evento.append({"label": html.Span(cat.upper(), className="opcao-categoria"),
                              "value": f"__{cat}", "disabled": True, "search": ""})
        for ev in eventos:
            # A unidade fica no rótulo só quando não é óbvia pelo nome (kg).
            texto = (ev + (" (kg)" if mj.unidade[ev] == "kg" else "")
                     + ("" if ev in mj.municipal else " · só por UF"))
            opcoes_evento.append({"label": texto, "value": ev,
                                  "search": " ".join([ev, cat, *mj.grupos.get(ev, [])])})
    opcoes_uf = [{"label": "Brasil (todas as UFs)", "value": BRASIL}] + [
        {"label": f"{r['nome_uf']} ({r['sigla_uf']})", "value": r["sigla_uf"]}
        for r in mj.ufs.to_dicts()
    ]
    ano_padrao = max(a for a in mj.anos if mj.ultimo_mes[a] == 12)
    primeiro, ultimo = mj.anos[0], mj.anos[-1]
    return html.Div([
        html.Div([
            filtro("Evento", dcc.Dropdown(opcoes_evento, EVENTO_PADRAO, id="mj-evento",
                                          clearable=False), largo=True),
            filtro("Localidade", dcc.Dropdown(opcoes_uf, BRASIL, id="mj-uf", clearable=False)),
            filtro("Abrangência", dcc.Checklist(id="mj-abrangencia", className="checklist")),
            filtro("Anos", _seletor_anos("mj-anos", mj.anos, mj.ultimo_mes, ano_padrao),
                   largo=True),
            filtro("Meses (valem em cada ano)", _seletor_meses("mj-periodo"), largo=True),
            filtro("Colorir e ordenar por", dcc.RadioItems(
                [{"label": f"Taxa por {fmt(BASE_TAXA_MJ)} hab.", "value": "taxa"},
                 {"label": "Total absoluto", "value": "valor"}],
                "taxa", id="mj-metrica", className="radio", inline=True)),
        ], className="filtros"),
        html.Div(id="mj-aviso", className="aviso"),
        html.Div(id="mj-kpis", className="kpis"),
        html.Div([
            cartao("", "", grafico("mj-mapa", 560), id_titulo="mj-mapa-titulo",
                   id_subtitulo="mj-mapa-sub"),
            cartao("", "", grafico("mj-ranking", 560), id_titulo="mj-ranking-titulo",
                   id_subtitulo="mj-ranking-sub"),
        ], className="grade grade--mapa"),
        _cartao_serie(f"Evolução mensal, {primeiro}–{ultimo}", "mj"),
        html.Div([
            cartao("", "", grafico("mj-anual", 320), id_titulo="mj-anual-titulo",
                   id_subtitulo="mj-anual-sub"),
            cartao("Vítimas por sexo", "", grafico("mj-sexo", 320), id_subtitulo="mj-sexo-sub"),
        ], className="grade grade--2"),
        cartao("", "", grafico("mj-calor", 640), id_titulo="mj-calor-titulo",
               id_subtitulo="mj-calor-sub"),
        cartao("Tabela", "Clique no cabeçalho para ordenar ou exporte os dados em CSV.",
               dash_table.DataTable(
                   id="mj-tabela", page_size=15, sort_action="native", filter_action="native",
                   export_format="csv", style_as_list_view=True,
                   style_header={"fontWeight": "600", "backgroundColor": "#f4f3f0"},
                   style_cell={"padding": "6px 10px", "fontSize": 13, "textAlign": "left"},
                   style_cell_conditional=[
                       {"if": {"column_type": "numeric"}, "textAlign": "right"}],
               )),
    ])


def _registrar_mj(app: Dash, mj: BaseMJ) -> None:
    geo_uf = malha_uf()
    # Municípios agrupados por UF: o mapa municipal só recebe os da UF escolhida.
    feats = malha_municipios()["features"]
    geo_mun: dict[str, dict] = {}
    for f in feats:
        cd_uf = f["properties"]["codarea"][:2]
        geo_mun.setdefault(cd_uf, {"type": "FeatureCollection", "features": []})["features"].append(f)
    uf_por_sigla = {r["sigla_uf"]: r for r in mj.ufs.to_dicts()}
    por_evento = {k[0]: df for k, df in mj.fatos.partition_by("evento", as_dict=True).items()}
    anos = mj.anos
    pop_brasil = mj.pop_uf.group_by("ano").agg(pl.col("populacao").sum())
    calendario = pl.DataFrame(
        [(a, m) for a in anos for m in range(1, mj.ultimo_mes[a] + 1)],
        schema={"ano": pl.Int64, "mes": pl.Int8}, orient="row",
    )

    @app.callback(
        Output("mj-abrangencia", "options"),
        Output("mj-abrangencia", "value"),
        Input("mj-evento", "value"),
        Input("mj-uf", "value"),
    )
    def _abrangencias(evento, uf):
        abr = sorted(por_evento[evento]["abrangencia"].unique().to_list())
        # No DF, só a Estadual: é o que a aba Distrito Federal (SSP-DF) conta.
        if uf == UF_DF and "Estadual" in abr:
            return abr, ["Estadual"]
        return abr, abr

    # Clicar numa UF do mapa nacional abre os municípios dela.
    @app.callback(
        Output("mj-uf", "value"),
        Input("mj-mapa", "clickData"),
        State("mj-uf", "value"),
        prevent_initial_call=True,
    )
    def _clique_uf(click, uf_atual):
        if not click or uf_atual != BRASIL:
            return no_update
        return click["points"][0]["customdata"][0]

    _registrar_seletores(app, "mj", anos, mj.ultimo_mes)

    @app.callback(
        Output("mj-aviso", "children"),
        Output("mj-kpis", "children"),
        Output("mj-serie", "figure"),
        Output("mj-serie-sub", "children"),
        Output("mj-serie-modelo", "data"),
        Output("mj-anual", "figure"),
        Output("mj-anual-titulo", "children"),
        Output("mj-anual-sub", "children"),
        Output("mj-sexo", "figure"),
        Output("mj-sexo-sub", "children"),
        Output("mj-mapa", "figure"),
        Output("mj-mapa-titulo", "children"),
        Output("mj-mapa-sub", "children"),
        Output("mj-ranking", "figure"),
        Output("mj-ranking-titulo", "children"),
        Output("mj-ranking-sub", "children"),
        Output("mj-calor", "figure"),
        Output("mj-calor-titulo", "children"),
        Output("mj-calor-sub", "children"),
        Output("mj-tabela", "data"),
        Output("mj-tabela", "columns"),
        Input("mj-evento", "value"),
        Input("mj-abrangencia", "value"),
        Input("mj-uf", "value"),
        Input("mj-anos", "value"),
        Input("mj-periodo", "value"),
        Input("mj-metrica", "value"),
    )
    def _atualizar(evento, abrangencias, uf, anos_sel, periodo, metrica):
        selecao = _selecao(anos_sel, periodo, mj.ultimo_mes)
        if selecao is None:
            return (_caixa_avisos([], SEM_SELECAO), *[no_update] * 20)
        # A variação compara o último ano escolhido com o anterior a ele (um ano
        # só) ou com o primeiro (vários anos). No ano corrente, os meses ainda
        # sem dados saem do período e da comparação, que usa os mesmos meses
        # (`meses_a1`) nos dois anos.
        anos_per, meses, a1, unico, ref, meses_a1, sem_meses = selecao
        unidade = mj.unidade[evento]
        casas = _casas(unidade)
        brasil = uf == BRASIL
        municipal = evento in mj.municipal and not brasil
        nome_escopo = "Brasil" if brasil else uf_por_sigla[uf]["nome_uf"]
        anos_txt = _anos_txt(anos_per)
        periodo_txt = _periodo_txt(anos_per, meses_a1 if unico else meses)
        # Os gráficos ano a ano mostram a taxa de cada ano; os do período, a média
        # anual quando há mais de um ano.
        rotulo_taxa_ano = f"{unidade} por {fmt(BASE_TAXA_MJ)} hab."
        rotulo_taxa = rotulo_taxa_ano + ("" if unico else " ao ano")
        rotulo_metrica = rotulo_taxa if metrica == "taxa" else unidade
        rotulo_metrica_ano = rotulo_taxa_ano if metrica == "taxa" else unidade

        # Todos os anos e meses do evento; `escopo` é o recorte da localidade.
        f = por_evento[evento].filter(pl.col("abrangencia").is_in(abrangencias or []))
        escopo = f if brasil else f.filter(pl.col("sigla_uf") == uf)
        sem_envio = _ufs_sem_envio(f, mj.ufs, anos)
        anos_lacuna = set() if brasil else set(sem_envio.filter(pl.col("sigla_uf") == uf)["ano"])
        pop_escopo = (pop_brasil if brasil
                      else mj.pop_uf.filter(pl.col("sigla_uf") == uf).drop("sigla_uf"))
        pop_ano = dict(pop_escopo.rows())

        def no_periodo(df, anos_, meses_=meses):
            return df.filter(pl.col("ano").is_in(anos_) & pl.col("mes").is_in(meses_))

        # Anos sem envio ficam fora do total e da população da taxa: a taxa de
        # vários anos é a média anual dos anos com dado.
        anos_validos = [a for a in anos_per if a not in anos_lacuna]
        sel = no_periodo(escopo, anos_validos)
        total = sel["valor"].sum()
        pop_validos = sum(pop_ano[a] for a in anos_validos)
        taxa = total * BASE_TAXA_MJ / pop_validos if pop_validos else None
        total_alvo = no_periodo(escopo, [a1], meses_a1)["valor"].sum()
        tem_ref = ref in mj.ultimo_mes and ref not in anos_lacuna
        total_ref = no_periodo(escopo, [ref], meses_a1)["valor"].sum() if tem_ref else None
        lacuna = not anos_validos or (unico and sem_meses)

        # --- avisos ----------------------------------------------------------
        # Cada aviso é (resumo em negrito, explicação). Saem como lista.
        avisos: list[tuple[str, str]] = []
        mes_fim_txt = MESES_EXTENSO[mj.ultimo_mes[a1] - 1]
        if sem_meses:
            avisos.append((
                f"{a1} só tem dados até {mes_fim_txt}.",
                f"Escolha algum mês até {mes_fim_txt} para ver esse ano.",
            ))
        elif meses_a1 != meses:
            meses_cmp = _meses_extenso(meses_a1)
            avisos.append((
                f"{a1} ainda está em andamento: há dados só até {mes_fim_txt}.",
                f"Por isso, a variação compara {meses_cmp} de {ref} com {meses_cmp} "
                f"de {a1}." + ("" if unico else f" No total do período, {a1} entra com "
                                              "menos meses que os outros anos."),
            ))
        if brasil:
            no_periodo_se = sem_envio.filter(pl.col("ano").is_in(anos_per))
            fora_periodo = sem_envio.filter(~pl.col("ano").is_in(anos_per))
            if no_periodo_se.height:
                todas_ufs = set(mj.ufs["sigla_uf"])

                def ufs_do_ano(ufs):
                    # Quando falta mais da metade, é mais fácil ler quem enviou.
                    if len(ufs) > len(todas_ufs) / 2:
                        return (f"{len(ufs)} das {len(todas_ufs)} UFs (só "
                                f"{', '.join(sorted(todas_ufs - set(ufs)))} enviaram)")
                    return ", ".join(sorted(ufs))

                lista = "; ".join(
                    f"{a}: {ufs_do_ano(ufs)}"
                    for a, ufs in no_periodo_se.group_by("ano").agg(pl.col("sigla_uf"))
                    .sort("ano").rows())
                avisos.append((
                    "Algumas UFs não enviaram dados em certos anos:",
                    f"{lista}. As que faltam não têm nenhum registro de “{evento}” no ano, embora "
                    "registrem o evento nos outros anos — o mais provável é falta de envio "
                    "ao Sinesp. O total do Brasil fica menor nesses anos, e parte da variação "
                    "pode vir daí, não do crime.",
                ))
            elif fora_periodo.height:
                anos_f = sorted(set(fora_periodo["ano"]))
                avisos.append((
                    "Na série histórica, faltam UFs em alguns anos:",
                    f"{_faixas(anos_f)}. Nesses anos alguma UF não enviou dados de “{evento}” "
                    f"ao Sinesp, então o Brasil aparece menor no gráfico. {anos_txt} não é "
                    "afetado.",
                ))
        elif anos_lacuna:
            no_per = sorted(anos_lacuna & set(anos_per))
            avisos.append((
                f"{nome_escopo} não enviou dados em {_faixas(sorted(anos_lacuna))}.",
                f"A UF não tem nenhum registro de “{evento}” nesses anos, embora registre "
                "o evento nos outros — o mais provável é falta de envio ao Sinesp. Nos "
                "gráficos, esses anos ficam sem valor (não como zero)"
                + (", e no período escolhido ficam fora do total e da taxa."
                   if no_per and not unico else "."),
            ))
        if evento in mj.grupos:
            membros = mj.grupos[evento]
            falhas = []
            for m in membros:
                fm = por_evento[m].filter(pl.col("abrangencia").is_in(abrangencias or []))
                # UF-ano sem nenhum membro já entra como lacuna do grupo inteiro.
                se = (_ufs_sem_envio(fm, mj.ufs, anos)
                      .filter(pl.col("ano").is_in(anos_per))
                      .join(sem_envio, on=["sigla_uf", "ano"], how="anti"))
                ufs_m = sorted(set(se["sigla_uf"])) if brasil else (
                    [uf] if uf in se["sigla_uf"] else [])
                if ufs_m:
                    falhas.append(f"{m} ({', '.join(ufs_m)})" if brasil else m)
            soma = f"{', '.join(membros[:-1])} e {membros[-1]}"
            if falhas:
                # Um membro sem envio não zera o grupo: só o deixa menor, sem sinal.
                avisos.append((
                    "Parte do grupo está faltando:",
                    f"“{evento}” soma {soma}, mas em {anos_txt} não há registro de "
                    f"{'; '.join(falhas)}. O total do grupo fica menor que o real"
                    + (" nessas UFs." if brasil else "."),
                ))
            else:
                avisos.append((f"“{evento}” é um grupo:", f"soma {soma}."))
        if not brasil and evento not in mj.municipal:
            avisos.append((
                "Este evento não vem por município.",
                f"O BancoVDE só traz “{evento}” por UF, então o mapa mostra o Brasil "
                "inteiro e destaca a UF escolhida.",
            ))
        if municipal:
            sem_mun = sel.filter(pl.col("codigo_municipio_dv").is_null())["valor"].sum()
            if sem_mun:
                avisos.append((
                    f"{fmt(sem_mun, casas)} {unidade} sem município informado.",
                    "Entram nos totais de "
                    f"{nome_escopo}, mas não aparecem no mapa nem no ranking de municípios.",
                ))
        if unidade == "kg":
            avisos.append((
                "Apreensões de droga são medidas em kg.",
                f"A “taxa” aqui é kg apreendidos por {fmt(BASE_TAXA_MJ)} habitantes.",
            ))
        if uf == UF_DF and evento in DIFERENCA_DF_MJ:
            avisos.append(("Diferente da aba Distrito Federal.", DIFERENCA_DF_MJ[evento]))
        aviso = _caixa_avisos(avisos, None if abrangencias else
                              "Nenhuma abrangência marcada: escolha ao menos uma no filtro acima.")

        # --- por local (UF ou município) no período ---------------------------
        def por_local_no(a, df):
            if municipal:
                base = (
                    mj.municipios.filter(pl.col("sigla_uf") == uf)
                    .join(mj.pop_municipio.filter(pl.col("ano") == a).drop("ano"),
                          on="codigo_municipio_dv", how="left")
                    .join(df.filter(pl.col("codigo_municipio_dv").is_not_null())
                          .group_by("codigo_municipio_dv").agg(pl.col("valor").sum()),
                          on="codigo_municipio_dv", how="left")
                    .rename({"codigo_municipio_dv": "codigo", "nome_municipio": "local"})
                )
            else:
                base = (
                    mj.ufs.join(mj.pop_uf.filter(pl.col("ano") == a).drop("ano"), on="sigla_uf")
                    .join(df.group_by("sigla_uf").agg(pl.col("valor").sum()),
                          on="sigla_uf", how="left")
                    .rename({"cd_uf": "codigo", "nome_uf": "local"})
                )
            return base.with_columns(pl.col("valor").fill_null(0)).with_columns(
                (pl.col("valor") * BASE_TAXA_MJ / pl.col("populacao")).alias("taxa"))

        fonte_local = escopo if municipal else f

        def por_local_em(anos_, meses_=meses):
            """Soma dos anos `anos_` por local. O ano em que a UF não enviou fica
            de fora do valor e da população; sem nenhum ano válido, fica nulo."""
            if not anos_:
                return None
            anual = pl.concat([
                por_local_no(a, no_periodo(fonte_local, [a], meses_))
                .with_columns(pl.lit(a).alias("ano")) for a in anos_
            ]).join(sem_envio.with_columns(pl.lit(True).alias("lac")),
                    on=["sigla_uf", "ano"], how="left")
            valido = pl.col("lac").is_null()
            return (
                anual.sort("ano").group_by("codigo", maintain_order=True)
                .agg(
                    pl.exclude("codigo", "ano", "lac", "valor", "taxa", "populacao").first(),
                    pl.col("populacao").last(),
                    pl.col("valor").filter(valido).sum().alias("valor"),
                    pl.col("populacao").filter(valido).sum().alias("pop_validos"),
                    pl.col("lac").is_not_null().sum().alias("n_lacunas"),
                )
                .with_columns(
                    pl.when(pl.col("pop_validos") > 0).then(pl.col("valor")).alias("valor"))
                .with_columns(
                    (pl.col("valor") * BASE_TAXA_MJ / pl.col("pop_validos")).alias("taxa"))
            )

        por_local = por_local_em(anos_per)
        comparar = {"anterior": [ref] if ref in mj.ultimo_mes else []}
        if not unico:
            comparar["atual"] = [a1]
        for nome, anos_c in comparar.items():
            outro = por_local_em(anos_c, meses_a1)
            por_local = (por_local.join(outro.select("codigo", pl.col("valor").alias(nome)),
                                        on="codigo", how="left")
                         if outro is not None else por_local.with_columns(pl.lit(None, pl.Float64).alias(nome)))
        por_local = por_local.with_columns(
            pl.when(pl.col("anterior") > 0)
            .then(100 * (pl.col("valor" if unico else "atual") - pl.col("anterior"))
                  / pl.col("anterior"))
            .alias("variacao")
        )

        # --- KPIs ------------------------------------------------------------
        nivel = "município" if municipal else "UF"
        ranking_base = por_local
        if municipal and metrica == "taxa":
            grandes = por_local.filter(pl.col("populacao") >= POP_MIN_RANKING)
            ranking_base = grandes if grandes.height >= 5 else por_local
        lider = (ranking_base.sort(metrica, descending=True, nulls_last=True).row(0, named=True)
                 if total else None)
        fonte_pop = mj.fonte_populacao[a1]
        alvo_lacuna = a1 in anos_lacuna or sem_meses
        if unico:
            nota_var = (f"{fmt(total_ref, casas)} {unidade} em {_periodo_txt([ref], meses_a1)}"
                        if total_ref is not None else "Sem ano anterior comparável")
        else:
            nota_var = (f"{_periodo_txt([ref], meses_a1)}: {fmt(total_ref, casas)} → "
                        f"{_periodo_txt([a1], meses_a1)}: {fmt(total_alvo, casas)}"
                        if total_ref is not None and not alvo_lacuna
                        else f"Sem dado comparável em {ref} ou {a1}")
        kpis = [
            kpi(f"Total de {unidade}", "—" if lacuna else fmt(total, casas),
                f"{nome_escopo} · {periodo_txt}"),
            kpi(f"Taxa por {fmt(BASE_TAXA_MJ)} hab." + ("" if unico else " (média anual)"),
                "—" if lacuna else fmt(taxa, 2),
                f"População {a1} ({fonte_pop}): {fmt(pop_ano[a1])}"),
            kpi(f"Variação sobre {ref}" if unico else f"Variação {ref} → {a1}",
                _fmt_var(None if alvo_lacuna else _variacao(total_alvo, total_ref)),
                nota_var),
            kpi(f"Maior {'taxa' if metrica == 'taxa' else 'total'} por {nivel}",
                lider["local"] if lider else "—",
                (fmt(lider["taxa"], 2) + f" {rotulo_taxa}" if metrica == "taxa"
                 else fmt(lider["valor"], casas) + f" {unidade}") if lider else ""),
        ]
        if not brasil:
            # Mesmos anos nos dois lados: sem os anos em que a UF não enviou.
            total_br = no_periodo(f, anos_validos)["valor"].sum()
            pop_br = dict(pop_brasil.rows())[a1]
            kpis.append(kpi("Participação no Brasil",
                            fmt(100 * total / total_br, 1) + "%" if total_br and not lacuna else "—",
                            f"{fmt(100 * pop_ano[a1] / pop_br, 1)}% da população em {a1}"))
        else:
            kpis.append(kpi("UFs com registro", str(por_local.filter(pl.col("valor") > 0).height),
                            "de 27"))
        if unidade == "vítimas":
            fem, masc, ni = (sel[c].sum() for c in ("feminino", "masculino", "nao_informado"))
            soma = fem + masc + ni
            kpis.append(kpi("Vítimas do sexo feminino",
                            fmt(100 * fem / soma, 1) + "%" if soma else "—",
                            f"{fmt(fem)} de {fmt(soma)}"))

        # --- série mensal (todos os anos) ------------------------------------
        mensal = (
            calendario.join(escopo.group_by("ano", "mes").agg(pl.col("valor").sum()),
                            on=["ano", "mes"], how="left")
            .with_columns(
                pl.when(pl.col("ano").is_in(list(anos_lacuna))).then(None)
                .otherwise(pl.col("valor").fill_null(0)).alias("valor"))
            .sort("ano", "mes")
            .with_columns(pl.col("valor").rolling_mean(12).alias("media"))
        )
        datas = [f"{a}-{m:02d}-01" for a, m in mensal.select("ano", "mes").rows()]
        hover_m = [
            f"<b>{MARCAS_MESES[m]}/{a}</b><br>"
            + ("sem registro (UF não enviou?)" if v is None else f"{fmt(v, casas)} {unidade}")
            + ("" if md is None else f"<br>média de 12 meses: {fmt(md, casas)}")
            for a, m, v, md in mensal.select("ano", "mes", "valor", "media").rows()
        ]
        serie = _serie_historica(datas, mensal["valor"].to_list(), mensal["media"].to_list(),
                                 hover_m, _faixas_serie(anos_per, meses, mj.ultimo_mes), 320)
        serie_sub = (f"{evento} em {nome_escopo}: {unidade} por mês e média dos 12 meses "
                     f"anteriores. A faixa marca {anos_txt}; clique num mês para ver só aquele "
                     "ano.")
        # No MJ, mês sem registro é zero de fato; os anos em que a UF não
        # enviou dados já vêm como faltantes.
        serie_modelo = {"datas": datas, "valores": mensal["valor"].to_list(),
                        "unidade": unidade, "casas": casas}

        # --- comparação anual (mesmos meses em todos os anos) ----------------
        anual = (
            pl.DataFrame({"ano": anos})
            .join(escopo.filter(pl.col("mes").is_in(meses))
                  .group_by("ano").agg(pl.col("valor").sum()), on="ano", how="left")
            .join(pop_escopo, on="ano", how="left")
            .with_columns(
                pl.when(pl.col("ano").is_in(list(anos_lacuna))).then(None)
                .otherwise(pl.col("valor").fill_null(0)).alias("valor"))
            .with_columns((pl.col("valor") * BASE_TAXA_MJ / pl.col("populacao")).alias("taxa"))
            .sort("ano")
        )
        parcial = {a for a in anos if mj.ultimo_mes[a] < meses[-1]}
        n_sem_envio = dict(sem_envio.group_by("ano").len().rows()) if brasil else {}
        linhas = anual.to_dicts()
        hover_a = []
        for i, r in enumerate(linhas):
            if r["valor"] is None:
                hover_a.append(f"<b>{r['ano']}</b><br>sem registro (UF não enviou?)")
                continue
            ant_r = linhas[i - 1] if i else None
            comparavel = (ant_r and ant_r["valor"] is not None
                          and r["ano"] not in parcial and ant_r["ano"] not in parcial)
            txt = (f"<b>{r['ano']}</b>"
                   + (f" (até {MARCAS_MESES[mj.ultimo_mes[r['ano']]]})" if r["ano"] in parcial else "")
                   + f"<br>{fmt(r['valor'], casas)} {unidade}<br>{fmt(r['taxa'], 2)} {rotulo_taxa_ano}")
            if comparavel:
                txt += f"<br>{_fmt_var(_variacao(r[metrica], ant_r[metrica]))} sobre {ant_r['ano']}"
            if n_sem_envio.get(r["ano"]):
                txt += f"<br>{n_sem_envio[r['ano']]} UF(s) sem registro no ano"
            hover_a.append(txt)
        anual_fig = go.Figure(go.Bar(
            x=[str(a) for a in anual["ano"]], y=anual[metrica].to_list(),
            marker=dict(
                color=[AZUL if a in anos_per else AZUL_CLARO for a in anual["ano"]],
                pattern=dict(shape=["/" if a in parcial else "" for a in anual["ano"]],
                             fgcolor="white", solidity=0.3),
                cornerradius=4,
            ),
            customdata=hover_a, hovertemplate="%{customdata}<extra></extra>",
        ))
        _estilo(anual_fig, 320, bargap=0.3)
        anual_fig.update_xaxes(type="category")
        anual_titulo = "Comparação anual"
        meses_txt = _recorte_txt(meses)
        anual_sub = (f"{rotulo_metrica_ano.capitalize()} em {nome_escopo} ({meses_txt}, em todos "
                     f"os anos). Em destaque, {anos_txt}"
                     + ("; hachurado, ano ainda incompleto" if parcial else "") + ".")
        if metrica == "taxa":
            anual_sub += " A população de cada ano é a estimada pelo IBGE."

        # --- sexo ------------------------------------------------------------
        if unidade == "vítimas" and total:
            vals = [sel[c].sum() for c in ("feminino", "masculino", "nao_informado")]
            soma = sum(vals) or 1
            sexo = _barras_ranking(
                ["Feminino", "Masculino", "Não informado"], vals,
                [f"{fmt(v)} vítimas ({fmt(100 * v / soma, 1)}%)" for v in vals], AZUL, 320)
            sexo_sub = f"Distribuição das vítimas em {nome_escopo}, {periodo_txt}."
        else:
            sexo = _vazio(320, "Este evento não tem recorte por sexo")
            sexo_sub = "Disponível só para eventos contados em vítimas."

        # --- mapa ------------------------------------------------------------
        var_txt = f"sobre {ref}" if unico else f"de {ref} para {a1}"

        def hover_local(r):
            if r["valor"] is None:
                return f"<b>{r['local']}</b><br>sem registro em {anos_txt} (UF não enviou?)"
            txt = (f"<b>{r['local']}</b><br>{fmt(r['valor'], casas)} {unidade} em {anos_txt}"
                   f"<br>{fmt(r['taxa'], 2)} {rotulo_taxa}<br>População {a1}: {fmt(r['populacao'])}"
                   f"<br>{_fmt_var(r['variacao'])} {var_txt}")
            if r["n_lacunas"]:
                txt += f"<br>{r['n_lacunas']} ano(s) sem registro fora da soma"
            return txt

        hover = [hover_local(r) for r in por_local.to_dicts()]
        if municipal:
            geojson = geo_mun[uf_por_sigla[uf]["cd_uf"]]
            custom = [[None, h] for h in hover]
            linha = 0.3
        else:
            geojson = geo_uf
            custom = [[s, h] for s, h in zip(por_local["sigla_uf"], hover)]
            linha = [2.5 if s == uf else 0.6 for s in por_local["sigla_uf"]]
        mapa = go.Figure(go.Choropleth(
            geojson=geojson, featureidkey="properties.codarea",
            locations=por_local["codigo"].to_list(), z=por_local[metrica].to_list(),
            colorscale=RAMPA_AZUL, zmin=0,
            marker=dict(line=dict(color="#ffffff", width=linha)),
            customdata=custom, hovertemplate="%{customdata[1]}<extra></extra>",
            colorbar=dict(thickness=10, len=0.6, outlinewidth=0, tickfont=dict(size=11),
                          title=dict(text=rotulo_metrica, side="right", font=dict(size=11))),
        ))
        if not municipal and not brasil:
            mapa.update_traces(marker_line_color=["#0b0b0b" if s == uf else "#ffffff"
                                                  for s in por_local["sigla_uf"]])
        _estilo(mapa, 560)
        mapa.update_geos(fitbounds="locations", visible=False, projection_type="mercator",
                         bgcolor="rgba(0,0,0,0)")
        mapa_titulo = (f"Municípios de {nome_escopo}" if municipal else "Unidades da Federação")
        mapa_sub = (f"{evento} em {periodo_txt}, colorido pela "
                    + (rotulo_taxa if metrica == "taxa" else f"quantidade de {unidade}") + "."
                    + (" Clique numa UF para ver seus municípios." if brasil and
                       evento in mj.municipal else ""))

        # --- ranking ---------------------------------------------------------
        top = ranking_base.sort(metrica, descending=True, nulls_last=True).head(TOP_RANKING)
        hover_rk = [hover_local(r) for r in top.to_dicts()]
        if municipal:
            cores = AZUL
        else:
            # Com uma UF escolhida, ela se destaca e as demais ficam neutras.
            cores = [AZUL if uf in (BRASIL, s) else CINZA for s in top["sigla_uf"]]
        ranking = (_barras_ranking(top["local"].to_list(), top[metrica].to_list(), hover_rk,
                                   cores, 560)
                   if total else _vazio(560, "Sem registros no filtro"))
        ranking_titulo = f"{'Municípios' if municipal else 'UFs'} com maior " + (
            "taxa" if metrica == "taxa" else "total") + f" em {anos_txt}"
        ranking_sub = f"Os {TOP_RANKING} primeiros, em {rotulo_metrica}."
        if municipal and metrica == "taxa" and ranking_base is not por_local:
            ranking_sub += f" Só entram municípios com {fmt(POP_MIN_RANKING)}+ habitantes."

        # --- mapa de calor: local × ano --------------------------------------
        periodo_f = f.filter(pl.col("mes").is_in(meses))
        if municipal:
            # Os municípios grandes de maior valor no período.
            escolhidos = (por_local.filter(pl.col("populacao") >= POP_MIN_RANKING)
                          .sort(metrica, descending=True, nulls_last=True)
                          .head(TOP_CALOR_MUNICIPIOS))
            calor = (
                escolhidos.select("codigo", "local")
                .join(pl.DataFrame({"ano": anos}), how="cross")
                .join(mj.pop_municipio.rename({"codigo_municipio_dv": "codigo"}),
                      on=["codigo", "ano"], how="left")
                .join(periodo_f.filter(pl.col("sigla_uf") == uf)
                      .group_by(pl.col("codigo_municipio_dv").alias("codigo"), "ano")
                      .agg(pl.col("valor").sum()), on=["codigo", "ano"], how="left")
                .with_columns(pl.col("ano").is_in(list(anos_lacuna)).alias("lacuna"))
            )
            calor_titulo = f"Municípios de {nome_escopo} ano a ano"
            calor_sub = (f"Os {TOP_CALOR_MUNICIPIOS} municípios com {fmt(POP_MIN_RANKING)}+ "
                         f"habitantes de maior {('taxa' if metrica == 'taxa' else 'total')} "
                         f"em {anos_txt}")
        else:
            calor = (
                mj.ufs.select("sigla_uf", pl.col("nome_uf").alias("local"))
                .join(pl.DataFrame({"ano": anos}), how="cross")
                .join(mj.pop_uf, on=["sigla_uf", "ano"], how="left")
                .join(periodo_f.group_by("sigla_uf", "ano").agg(pl.col("valor").sum()),
                      on=["sigla_uf", "ano"], how="left")
                .join(sem_envio.with_columns(pl.lit(True).alias("lacuna")),
                      on=["sigla_uf", "ano"], how="left")
                .with_columns(pl.col("lacuna").fill_null(False))
            )
            calor_titulo = "UFs ano a ano"
            calor_sub = (f"Todas as UFs, da maior para a menor "
                         f"{('taxa' if metrica == 'taxa' else 'total')} em {anos_txt}")
        calor = calor.with_columns(
            pl.when(pl.col("lacuna")).then(None).otherwise(pl.col("valor").fill_null(0))
            .alias("valor")
        ).with_columns((pl.col("valor") * BASE_TAXA_MJ / pl.col("populacao")).alias("taxa"))
        # Mesma ordem do ranking: pelo valor do período escolhido.
        ordem = por_local.sort(pl.col(metrica).fill_null(-1), descending=True)["local"].to_list()
        grade = calor.pivot("ano", index="local", values=metrica, sort_columns=True)
        grade_txt = calor.with_columns(pl.Series("txt", [
            "sem registro (não enviou?)" if lac
            else f"{fmt(v, casas)} {unidade}<br>{fmt(t, 2)} {rotulo_taxa_ano}"
            for v, t, lac in calor.select("valor", "taxa", "lacuna").rows()
        ], dtype=pl.String)).pivot("ano", index="local", values="txt", sort_columns=True)
        grade = grade.join(pl.DataFrame({"local": ordem}).with_row_index("i"), on="local").sort("i")
        grade_txt = grade_txt.join(pl.DataFrame({"local": ordem}).with_row_index("i"),
                                   on="local").sort("i")
        cols_ano = [str(a) for a in anos]
        rotulos_x = [a + ("*" if int(a) in parcial else "") for a in cols_ano]
        n_linhas = grade.height
        altura_calor = max(260, 22 * n_linhas + 70)
        if n_linhas:
            calor_fig = go.Figure(go.Heatmap(
                z=grade.select(cols_ano).rows(), x=rotulos_x, y=grade["local"].to_list(),
                customdata=grade_txt.select(cols_ano).rows(),
                colorscale=RAMPA_AZUL, zmin=0, xgap=2, ygap=2, hoverongaps=True,
                hovertemplate="<b>%{y} · %{x}</b><br>%{customdata}<extra></extra>",
                colorbar=dict(thickness=10, len=0.6, outlinewidth=0, tickfont=dict(size=11),
                              title=dict(text=rotulo_metrica_ano, side="right",
                                         font=dict(size=11))),
            ))
            _estilo(calor_fig, altura_calor)
            # Lacunas ficam sem célula: o fundo cinza aparece no lugar.
            calor_fig.update_layout(plot_bgcolor=LACUNA)
            calor_fig.update_xaxes(type="category", side="top", gridcolor="rgba(0,0,0,0)")
            calor_fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)",
                                   ticksuffix="  ", tickfont=dict(size=11))
        else:
            calor_fig = _vazio(260, "Nenhum município com o porte mínimo")
        calor_sub += (f", em {rotulo_metrica_ano} ({meses_txt}). Cinza: ano sem nenhum registro "
                      "de um evento que a UF registra nos outros anos (provável falta de envio)."
                      + (" *Ano ainda incompleto." if parcial else "")
                      + " Clique numa célula para ver só aquele ano.")

        # --- tabela ----------------------------------------------------------
        tabela = por_local.sort(metrica, descending=True, nulls_last=True).select(
            pl.col("local"),
            *([] if municipal else [pl.col("sigla_uf"), pl.col("nome_regiao")]),
            pl.col("populacao"),
            pl.col("valor").round(casas),
            pl.col("taxa").round(2),
            pl.col("anterior").round(casas),
            *([] if unico else [pl.col("atual").round(casas)]),
            pl.col("variacao").round(1),
            *([] if unico else [pl.col("n_lacunas")]),
        )
        colunas = [{"name": "Município" if municipal else "UF", "id": "local"}]
        if not municipal:
            colunas += [{"name": "Sigla", "id": "sigla_uf"}, {"name": "Região", "id": "nome_regiao"}]
        num = dict(type="numeric", format={"locale": {"group": ".", "decimal": ","},
                                            "specifier": ",.0f"})
        fmt_valor = {**num["format"], "specifier": f",.{casas}f"}
        colunas += [
            {"name": f"População {a1}", "id": "populacao", **num},
            {"name": f"{unidade.capitalize()} {anos_txt}", "id": "valor", **num,
             "format": fmt_valor},
            {"name": f"Taxa /{fmt(BASE_TAXA_MJ)} hab." + ("" if unico else " ao ano"),
             "id": "taxa", **num, "format": {**num["format"], "specifier": ",.2f"}},
            {"name": f"{unidade.capitalize()} {ref}", "id": "anterior", **num,
             "format": fmt_valor},
        ]
        if not unico:
            colunas.append({"name": f"{unidade.capitalize()} {a1}", "id": "atual", **num,
                            "format": fmt_valor})
        colunas.append({"name": "Variação (%)", "id": "variacao", **num,
                        "format": {**num["format"], "specifier": "+,.1f"}})
        if not unico:
            colunas.append({"name": "Anos sem registro", "id": "n_lacunas", **num})

        # O rótulo da taxa termina em "hab.": evita o ".." no fim da frase.
        anual_sub, mapa_sub, ranking_sub, calor_sub = (
            t.replace("..", ".") for t in (anual_sub, mapa_sub, ranking_sub, calor_sub))
        return (
            aviso, kpis, serie, serie_sub, serie_modelo,
            anual_fig, anual_titulo, anual_sub, sexo, sexo_sub,
            mapa, mapa_titulo, mapa_sub, ranking, ranking_titulo, ranking_sub,
            calor_fig, calor_titulo, calor_sub,
            tabela.to_dicts(), colunas,
        )


# ----------------------------------------------------------------------------
# Aba Distrito Federal — SSP-DF
# ----------------------------------------------------------------------------


def _rotulo_eixo(eixo: str) -> str:
    """"1. C.V.L.I. - CRIMES VIOLENTOS..." → "C.V.L.I."; "3. OUTROS CRIMES" → "Outros Crimes"."""
    nome = eixo.split(". ", 1)[-1]
    return nome.split(" - ")[0] if " - " in nome else nome.title()


def _layout_ssp(ssp: BaseSSP) -> html.Div:
    opcoes = [
        {"label": html.Span(n, title=g["eixo"]), "value": n, "search": n}
        for g in ssp.grupos for n in g["naturezas"]
    ]
    ano_padrao = max(a for a in ssp.anos if ssp.ultimo_mes[a] == 12)
    primeiro, ultimo = ssp.anos[0], ssp.anos[-1]
    return html.Div([
        html.Div([
            filtro("Natureza do crime", html.Div([
                dcc.Dropdown(opcoes, ssp.padrao, id="ssp-naturezas", multi=True,
                             placeholder="Escolha uma ou mais naturezas"),
                html.Div([
                    html.Button("Crimes (padrão)", id="ssp-btn-padrao", className="botao"),
                    *[html.Button(_rotulo_eixo(g["eixo"]), id={"tipo": "ssp-btn-eixo", "i": i},
                                  className="botao", title=g["eixo"])
                      for i, g in enumerate(ssp.grupos)],
                    html.Button("Todas", id="ssp-btn-todas", className="botao"),
                    html.Button("Limpar", id="ssp-btn-limpar", className="botao"),
                ], className="botoes"),
            ]), largo=True),
            filtro("Anos", _seletor_anos("ssp-anos", ssp.anos, ssp.ultimo_mes, ano_padrao),
                   largo=True),
            filtro("Meses (valem em cada ano)", _seletor_meses("ssp-periodo"), largo=True),
            filtro("Mapa", dcc.RadioItems(
                [{"label": "Coroplético (taxa)", "value": "coropletico"},
                 {"label": "Bolhas (total)", "value": "bolhas"}],
                "coropletico", id="ssp-tipo-mapa", className="radio", inline=True)),
        ], className="filtros"),
        html.Div(id="ssp-aviso", className="aviso"),
        html.Div(id="ssp-kpis", className="kpis"),
        html.Div([
            cartao("Mapa das Regiões Administrativas", "",
                   dcc.Loading(html.Iframe(id="ssp-mapa", className="mapa-folium"),
                               type="circle", color=AZUL), id_subtitulo="ssp-mapa-sub"),
            cartao("", "", grafico("ssp-ranking", 640), id_titulo="ssp-ranking-titulo",
                   id_subtitulo="ssp-ranking-sub"),
        ], className="grade grade--mapa"),
        _cartao_serie(f"Evolução mensal no DF, {primeiro}–{ultimo}", "ssp"),
        cartao("Comparação anual", "", grafico("ssp-anual", 320), id_subtitulo="ssp-anual-sub"),
        cartao("Regiões Administrativas ano a ano", "", grafico("ssp-calor", 820),
               id_subtitulo="ssp-calor-sub"),
        cartao("Tabela por RA", "Clique no cabeçalho para ordenar ou exporte os dados em CSV.",
               dash_table.DataTable(
                   id="ssp-tabela", page_size=33, sort_action="native", filter_action="native",
                   export_format="csv", style_as_list_view=True,
                   style_header={"fontWeight": "600", "backgroundColor": "#f4f3f0"},
                   style_cell={"padding": "6px 10px", "fontSize": 13, "textAlign": "left",
                               "whiteSpace": "normal", "maxWidth": 380},
               )),
    ])


def _nome_ra(ra: str) -> str:
    """"SÃO SEBASTIÃO" → "São Sebastião"; siglas (SIA, SCIA) ficam em caixa alta."""
    nome = ra.title()
    for minuscula in ("Do", "Da", "De", "Das", "Dos"):
        nome = nome.replace(f" {minuscula} ", f" {minuscula.lower()} ")
    return nome.replace("Scia", "SCIA").replace("Sia", "SIA")


def _nome_natureza(n: str) -> str:
    """A natureza já vem com o nome do painel (`tcc.painel.dados.NOMES_SSP`)."""
    return n


def _lista_ras(df: pl.DataFrame) -> str:
    """"Planaltina (2016–2017), Sobradinho (2015)" a partir de `ra` e `ano`."""
    return ", ".join(
        f"{_nome_ra(ra)} ({_faixas(sorted(anos))})"
        for ra, anos in df.group_by("ra").agg(pl.col("ano")).sort("ra").rows()
    )


def _registrar_ssp(app: Dash, ssp: BaseSSP) -> None:
    todas = [n for g in ssp.grupos for n in g["naturezas"]]
    fora_taxa = {n for g in ssp.grupos if g["fora_taxa"] for n in g["naturezas"]}
    ja_em_homicidio = {n for g in ssp.grupos if g["ja_em_homicidio"] for n in g["naturezas"]}
    ordem = {n: i for i, n in enumerate(todas)}
    eixo_de = {n: g["eixo"] for g in ssp.grupos for n in g["naturezas"]}
    anos = ssp.anos
    base_txt = f"por {fmt(BASE_TAXA)} hab."
    nome_ra = dict(ssp.ras.select("cd_subdist", "ra").rows())
    # RAs desmembradas (Arniqueira, Sol Nascente/Pôr do Sol) antes de existirem.
    nao_existia = {(cd, a): mae for cd, (mae, criacao) in RAS_DESMEMBRADAS.items()
                   for a in anos if a < criacao}
    ausentes_set = set(ssp.ausentes.select("cd_subdist", "ano").rows())
    calendario = pl.DataFrame(
        [(a, m) for a in anos for m in range(1, ssp.ultimo_mes[a] + 1)],
        schema={"ano": pl.Int64, "mes": pl.Int8}, orient="row",
    )
    # RAs fora da soma do DF em cada mês: sem balanço no ano ou sem o mês detalhado.
    faltam_mes = {
        (a, m): n for a, m, n in
        pl.concat([
            ssp.ausentes.join(calendario, on="ano").select("cd_subdist", "ano", "mes"),
            ssp.mensal.filter(pl.col("valor").is_null()).select("cd_subdist", "ano", "mes"),
        ]).unique().group_by("ano", "mes").len().rows()
    }
    # Meses (ano, mês) com registro de cada natureza.
    meses_natureza = {
        n: set(df.select("ano", "mes").rows())
        for (n,), df in ssp.mensal.partition_by("natureza", as_dict=True).items()
    }
    meses_cal = calendario.rows()

    def _inicio(naturezas: list[str]) -> tuple[int, int] | None:
        """Primeiro (ano, mês) em que alguma das naturezas tem registro. Antes
        dele, nenhuma existia: é falta de dado, não zero (FEMINICÍDIO por RA só
        existe a partir de 2025)."""
        primeiros = [min(meses_natureza[n]) for n in naturezas if meses_natureza.get(n)]
        return min(primeiros) if primeiros else None

    def _anos_sem(naturezas: list[str]) -> set[int]:
        """Anos em que nenhuma das naturezas tem registro: ficam sem dado."""
        return {a for a in anos
                if not any(a in ssp.anos_natureza.get(n, set()) for n in naturezas)}

    def _serie_para_modelo(naturezas: list[str]) -> tuple[list[int | None], list[str]]:
        """Série mensal do DF para o modelo e as naturezas deixadas de fora.

        A série tem `None` onde a soma não é comparável: meses em que falta
        alguma RA e meses, depois do primeiro registro, em que uma natureza
        escolhida não aparece (o gráfico mostra esses meses como zero, mas são
        lacunas do balanço, como TRÁFICO DE DROGAS em 2019). Antes do primeiro
        registro, a natureza ainda não existia e conta como zero, a menos que
        nenhuma das escolhidas existisse: aí o mês é `None`. Naturezas sem
        registro no último mês (FURTO A TRANSEUNTE não sai nos balanços de 2026)
        ficam fora da soma: senão o fim da série seria todo faltante."""
        fora = [n for n in naturezas if meses_cal[-1] not in meses_natureza.get(n, set())]
        naturezas = [n for n in naturezas if n not in fora]
        soma = {
            (a, m): v for a, m, v in
            ssp.mensal.filter(pl.col("natureza").is_in(naturezas))
            .group_by("ano", "mes").agg(pl.col("valor").sum()).rows()
        }
        lacunas = set()
        for n in naturezas:
            tem = meses_natureza.get(n, set())
            if tem:
                inicio = min(tem)
                lacunas |= {am for am in meses_cal if am >= inicio and am not in tem}
        inicio = _inicio(naturezas)
        valores = [None if faltam_mes.get(am) or am in lacunas or inicio is None or am < inicio
                   else int(soma.get(am) or 0)
                   for am in meses_cal]
        return valores, fora

    @app.callback(
        Output("ssp-naturezas", "value"),
        Input("ssp-btn-padrao", "n_clicks"),
        Input("ssp-btn-todas", "n_clicks"),
        Input("ssp-btn-limpar", "n_clicks"),
        Input({"tipo": "ssp-btn-eixo", "i": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _atalhos(*_):
        disparo = ctx.triggered_id
        if disparo == "ssp-btn-padrao":
            return ssp.padrao
        if disparo == "ssp-btn-todas":
            return todas
        if disparo == "ssp-btn-limpar":
            return []
        return ssp.grupos[disparo["i"]]["naturezas"]

    _registrar_seletores(app, "ssp", anos, ssp.ultimo_mes)

    def _por_ra(ra_ano: pl.DataFrame, anos_: list[int]) -> pl.DataFrame:
        """Soma dos anos `anos_` por RA, só os anos com dado; a taxa é a média
        anual (soma das ocorrências ÷ soma das populações)."""
        v = ra_ano.filter(pl.col("ano").is_in(anos_) & pl.col("total").is_not_null())
        return (
            ssp.ras.join(
                v.group_by("cd_subdist").agg(
                    pl.col("total").sum(), pl.col("populacao").sum().alias("pop_validos")),
                on="cd_subdist", how="left")
            .with_columns((pl.col("total") * BASE_TAXA / pl.col("pop_validos")).alias("taxa"))
        )

    @app.callback(
        Output("ssp-mapa", "srcDoc"),
        Output("ssp-mapa-sub", "children"),
        Input("ssp-naturezas", "value"),
        Input("ssp-anos", "value"),
        Input("ssp-periodo", "value"),
        Input("ssp-tipo-mapa", "value"),
    )
    def _mapa(naturezas, anos_sel, periodo, tipo):
        naturezas = sorted(naturezas or [], key=ordem.get)
        selecao = _selecao(anos_sel, periodo, ssp.ultimo_mes)
        if selecao is None:
            return no_update, SEM_SELECAO
        anos_per, meses = selecao[:2]
        # RAs/anos que entram: com balanço e com os meses do período.
        validos = (ssp_por_ra_ano(ssp, [], meses)
                   .filter(pl.col("ano").is_in(anos_per) & pl.col("total").is_not_null()
                           & ~pl.col("ano").is_in(list(_anos_sem(naturezas)))))
        if meses == TODOS_MESES:
            fatos = ssp.anual
        else:
            fatos = (ssp.mensal.filter(pl.col("mes").is_in(meses))
                     .group_by("cd_subdist", "ano", "natureza")
                     .agg(pl.col("valor").sum().alias("total")))
        por_nat = (
            fatos.join(validos.select("cd_subdist", "ano"), on=["cd_subdist", "ano"])
            .group_by("cd_subdist", "natureza").agg(pl.col("total").sum())
        )
        # Denominador da taxa: a soma das populações dos anos com dado, para a
        # taxa sair como média anual; o tooltip mostra a do último ano.
        pop = validos.group_by("cd_subdist").agg(
            pl.col("populacao").sum(),
            pl.col("populacao").sort_by("ano").last().alias("pop_ultimo"),
        )
        base = (por_nat.join(pop, on="cd_subdist")
                .join(ssp.ras.select("cd_subdist", "ra", "n_delegacias"), on="cd_subdist")
                .with_columns(pl.col("natureza").replace_strict(eixo_de).alias("eixo")))
        dados = dataclasses.replace(
            preparar_dados(base), selecao=naturezas,
            populacao_exibida=dict(pop.select("cd_subdist", "pop_ultimo").rows()),
        )
        if tipo == "bolhas":
            mapa = mapa_bolhas(ssp.gdf_ra, ssp.gdf_ra, ssp.delegacias, dados, ssp.centroides,
                               painel_aberto=False)
        else:
            mapa = mapa_coropletico(ssp.gdf_ra, ssp.gdf_ra, ssp.delegacias, dados,
                                    painel_aberto=False)
        sub = (f"Ocorrências das naturezas selecionadas em {_periodo_txt(anos_per, meses)}"
               + ("" if len(anos_per) == 1 else ", somadas no período; a cor é a taxa média anual")
               + ". Os marcadores azuis são as delegacias (localização atual). Em cinza, RA "
               "sem dado no período.")
        return mapa.get_root().render(), sub

    @app.callback(
        Output("ssp-aviso", "children"),
        Output("ssp-kpis", "children"),
        Output("ssp-ranking", "figure"),
        Output("ssp-ranking-titulo", "children"),
        Output("ssp-ranking-sub", "children"),
        Output("ssp-serie", "figure"),
        Output("ssp-serie-sub", "children"),
        Output("ssp-serie-modelo", "data"),
        Output("ssp-anual", "figure"),
        Output("ssp-anual-sub", "children"),
        Output("ssp-calor", "figure"),
        Output("ssp-calor-sub", "children"),
        Output("ssp-tabela", "data"),
        Output("ssp-tabela", "columns"),
        Input("ssp-naturezas", "value"),
        Input("ssp-anos", "value"),
        Input("ssp-periodo", "value"),
    )
    def _atualizar(naturezas, anos_sel, periodo):
        naturezas = naturezas or []
        selecao = _selecao(anos_sel, periodo, ssp.ultimo_mes)
        if selecao is None:
            return (_caixa_avisos([], SEM_SELECAO), *[no_update] * 13)
        anos_per, meses, a1, unico, ref, meses_a1, sem_meses = selecao
        # A variação compara o último ano com o anterior a ele (um ano só) ou com
        # o primeiro (vários anos), nos mesmos meses.
        anos_txt = _anos_txt(anos_per)
        periodo_txt = _periodo_txt(anos_per, meses_a1 if unico else meses)
        rotulo_taxa = base_txt + ("" if unico else " ao ano")

        # Anos em que nenhuma das naturezas escolhidas existia: sem dado, não zero.
        anos_sem = _anos_sem(naturezas)
        ra_ano = ssp_por_ra_ano(ssp, naturezas, meses)
        por_ra = _por_ra(ra_ano, [a for a in anos_per if a not in anos_sem])
        total = por_ra["total"].sum()
        pop_validos = por_ra["pop_validos"].sum()
        taxa = total * BASE_TAXA / pop_validos if pop_validos else None
        pop_a1 = ssp.pop.filter(pl.col("ano") == a1)["populacao"].sum()
        lacuna = (not naturezas or (unico and sem_meses) or not pop_validos
                  or all(a in anos_sem for a in anos_per))

        # Comparação só nas RAs com dado nos dois anos: em 2015–2017 e 2023 faltam
        # RAs, e somar todas faria a variação vir da cobertura, não do crime.
        cmp = (ssp_por_ra_ano(ssp, naturezas, meses_a1)
               .filter(pl.col("ano").is_in([ref, a1]) & pl.col("total").is_not_null()))
        por_ra_cmp = (
            cmp.filter(pl.col("ano") == ref).select("cd_subdist", pl.col("total").alias("anterior"))
            .join(cmp.filter(pl.col("ano") == a1).select("cd_subdist", pl.col("total").alias("atual")),
                  on="cd_subdist")
        )
        tem_ref = (bool(naturezas) and ref in ssp.ultimo_mes and por_ra_cmp.height > 0
                   and not sem_meses and ref not in anos_sem and a1 not in anos_sem)
        if not tem_ref:
            por_ra_cmp = por_ra_cmp.clear()
        total_ref = por_ra_cmp["anterior"].sum() if tem_ref else None
        total_alvo = por_ra_cmp["atual"].sum() if tem_ref else None

        # --- avisos ----------------------------------------------------------
        # Cada aviso é (título em negrito, explicação). Saem como lista.
        avisos: list[tuple[str, str]] = []
        extras = [n for n in naturezas if n in fora_taxa]
        if extras:
            avisos.append((
                "A seleção inclui produtividade policial",
                f"({', '.join(_nome_natureza(n) for n in extras)}). Esses registros medem a "
                "atuação da polícia, não crimes sofridos pela população; com eles, a taxa "
                "deixa de medir só vitimização.",
            ))
        contidas = [n for n in naturezas if n in ja_em_homicidio]
        if contidas and HOMICIDIO_SSP in naturezas:
            avisos.append((
                "Feminicídio contado duas vezes.",
                "O homicídio doloso dos balanços das RAs já inclui os feminicídios. Para somar "
                "os crimes letais, deixe só o homicídio; para ver os feminicídios, só o "
                "feminicídio.",
            ))
        estupro, vulneravel = NOMES_SSP["ESTUPRO"], NOMES_SSP["ESTUPRO DE VULNERÁVEL"]
        separacao = min(ssp.anos_natureza.get(vulneravel, {anos[-1] + 1}))
        if estupro in naturezas and anos_per[0] < separacao:
            avisos.append((
                f"Até {separacao - 1}, o estupro das RAs inclui o estupro de vulnerável.",
                f"Os balanços das RAs só separam os dois a partir de {separacao}. "
                + ("Com os dois marcados, a soma é comparável em todos os anos."
                   if vulneravel in naturezas else
                   "Para comparar anos antes e depois disso, marque também o estupro de "
                   f"vulnerável: sem ele, a queda de {separacao - 1} para {separacao} vem da "
                   "separação, não do crime."),
            ))
        no_mj = [n for n in (HOMICIDIO_SSP, NOMES_SSP["TENTATIVA DE HOMICÍDIO"]) if n in naturezas]
        if no_mj:
            avisos.append((
                "Diferente da aba Brasil (MJ/Sinesp).",
                f"{' e '.join(no_mj)}: aqui são ocorrências, com o feminicídio. No MJ, o mesmo "
                "evento conta vítimas e deixa o feminicídio de fora, então lá o número do DF é "
                "menor.",
            ))
        if contidas:
            anos_fem = sorted(set().union(*(ssp.anos_natureza[n] for n in contidas)))
            avisos.append((
                f"O feminicídio por RA vem dos estudos da CTMHF/SSP-DF ({_faixas(anos_fem)}),",
                "não dos balanços: são os casos que o sistema de justiça confirma como "
                "feminicídio consumado. O mês de alguns casos foi reconstruído pela diferença "
                "entre estudos mensais e pode estar trocado entre meses próximos; os totais por "
                "RA e ano são os do estudo. A soma do DF pode diferir da aba Brasil (MJ) em "
                "até 3 casos por ano.",
            ))
        mes_fim_txt = MESES_EXTENSO[ssp.ultimo_mes[a1] - 1]
        if sem_meses:
            avisos.append((
                f"{a1} só tem dados até {mes_fim_txt}.",
                f"Escolha algum mês até {mes_fim_txt} para ver esse ano.",
            ))
        elif meses_a1 != meses:
            meses_cmp = _meses_extenso(meses_a1)
            avisos.append((
                f"{a1} ainda está em andamento: há dados só até {mes_fim_txt}.",
                f"Por isso, a variação compara {meses_cmp} de {ref} com {meses_cmp} "
                f"de {a1}." + ("" if unico else f" No total do período, {a1} entra com "
                                              "menos meses que os outros anos."),
            ))
        ausentes = (ssp.ausentes.filter(pl.col("ano").is_in(anos_per))
                    .join(ssp.ras.select("cd_subdist", "ra"), on="cd_subdist"))
        if ausentes.height:
            avisos.append((
                "Algumas RAs não têm dados em certos anos:",
                f"{_lista_ras(ausentes)}. O site da SSP-DF não publica o balanço correto "
                "delas nesses anos. Essas RAs ficam fora do total e da taxa só nesses anos; "
                "o resto do DF é contado normalmente.",
            ))
        sem_detalhe = (ra_ano.filter(pl.col("ano").is_in(anos_per) & pl.col("total").is_null())
                       .join(ssp.ras.select("cd_subdist", "ra"), on="cd_subdist"))
        if sem_detalhe.height:
            avisos.append((
                "Algumas RAs não têm os números mês a mês:",
                f"{_lista_ras(sem_detalhe)}. O balanço delas traz só o total do ano. Como "
                "você filtrou meses, elas ficam fora da conta nesses anos.",
            ))
        faltando = []
        for n in naturezas:
            faltam = sorted(set(anos_per) - ssp.anos_natureza[n])
            if not faltam:
                continue
            primeiro = min(ssp.anos_natureza[n])
            if all(a < primeiro for a in faltam):
                faltando.append(f"{_nome_natureza(n)} só existe a partir de {primeiro}")
            else:
                faltando.append(f"{_nome_natureza(n)} não aparece em {_faixas(faltam)}")
        if faltando:
            avisos.append((
                "Nem toda natureza existe em todos os anos:",
                "; ".join(faltando) + ". Nos anos em que não aparece, a natureza conta como zero"
                + (f"; em {_faixas(sorted(anos_sem & set(anos_per)))}, nenhuma das escolhidas "
                   "existe e o período fica sem dado." if anos_sem & set(anos_per) else "."),
            ))
        antes = {cd: mae for (cd, a), mae in nao_existia.items() if a in anos_per}
        if antes:
            filhas = " e ".join(_nome_ra(nome_ra[cd]) for cd in antes)
            maes = " e ".join(_nome_ra(nome_ra[m]) for m in antes.values())
            criacao = min(RAS_DESMEMBRADAS[cd][1] for cd in antes)
            avisos.append((
                f"{filhas} só existem a partir de {criacao}.",
                f"Antes disso, os registros e a população delas estão em {maes}, "
                "respectivamente.",
            ))
        aviso = _caixa_avisos(avisos, None if naturezas else
                              "Nenhuma natureza selecionada: escolha ao menos uma no "
                              "filtro acima.")

        # --- KPIs ------------------------------------------------------------
        n_dp = int(ssp.ras["n_delegacias"].sum())
        com_dado = por_ra.filter(pl.col("pop_validos").is_not_null())
        sem_dp = com_dado.filter(pl.col("n_delegacias") == 0)
        com_dp = com_dado.filter(pl.col("n_delegacias") > 0)
        taxa_sem = (sem_dp["total"].sum() * BASE_TAXA / sem_dp["pop_validos"].sum()
                    if sem_dp.height else None)
        taxa_com = (com_dp["total"].sum() * BASE_TAXA / com_dp["pop_validos"].sum()
                    if com_dp.height else None)
        lider = (por_ra.sort("taxa", descending=True, nulls_last=True).row(0, named=True)
                 if total else None)
        if naturezas and (ref in anos_sem or a1 in anos_sem):
            nota_var = (f"As naturezas escolhidas não existem em "
                        f"{ref if ref in anos_sem else a1}")
        elif unico:
            nota_var = (f"{fmt(total_ref)} em {_periodo_txt([ref], meses_a1)}"
                        if tem_ref else "Sem ano anterior comparável")
        else:
            nota_var = (f"{_periodo_txt([ref], meses_a1)}: {fmt(total_ref)} → "
                        f"{_periodo_txt([a1], meses_a1)}: {fmt(total_alvo)}"
                        if tem_ref else f"Sem dado comparável em {ref} ou {a1}")
        if tem_ref and por_ra_cmp.height < ssp.ras.height:
            nota_var += f" · só as {por_ra_cmp.height} RAs com dado nos dois anos"
        kpis = [
            kpi("Ocorrências", "—" if lacuna else fmt(total),
                f"{len(naturezas)} natureza(s) · {periodo_txt}"),
            kpi(f"Taxa DF {base_txt}" + ("" if unico else " (média anual)"),
                "—" if lacuna else fmt(taxa, 2),
                f"População {a1}: {fmt(pop_a1)}"
                + (" (só as RAs com balanço)" if (ssp.ausentes["ano"] == a1).any() else "")),
            kpi(f"Variação sobre {ref}" if unico else f"Variação {ref} → {a1}",
                _fmt_var(_variacao(total_alvo, total_ref) if tem_ref else None), nota_var),
            kpi("RA com maior taxa", lider["ra"] if lider else "—",
                f"{fmt(lider['taxa'], 2)} {rotulo_taxa}" if lider else ""),
            kpi("Delegacias", str(n_dp),
                f"{fmt(pop_a1 / n_dp)} habitantes por delegacia em {a1}"),
            kpi("RAs sem delegacia",
                f"{ssp.ras.filter(pl.col('n_delegacias') == 0).height} de {ssp.ras.height}",
                "" if lacuna else
                f"Taxa {fmt(taxa_sem, 2)} vs. {fmt(taxa_com, 2)} nas RAs com delegacia"),
        ]

        # --- ranking: cor pela presença de delegacia (identidade, não magnitude)
        rk = por_ra.filter(pl.col("taxa").is_not_null()).sort("taxa", descending=True)
        ranking = _barras_ranking(
            rk["ra"].to_list(), rk["taxa"].to_list(),
            [f"<b>{r['ra']}</b><br>{fmt(r['taxa'], 2)} {rotulo_taxa}"
             f"<br>{fmt(r['total'])} ocorrências em {anos_txt} · {r['n_delegacias']} delegacia(s)"
             for r in rk.to_dicts()],
            [AZUL if n else LARANJA for n in rk["n_delegacias"]], 640,
        ) if total else _vazio(640, "Sem ocorrências no filtro")
        ranking.update_yaxes(tickfont=dict(size=11))
        ranking_titulo = f"Taxa por RA em {anos_txt}"
        ranking_sub = (f"Ocorrências {rotulo_taxa}, da maior para a menor. Em laranja, as RAs "
                       "sem delegacia.")

        # --- série mensal (todos os anos), soma das RAs ----------------------
        mensal = (
            calendario.join(
                ssp.mensal.filter(pl.col("natureza").is_in(naturezas))
                .group_by("ano", "mes").agg(pl.col("valor").sum()),
                on=["ano", "mes"], how="left")
            .with_columns(pl.col("valor").fill_null(0))
            .sort("ano", "mes")
        )
        inicio = _inicio(naturezas)
        if inicio is not None:
            mensal = mensal.with_columns(
                pl.when(pl.col("ano") * 100 + pl.col("mes") < inicio[0] * 100 + inicio[1])
                .then(None).otherwise(pl.col("valor")).alias("valor"))
        mensal = mensal.with_columns(pl.col("valor").rolling_mean(12).alias("media"))
        datas = [f"{a}-{m:02d}-01" for a, m in mensal.select("ano", "mes").rows()]
        hover_m = []
        for a, m, v, md in mensal.select("ano", "mes", "valor", "media").rows():
            txt = (f"<b>{MARCAS_MESES[m]}/{a}</b><br>"
                   + ("sem dado: as naturezas escolhidas ainda não existiam" if v is None
                      else f"{fmt(v)} ocorrências"))
            if md is not None:
                txt += f"<br>média de 12 meses: {fmt(md)}"
            if v is not None and faltam_mes.get((a, m)):
                txt += f"<br>{faltam_mes[(a, m)]} RA(s) sem dado no mês, fora da soma"
            hover_m.append(txt)
        serie = _serie_historica(datas, mensal["valor"].to_list(), mensal["media"].to_list(),
                                 hover_m, _faixas_serie(anos_per, meses, ssp.ultimo_mes), 320)
        serie_sub = ("Ocorrências por mês, somando as RAs, e média dos 12 meses anteriores. "
                     f"A faixa marca {anos_txt}; clique num mês para ver só aquele ano. Nos "
                     "meses em que falta alguma RA, o total fica abaixo do real: para comparar "
                     "anos, use a taxa da comparação anual.")
        valores_modelo, fora_modelo = _serie_para_modelo(naturezas)
        ultimo_txt = f"{MESES_EXTENSO[meses_cal[-1][1] - 1]} de {meses_cal[-1][0]}"
        nomes_fora = ", ".join(_nome_natureza(n) for n in fora_modelo)
        serie_modelo = {"datas": datas, "valores": valores_modelo,
                        "unidade": "ocorrências", "casas": 0,
                        "obs": (f"Ficam fora da previsão, por não terem dado em {ultimo_txt}: "
                                f"{nomes_fora}."
                                if fora_modelo and len(fora_modelo) < len(naturezas) else "")}
        # Todas as escolhidas saíram dos balanços antes do fim da série (FURTO A
        # TRANSEUNTE não sai em 2026): sem isso, o motivo seria "poucos meses".
        if fora_modelo and len(fora_modelo) == len(naturezas):
            ultimo_ano = max(max(ssp.anos_natureza[n]) for n in fora_modelo)
            serie_modelo["motivo"] = (
                f"{nomes_fora} {'não aparece' if len(fora_modelo) == 1 else 'não aparecem'} "
                f"nos balanços depois de {ultimo_ano}, e a série precisa ter dado no último "
                f"mês ({ultimo_txt})")

        # --- comparação anual: taxa do DF só com as RAs que têm dado no ano --
        anual = (
            pl.DataFrame({"ano": anos})
            .join(ra_ano.filter(pl.col("total").is_not_null()).group_by("ano")
                  .agg(pl.col("total").sum(), pl.col("populacao").sum(), pl.len().alias("n_ras")),
                  on="ano", how="left")
            .with_columns(
                pl.when(pl.col("ano").is_in(list(anos_sem))).then(None)
                .otherwise(pl.col("total")).alias("total"))
            .with_columns((pl.col("total") * BASE_TAXA / pl.col("populacao")).alias("taxa"))
            .sort("ano")
        )
        n_existentes = {a: ssp.ras.height - sum(1 for (_, x) in nao_existia if x == a)
                        for a in anos}
        parcial = {a for a in anos if ssp.ultimo_mes[a] < meses[-1]}
        linhas = anual.to_dicts()
        hover_a = []
        for i, r in enumerate(linhas):
            if r["ano"] in anos_sem:
                hover_a.append(f"<b>{r['ano']}</b><br>sem dado: as naturezas escolhidas "
                               "ainda não existiam")
                continue
            txt = (f"<b>{r['ano']}</b>"
                   + (f" (até {MARCAS_MESES[ssp.ultimo_mes[r['ano']]]})" if r["ano"] in parcial else "")
                   + f"<br>{fmt(r['taxa'], 2)} {base_txt}<br>{fmt(r['total'])} ocorrências")
            ant = linhas[i - 1] if i else None
            if ant and ant["taxa"] and r["ano"] not in parcial and ant["ano"] not in parcial:
                txt += f"<br>{_fmt_var(_variacao(r['taxa'], ant['taxa']))} sobre {ant['ano']}"
            fora = n_existentes[r["ano"]] - (r["n_ras"] or 0)
            if fora:
                txt += f"<br>{fora} RA(s) sem dado, fora da taxa"
            hover_a.append(txt)
        anual_fig = go.Figure(go.Bar(
            x=[str(a) for a in anual["ano"]], y=anual["taxa"].to_list(),
            marker=dict(
                color=[AZUL if a in anos_per else AZUL_CLARO for a in anual["ano"]],
                pattern=dict(shape=["/" if a in parcial else "" for a in anual["ano"]],
                             fgcolor="white", solidity=0.3),
                cornerradius=4,
            ),
            customdata=hover_a, hovertemplate="%{customdata}<extra></extra>",
        ))
        _estilo(anual_fig, 320, bargap=0.3)
        anual_fig.update_xaxes(type="category")
        meses_txt = _recorte_txt(meses)
        anual_sub = (f"Taxa do DF {base_txt} ({meses_txt}, em todos os anos), só com as RAs que "
                     f"têm dado no ano. Em destaque, {anos_txt}"
                     + ("; hachurado, ano ainda incompleto" if parcial else "")
                     + ". A população de cada ano é a estimativa do IBGE para o DF, rateada "
                     "entre as RAs pela participação de cada uma no Censo 2022.")

        # --- mapa de calor: RA × ano ------------------------------------------
        def txt_celula(cd, a, v, t):
            if a in anos_sem:
                return "as naturezas escolhidas ainda não existiam"
            if (cd, a) in nao_existia:
                return f"RA ainda não existia: dados em {nome_ra[nao_existia[(cd, a)]]}"
            if (cd, a) in ausentes_set:
                return "sem balanço válido"
            if v is None:
                return "balanço sem os meses"
            return f"{fmt(t, 2)} {base_txt}<br>{fmt(v)} ocorrências"

        calor = (
            ssp.ras.select("cd_subdist", "ra")
            .join(pl.DataFrame({"ano": anos}), how="cross")
            .join(ra_ano, on=["cd_subdist", "ano"], how="left")
            .with_columns(
                pl.when(pl.col("ano").is_in(list(anos_sem))).then(None)
                .otherwise(pl.col("total")).alias("total"))
            .with_columns((pl.col("total") * BASE_TAXA / pl.col("populacao")).alias("taxa"))
        )
        calor = calor.with_columns(pl.Series("txt", [
            txt_celula(*r) for r in calor.select("cd_subdist", "ano", "total", "taxa").rows()
        ], dtype=pl.String))
        # Mesma ordem do ranking: pela taxa do período escolhido.
        idx = (pl.DataFrame({"ra": por_ra.sort(pl.col("taxa").fill_null(-1), descending=True)["ra"]})
               .with_row_index("i"))
        grade = (calor.pivot("ano", index="ra", values="taxa", sort_columns=True)
                 .join(idx, on="ra").sort("i"))
        grade_txt = (calor.pivot("ano", index="ra", values="txt", sort_columns=True)
                     .join(idx, on="ra").sort("i"))
        cols_ano = [str(a) for a in anos]
        calor_fig = go.Figure(go.Heatmap(
            z=grade.select(cols_ano).rows(),
            x=[a + ("*" if int(a) in parcial else "") for a in cols_ano],
            y=grade["ra"].to_list(), customdata=grade_txt.select(cols_ano).rows(),
            colorscale=RAMPA_AZUL, zmin=0, xgap=2, ygap=2, hoverongaps=True,
            hovertemplate="<b>%{y} · %{x}</b><br>%{customdata}<extra></extra>",
            colorbar=dict(thickness=10, len=0.6, outlinewidth=0, tickfont=dict(size=11),
                          title=dict(text=base_txt, side="right", font=dict(size=11))),
        ))
        _estilo(calor_fig, 22 * grade.height + 70)
        # Lacunas ficam sem célula: o fundo cinza aparece no lugar.
        calor_fig.update_layout(plot_bgcolor=LACUNA)
        calor_fig.update_xaxes(type="category", side="top", gridcolor="rgba(0,0,0,0)")
        calor_fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)",
                               ticksuffix="  ", tickfont=dict(size=11))
        calor_sub = (f"Taxa {base_txt} de cada RA em cada ano ({meses_txt}), da maior para a "
                     f"menor taxa em {anos_txt}. Cinza: RA sem balanço válido, balanço sem os "
                     "meses do recorte, RA ainda não criada ou natureza que ainda não existia."
                     + (" *Ano ainda incompleto." if parcial else "")
                     + " Clique numa célula para ver só aquele ano.")

        # --- tabela ----------------------------------------------------------
        n_lacunas = (
            pl.concat([ausentes.select("cd_subdist", "ano"),
                       sem_detalhe.select("cd_subdist", "ano")])
            .group_by("cd_subdist").agg(pl.len().cast(pl.Int64).alias("n_lacunas"))
        )
        tabela = (
            por_ra.join(ssp.pop.filter(pl.col("ano") == a1).drop("ano"), on="cd_subdist",
                        how="left")
            .join(por_ra_cmp, on="cd_subdist", how="left")
            .join(n_lacunas, on="cd_subdist", how="left")
            .with_columns(
                pl.when(pl.col("anterior") > 0)
                .then(100 * (pl.col("atual") - pl.col("anterior")) / pl.col("anterior"))
                .alias("variacao"),
                pl.col("n_lacunas").fill_null(0),
            )
            .sort("taxa", descending=True, nulls_last=True)
            .select("ra", "populacao", "n_delegacias", "total", pl.col("taxa").round(2),
                    "anterior", *([] if unico else ["atual"]), pl.col("variacao").round(1),
                    *([] if unico else ["n_lacunas"]), pl.col("delegacias").fill_null("—"))
        )
        num = dict(type="numeric", format={"locale": {"group": ".", "decimal": ","},
                                            "specifier": ",.0f"})
        colunas = [
            {"name": "Região Administrativa", "id": "ra"},
            {"name": f"População {a1}", "id": "populacao", **num},
            {"name": "Delegacias", "id": "n_delegacias", **num},
            {"name": f"Ocorrências {anos_txt}", "id": "total", **num},
            {"name": f"Taxa /{fmt(BASE_TAXA)} hab." + ("" if unico else " ao ano"), "id": "taxa",
             **num, "format": {**num["format"], "specifier": ",.2f"}},
            {"name": f"Ocorrências {ref}", "id": "anterior", **num},
        ]
        if not unico:
            colunas.append({"name": f"Ocorrências {a1}", "id": "atual", **num})
        colunas.append({"name": "Variação (%)", "id": "variacao", **num,
                        "format": {**num["format"], "specifier": "+,.1f"}})
        if not unico:
            colunas.append({"name": "Anos sem dado", "id": "n_lacunas", **num})
        colunas.append({"name": "Delegacias na RA", "id": "delegacias"})

        # O rótulo da taxa termina em "hab.": evita o ".." no fim da frase.
        anual_sub, ranking_sub, calor_sub = (
            t.replace("..", ".") for t in (anual_sub, ranking_sub, calor_sub))
        return (aviso, kpis, ranking, ranking_titulo, ranking_sub,
                serie, serie_sub, serie_modelo, anual_fig, anual_sub, calor_fig, calor_sub,
                tabela.to_dicts(), colunas)


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------


def criar_app() -> Dash:
    mj = carregar_mj()
    ssp = carregar_ssp()
    anos_ssp = f"{ssp.anos[0]}–{ssp.anos[-1]}"

    app = Dash(
        __name__,
        title="Painel de Segurança Pública",
        assets_folder=str(Path(__file__).parent / "assets"),
        external_stylesheets=[
            "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"
        ],
        suppress_callback_exceptions=True,
    )
    app.layout = html.Div([
        dcc.Location(id="url"),
        html.Header([
            html.H1("Painel de Segurança Pública"),
            html.P(f"Ocorrências e vítimas no Brasil (MJ/Sinesp, {mj.anos[0]}–{mj.anos[-1]}) e "
                   f"no Distrito Federal (SSP-DF, {anos_ssp}), com a distribuição das "
                   "delegacias"),
            dcc.Tabs(id="abas", value="mj", className="abas", children=[
                dcc.Tab(label="Brasil — MJ/Sinesp", value="mj",
                        className="aba", selected_className="aba--sel"),
                dcc.Tab(label="Distrito Federal — SSP-DF", value="ssp",
                        className="aba", selected_className="aba--sel"),
            ]),
        ], className="cabecalho"),
        html.Main([
            html.Div(_layout_mj(mj), id="pagina-mj"),
            html.Div(_layout_ssp(ssp), id="pagina-ssp", style={"display": "none"}),
            html.Footer([
                html.P(f"Fontes: Ministério da Justiça — BancoVDE/Sinesp {mj.anos[0]}–"
                       f"{mj.anos[-1]}; SSP-DF — balanços por Região Administrativa {anos_ssp}; "
                       "IBGE — estimativas de população, Censo 2022 e malhas territoriais."),
                html.P("População no MJ: a do município no ano (estimativa do IBGE; Censo "
                       "2022 em 2022 e 2023). O IBGE refaz as estimativas a cada Censo, então "
                       "parte da variação da taxa entre anos pode vir da população."),
                html.P("População no DF: a estimativa do IBGE para o DF no ano, rateada entre "
                       "as RAs pela participação de cada uma no Censo 2022 (o IBGE não estima "
                       "população abaixo do município). As delegacias são as de hoje, em "
                       "todos os anos."),
                html.P(f"Taxas: por {fmt(BASE_TAXA_MJ)} habitantes no Brasil; por "
                       f"{fmt(BASE_TAXA)} no DF, onde há RAs com poucos milhares de "
                       "residentes. A população é a residente — em RAs de perfil não "
                       "residencial (ex.: SIA) a taxa fica superestimada."),
            ], className="rodape"),
        ], className="conteudo"),
    ])

    # `?aba=ssp` abre direto na aba do DF (links compartilháveis).
    @app.callback(Output("abas", "value"), Input("url", "search"))
    def _aba_da_url(busca):
        return "ssp" if busca and "aba=ssp" in busca else "mj"

    # As duas páginas ficam montadas e só alternam a visibilidade, para não
    # refazer os callbacks (e o mapa folium) a cada troca de aba.
    @app.callback(
        Output("pagina-mj", "style"),
        Output("pagina-ssp", "style"),
        Input("abas", "value"),
    )
    def _abas(aba):
        esconder = {"display": "none"}
        return ({}, esconder) if aba == "mj" else (esconder, {})

    _registrar_mj(app, mj)
    _registrar_ssp(app, ssp)
    _registrar_previsao(app, "mj")
    _registrar_previsao(app, "ssp")
    return app
