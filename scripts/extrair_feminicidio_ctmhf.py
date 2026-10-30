# /// script
# requires-python = ">=3.13"
# dependencies = ["polars>=1.30", "pdfplumber>=0.11", "scipy>=1.13"]
# ///
"""Extrai os feminicídios por RA e mês dos estudos mensais da Câmara Técnica de
Monitoramento de Homicídios e Feminicídios (CTMHF/SSP-DF), salvos em
`data/dados_ssp_df/feeminicidio/<ano>/<Mês>.pdf`.

Os anos saem dos próprios estudos: entra todo ano com estudo de quase todos os
meses (ver :data:`MESES_FALTANDO_MAX`), porque o mês de cada caso vem da
diferença entre estudos seguidos. Os anos anteriores já chegam fechados no
primeiro estudo, sem como saber o mês dos casos de cada RA, e ficam de fora.
Os estudos de janeiro/2021 a fevereiro/2023 trazem a tabela por RA × ano, mas
não a de casos por mês × ano; em 2021 e 2022, o mês sai do estudo em que cada
caso apareceu (ver :func:`reconstruir_por_entrada`).

Cada estudo é acumulado (março de 2015 até o fim do mês do estudo) e traz duas
tabelas que interessam aqui:

- TABELA 03, casos por mês × ano (o DF inteiro);
- TABELA 04, casos por RA × ano (TABELA 03 ou 04 nos estudos até 2023).

Nenhuma traz RA × mês. O mês de cada caso por RA é reconstruído pela diferença
entre estudos consecutivos: o que entra na TABELA 04 de um estudo para o outro
são os casos novos daquela RA, e o que entra na TABELA 03 diz o mês deles. Os
estudos também revisam casos antigos (um caso deixa de ser feminicídio, muda de
RA ou de mês), então cada passo procura o menor número de casos removidos e
incluídos que leva de um estudo ao seguinte, e mantém todas as reconstruções
igualmente curtas: uma revisão posterior às vezes diz qual delas era a certa.
Quando, no fim, mais de uma reconstrução continua possível — vários casos novos
de RAs diferentes em meses diferentes, no mesmo estudo —, o script usa a
primeira e marca esses casos com `mes_incerto` no CSV de casos.

Os estudos seguem incluindo casos de anos já fechados: um homicídio que o
inquérito ou o processo passa a tratar como feminicídio entra meses depois. Por
isso a CTMHF diverge do MJ/Sinesp, que reproduz o registro policial. Para que o
painel do DF bata com o do MJ, cada ano usa o estudo mais recente cuja TABELA 03
coincide, mês a mês, com o feminicídio do DF no BancoVDE
(`mj_censo_geo.parquet`, de `consolidar_mj.py`, que deve rodar antes); os casos
incluídos depois dele ficam de fora, e o script os lista. Se nenhum estudo
coincide, usa o que menos difere e avisa a diferença; acima de
:data:`TOLERANCIA_MJ` casos, o ano fica de fora. Totais por RA × ano e por
mês × ano saem exatamente como no estudo usado; só a combinação RA × mês dos
casos marcados é incerta.

Saídas:
- `data/processed/ssp_df_feminicidio_ra.csv`: uma linha por RA e ano, no
  formato de `ssp_df_ra_consolidado.csv` (`ra_numero, ra, ano, eixo, natureza,
  contagem, total, jan … dez, fonte, arquivo`), com as 33 RAs do painel;
- `data/processed/ssp_df_feminicidio_casos.csv`: um caso por linha (`ano`,
  `mes`, `ra_ctmhf`, `ra_numero`, `estudo_entrada`, `mes_incerto`).

Arapoanga e Água Quente, RAs criadas em 2022 que a CTMHF já separa, vão para as
RAs de origem (Planaltina e Recanto das Emas), como nos balanços da SSP-DF.

Script independente do projeto (dependências declaradas acima). Uso:
    uv run --script scripts/extrair_feminicidio_ctmhf.py
"""

import itertools
import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pdfplumber
import polars as pl
from scipy.optimize import linear_sum_assignment

RAIZ = Path(__file__).resolve().parents[1]
ESTUDOS_DIR = RAIZ / "data" / "dados_ssp_df" / "feeminicidio"
SAIDA_RA_CSV = RAIZ / "data" / "processed" / "ssp_df_feminicidio_ra.csv"
SAIDA_CASOS_CSV = RAIZ / "data" / "processed" / "ssp_df_feminicidio_casos.csv"
MJ_PARQUET = RAIZ / "data" / "processed" / "mj_censo_geo.parquet"

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MESES_EXTENSO = ["JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO",
                 "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"]

# Eixo próprio: o HOMICÍDIO dos balanços das RAs já inclui os feminicídios.
EIXO = "5. FEMINICÍDIO - CTMHF"
NATUREZA = "FEMINICÍDIO"

# Nomes das RAs como em `ssp_df_ra_consolidado.csv`.
RAS: dict[int, str] = {
    1: "BRASÍLIA", 2: "GAMA", 3: "TAGUATINGA", 4: "BRAZLÂNDIA", 5: "SOBRADINHO",
    6: "PLANALTINA", 7: "PARANOÁ", 8: "NÚCLEO BANDEIRANTE", 9: "CEILÂNDIA", 10: "GUARÁ",
    11: "CRUZEIRO", 12: "SAMAMBAIA", 13: "SANTA MARIA", 14: "SÃO SEBASTIÃO",
    15: "RECANTO DAS EMAS", 16: "LAGO SUL", 17: "RIACHO FUNDO", 18: "LAGO NORTE",
    19: "CANDANGOLÂNDIA", 20: "ÁGUAS CLARAS", 21: "RIACHO FUNDO II", 22: "SUDOESTE/OCTOGONAL",
    23: "VARJÃO", 24: "PARK WAY", 25: "SCIA/ESTRUTURAL", 26: "SOBRADINHO II",
    27: "JARDIM BOTÂNICO", 28: "ITAPOÃ", 29: "SIA", 30: "VICENTE PIRES", 31: "FERCAL",
    32: "SOL NASCENTE/PÔR DO SOL", 33: "ARNIQUEIRA",
}

# Nome da RA na TABELA 04 (sem acento, caixa alta) → número da RA.
RA_DA_CTMHF: dict[str, int] = {
    "PLANO PILOTO": 1, "GAMA": 2, "TAGUATINGA": 3, "BRAZLANDIA": 4, "SOBRADINHO I": 5,
    "PLANALTINA": 6, "PARANOA": 7, "NUCLEO BANDEIRANTE": 8, "CEILANDIA": 9, "GUARA": 10,
    "CRUZEIRO": 11, "SAMAMBAIA": 12, "SANTA MARIA": 13, "SAO SEBASTIAO": 14,
    "RECANTO DAS EMAS": 15, "LAGO SUL": 16, "RIACHO FUNDO I": 17, "LAGO NORTE": 18,
    "CANDANGOLANDIA": 19, "AGUAS CLARAS": 20, "RIACHO FUNDO II": 21,
    "SUDOESTE/OCTOGONAL": 22, "VARJAO": 23, "PARK WAY": 24, "SCIA E ESTRUTURAL": 25,
    "SOBRADINHO II": 26, "JARDIM BOTANICO": 27, "ITAPOA": 28, "SIA": 29,
    "VICENTE PIRES": 30, "FERCAL": 31, "POR DO SOL / SOL NASCENTE": 32, "ARNIQUEIRA": 33,
    # Criadas em 2022; nos balanços da SSP-DF seguem nas RAs de origem.
    "ARAPOANGA": 6, "AGUA QUENTE": 15,
}

# Começo de rótulos (sem acento, caixa alta) que não casam com o nome de
# RA_DA_CTMHF nem pelo começo, como os de alguns estudos de 2022.
APELIDOS: dict[str, str] = {
    "SIA (": "SIA",
    "SOL NASCENTE / P": "POR DO SOL / SOL NASCENTE",
}

_NUMERO = re.compile(r"^(\d+|-)$")


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _casar(rotulo: str, nomes: list[str]) -> str | None:
    """O nome de `nomes` que corresponde a `rotulo`. A fonte dos PDFs não mapeia
    as letras acentuadas, que o pdfplumber devolve como U+FFFD; elas valem como
    qualquer letra. Rótulo cortado com reticências ("Pôr do Sol / Sol Na…")
    vale pelo começo."""
    texto = " ".join(rotulo.split())
    cortado = texto.endswith(("…", "..."))
    texto = texto.rstrip("….").rstrip()
    padrao = "".join("." if c == "�" else re.escape(c) for c in _sem_acento(texto).upper())
    if cortado:
        padrao += ".*"
    achados = [n for n in nomes if re.fullmatch(padrao, n)]
    return achados[0] if len(achados) == 1 else None


def _linhas(page) -> list[list[dict]]:
    """Palavras da página agrupadas por linha (topo a até 4 pt), da esquerda
    para a direita."""
    linhas: list[tuple[float, list[dict]]] = []
    for w in sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"])):
        if linhas and abs(w["top"] - linhas[-1][0]) <= 4:
            linhas[-1][1].append(w)
        else:
            linhas.append((w["top"], [w]))
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in linhas]


def _sem_sobreposicao(page, ws: list[dict]) -> list[dict]:
    """A linha `ws` refeita só com os caracteres do tamanho do primeiro. Em
    alguns estudos de 2022 o rodapé ("CTHMF / SSPDF - 2022", em fonte menor) cai
    sobre a linha "Total geral" e as letras se misturam aos números."""
    topo = ws[0]["top"]
    tamanho = min((c for c in page.chars if abs(c["top"] - topo) <= 4),
                  key=lambda c: c["x0"])["size"]
    filtrada = page.filter(lambda o: o.get("object_type") != "char"
                           or abs(o["top"] - topo) > 4 or abs(o["size"] - tamanho) < 0.1)
    return next(l for l in _linhas(filtrada) if abs(l[0]["top"] - topo) <= 4)


def _numeros(ws: list[dict]) -> list[int]:
    return [0 if w["text"] == "-" else int(w["text"]) for w in ws]


def _tabela_ra(page, n_anos: int) -> tuple[dict[str, list[int]], list[int]]:
    """Tabela de casos por RA × ano: linhas com o rótulo da RA (às vezes
    precedido do número dela) e uma célula por ano mais o total ("-" é zero).
    Em 2021 a tabela das taxas fica ao lado, na mesma linha: só vale a primeira
    sequência de números depois do rótulo. Devolve RA → valores e a linha
    "Total geral"."""
    corpo, total = {}, None
    for ws in _linhas(page):
        if ws[0]["text"] == "Total":
            ws = _sem_sobreposicao(page, ws)
        textos = [w["text"] for w in ws]
        numero = None
        if len(textos) > 1 and textos[0].isdigit() and not _NUMERO.match(textos[1]):
            numero, textos = int(textos[0]), textos[1:]
        i = next((i for i, t in enumerate(textos) if _NUMERO.match(t)), len(textos))
        j = next((j for j in range(i, len(textos)) if not _NUMERO.match(textos[j])), len(textos))
        if i == 0 or j - i < n_anos + 1:
            continue
        rotulo = " ".join(textos[:i])
        celulas = _numeros([{"text": t} for t in textos[i:i + n_anos + 1]])
        if rotulo.startswith("Total"):
            total = celulas
            continue
        # Rótulos cortados ("Riacho Fun…") só se distinguem pelo número da RA.
        nomes = [n for n, k in RA_DA_CTMHF.items() if numero in (None, k)]
        chave = _sem_acento(rotulo).upper()
        rotulo = next((n for a, n in APELIDOS.items() if chave.startswith(a)), rotulo)
        ra = _casar(rotulo, nomes)
        if ra is None:
            raise ValueError(f"RA desconhecida na tabela por RA: {rotulo!r}")
        corpo[ra] = celulas
    return corpo, total


def _tabela_mes(page, n_anos: int) -> tuple[dict[int, list[int]], list[int]]:
    """TABELA 03: uma linha por mês, com uma célula por ano mais o total."""
    corpo, total = {}, None
    for ws in _linhas(page):
        if len(ws) < 2:
            continue
        # "geral" às vezes sai truncado ("g�") ou ilegível ("�").
        geral = ws[0]["text"] == "Total" and not _NUMERO.match(ws[1]["text"])
        mes = None if geral else _casar(ws[0]["text"], MESES_EXTENSO)
        if not geral and mes is None:
            continue
        borda = ws[1 if geral else 0]["x1"]
        celulas = [w for w in ws if w["x0"] > borda and _NUMERO.match(w["text"])]
        if len(celulas) != n_anos + 1:
            continue  # o "Total" da TABELA 02, na mesma página
        if geral:
            total = _numeros(celulas)
        else:
            corpo[MESES_EXTENSO.index(mes) + 1] = _numeros(celulas)
    return corpo, total


class EstudoSemTabelas(Exception):
    """O estudo não tem, no formato esperado, o período ou a tabela por RA."""


def _periodo(pdf) -> tuple[int, int]:
    """Ano e mês do fim do estudo: da capa ("acumulado: março de 2015 a agosto
    de 2022", às vezes com as letras espaçadas) ou, quando a capa só diz o ano,
    da apresentação ("... e o dia 30 do mês de setembro de 2021")."""
    capa = re.sub(r"\s+", "", pdf.pages[0].extract_text() or "")
    m = re.search(r"acumulado:?mar.ode2015a(\D+?)de(20\d\d)", capa, re.IGNORECASE)
    if not m:
        for page in pdf.pages[1:4]:
            texto = " ".join((page.extract_text() or "").split())
            m = re.search(r"dia \d+ do m.s de (\S+) de (20\d\d)", texto, re.IGNORECASE)
            if m:
                break
    if not m:
        raise EstudoSemTabelas("período do estudo não encontrado")
    mes = _casar(m.group(1), MESES_EXTENSO)
    if mes is None:
        raise EstudoSemTabelas(f"mês do período ilegível: {m.group(1)!r}")
    return int(m.group(2)), MESES_EXTENSO.index(mes) + 1


def ler_estudo(path: Path) -> dict:
    """Período do estudo, a tabela de casos por RA × ano e, quando há, a TABELA
    03 (casos por mês × ano), conferidas pelos totais. Sem a TABELA 03, `mes`
    fica vazio."""
    with pdfplumber.open(path) as pdf:
        ano_fim, mes_fim = _periodo(pdf)
        n_anos = ano_fim - 2015 + 1
        pag_ra = pag_mes = None
        for page in pdf.pages:
            texto = page.extract_text() or ""
            if "TABELA 03. QUANTIDADE DE CASOS" in texto:
                pag_mes = page
            if re.search(r"TABELA 0\d\. TOTAL DE .* POR RA - 2015 A " + str(ano_fim), texto):
                pag_ra = page
        if pag_ra is None:
            raise EstudoSemTabelas("sem a tabela de casos por RA × ano")
        try:
            por_ra, total_ra = _tabela_ra(pag_ra, n_anos)
            por_mes, total_mes = _tabela_mes(pag_mes, n_anos) if pag_mes else ({}, None)
        except ValueError as e:
            raise ValueError(f"{path}: {e}") from e

    soma_ra = [sum(c) for c in zip(*por_ra.values())]
    if soma_ra != total_ra:
        raise ValueError(f"{path}: a tabela por RA não fecha ({soma_ra} × {total_ra})")
    if pag_mes:
        soma_mes = [sum(c) for c in zip(*por_mes.values())]
        if len(por_mes) != 12 or not (soma_ra == soma_mes == total_mes):
            raise ValueError(f"{path}: as tabelas por RA e por mês não fecham "
                             f"({total_ra} × {total_mes})")
    anos = range(2015, ano_fim + 1)
    return {
        "arquivo": path.relative_to(ESTUDOS_DIR).as_posix(),
        "fim": (ano_fim, mes_fim),
        "ra": {a: {ra: v[i] for ra, v in por_ra.items() if v[i]} for i, a in enumerate(anos)},
        "mes": {a: {mes: v[i] for mes, v in por_mes.items() if v[i]} for i, a in enumerate(anos)}
               if pag_mes else {},
    }


# ---------------------------------------------------------------------------
# Reconstrução dos casos (RA, mês) de um ano
# ---------------------------------------------------------------------------

Estado = tuple[tuple[str, int], ...]


def _pareamentos(ras: Counter, meses: Counter) -> set[Estado]:
    """Todas as formas de dar um mês de `meses` a cada caso novo de `ras`."""
    lista_ras = sorted(ras.elements())
    resultado: set[Estado] = set()

    def _rec(i: int, livres: Counter, casos: list[tuple[str, int]]) -> None:
        if i == len(lista_ras):
            resultado.add(tuple(sorted(casos)))
            return
        for mes in sorted(m for m, n in livres.items() if n):
            livres[mes] -= 1
            _rec(i + 1, livres, [*casos, (lista_ras[i], mes)])
            livres[mes] += 1

    _rec(0, Counter(meses), [])
    return resultado


def _passos(estado: Estado, alvo_ra: dict[str, int], alvo_mes: dict[int, int]
            ) -> tuple[int, set[Estado]]:
    """Os estados que chegam aos totais do estudo seguinte removendo o menor
    número possível de casos de `estado` (e incluindo os que faltam)."""
    def _faltam(casos, chave, alvo):
        atual = Counter(c[chave] for c in casos)
        return Counter({k: alvo.get(k, 0) - atual[k] for k in set(alvo) | set(atual)})

    d_ra, d_mes = _faltam(estado, 0, alvo_ra), _faltam(estado, 1, alvo_mes)
    minimo = max(sum(-v for v in d_ra.values() if v < 0), sum(-v for v in d_mes.values() if v < 0))
    for k in range(minimo, len(estado) + 1):
        novos: set[Estado] = set()
        for removidos in set(itertools.combinations(estado, k)):
            resto = list((Counter(estado) - Counter(removidos)).elements())
            f_ra, f_mes = _faltam(resto, 0, alvo_ra), _faltam(resto, 1, alvo_mes)
            if any(v < 0 for v in f_ra.values()) or any(v < 0 for v in f_mes.values()):
                continue
            for incluidos in _pareamentos(+f_ra, +f_mes):
                novos.add(tuple(sorted([*resto, *incluidos])))
        if novos:
            return k, novos
    raise ValueError("estudo sem reconstrução possível")


def reconstruir(estudos: list[dict], ano: int) -> tuple[Estado, set[tuple[str, int]], dict]:
    """Casos (RA da CTMHF, mês) de `ano` no último estudo, as combinações
    (número da RA, mês) que não ficam determinadas e o estudo em que cada caso
    apareceu. A incerteza é medida já nas RAs do painel: um caso que só passou
    de Planaltina para Arapoanga não é incerto."""
    estados: dict[Estado, int] = {(): 0}  # estado → casos removidos até aqui
    entrada: dict[tuple[str, int], str] = {}
    for estudo in estudos:
        if ano not in estudo["mes"]:
            continue
        candidatos: dict[Estado, int] = {}
        for estado, custo in estados.items():
            k, novos = _passos(estado, estudo["ra"][ano], estudo["mes"][ano])
            for novo in novos:
                if custo + k < candidatos.get(novo, 1 << 30):
                    candidatos[novo] = custo + k
                for caso in set(novo) - set(estado):
                    entrada.setdefault(caso, estudo["arquivo"])
        menor = min(candidatos.values())
        estados = {e: c for e, c in candidatos.items() if c == menor}

    finais = sorted(estados)
    contagens = [Counter((RA_DA_CTMHF[ra], mes) for ra, mes in e) for e in finais]
    celulas = set().union(*contagens)
    incertas = {c for c in celulas if len({cnt[c] for cnt in contagens}) > 1}
    return finais[0], incertas, entrada


# Peso de datar um caso depois do estudo em que ele apareceu (só uma revisão
# explica isso), contra 1 por mês de antecedência.
CUSTO_DEPOIS = 100


def reconstruir_por_entrada(estudos: list[dict], ano: int
                            ) -> tuple[Estado, set[tuple[int, int]], dict]:
    """Como :func:`reconstruir`, para os anos em que nenhum estudo do próprio
    ano traz a TABELA 03 (2021 e 2022). Cada caso novo de uma RA, de um estudo
    para o seguinte, recebe o mês do estudo em que apareceu; um caso que sai
    leva o mais recente da RA. No fim, os meses são ajustados à TABELA 03 do
    último estudo (`estudos[-1]`) com o menor deslocamento total: um caso pode
    ter acontecido antes do estudo em que apareceu (registro tardio), quase
    nunca depois. Fica incerto o caso que muda de mês e, quando o deslocado
    poderia ser outro, os que apareceram com ele em outras RAs."""
    entradas: dict[str, list[tuple[int, str]]] = {}  # RA → (mês de entrada, estudo)
    for e in estudos:
        if ano not in e["ra"]:
            continue
        # Meses contados a partir de janeiro do ano: 13 é o estudo de janeiro seguinte.
        mes_entrada = (e["fim"][0] - ano) * 12 + e["fim"][1]
        for ra in set(entradas) | set(e["ra"][ano]):
            casos = entradas.setdefault(ra, [])
            n = e["ra"][ano].get(ra, 0)
            del casos[n:]
            casos.extend([(mes_entrada, e["arquivo"])] * (n - len(casos)))

    casos = [(ra, ent, arq) for ra, lista in entradas.items() for ent, arq in lista]
    vagas = [m for m, n in sorted(estudos[-1]["mes"][ano].items()) for _ in range(n)]
    custo = [[ent - m if m <= ent else CUSTO_DEPOIS * (m - ent) for m in vagas]
             for _, ent, _ in casos]
    linhas, colunas = linear_sum_assignment(custo)

    final, entrada, grupos = [], {}, {}
    for li, co in zip(linhas, colunas):
        ra, ent, arq = casos[li]
        mes = vagas[co]
        final.append((ra, mes))
        entrada.setdefault((ra, mes), arq)
        grupos.setdefault(ent, []).append((RA_DA_CTMHF[ra], mes, mes != ent))
    incertas = set()
    for membros in grupos.values():
        trocavel = any(m for *_, m in membros) and len({ra for ra, *_ in membros}) > 1
        incertas |= {(ra, mes) for ra, mes, movido in membros if movido or trocavel}
    return tuple(sorted(final)), incertas, entrada


# Casos de diferença tolerados, num ano, entre o estudo usado e o MJ (soma das
# diferenças de cada mês); acima disso, o ano fica de fora. O mesmo limite está
# em `scripts/consolidar_ssp.py`.
TOLERANCIA_MJ = 3

# Meses do ano que podem faltar entre os estudos. Cada mês faltando junta os
# casos de dois meses na mesma diferença (o de 2023 começa em março, porque os
# estudos de janeiro e fevereiro não têm a tabela por mês); mais que isso junta
# casos demais para separar o mês de cada um.
MESES_FALTANDO_MAX = 2


def _anos_reconstruiveis(estudos: list[dict]) -> list[int]:
    """Anos com estudo de todos os meses (até dezembro, ou até o último estudo,
    no ano corrente), menos no máximo :data:`MESES_FALTANDO_MAX`."""
    fins = {e["fim"] for e in estudos}
    ultimo = max(fins)
    anos = []
    for a in sorted({a for a, _ in fins}):
        ate = ultimo[1] if a == ultimo[0] else 12
        faltam = [m for m in range(1, ate + 1) if (a, m) not in fins]
        if len(faltam) <= MESES_FALTANDO_MAX:
            anos.append(a)
            if faltam:
                print(f"{a}: sem os estudos de {', '.join(MESES[m - 1] for m in faltam)}; "
                      "os casos desses meses entram juntos com os do mês seguinte")
        else:
            print(f"{a} fica de fora: faltam os estudos de "
                  + ", ".join(MESES[m - 1] for m in faltam))
    return anos


def _feminicidio_mj(anos: list[int]) -> dict[int, list[int | None]]:
    """Feminicídio do DF no BancoVDE (abrangência estadual) por ano: os 12 meses,
    nulos os que o BancoVDE ainda não traz."""
    mj = (
        pl.scan_parquet(MJ_PARQUET)
        .filter((pl.col("sigla_uf") == "DF") & pl.col("ano").is_in(anos))
        .with_columns(pl.col("data_referencia").dt.month().alias("mes"))
        .collect()
    )
    ultimo = dict(mj.group_by("ano").agg(pl.col("mes").max()).rows())
    fem = dict(
        ((a, m), v) for a, m, v in
        mj.filter((pl.col("evento") == "Feminicídio") & (pl.col("abrangencia") == "Estadual"))
        .group_by("ano", "mes").agg(pl.col("total_vitima").sum()).rows()
    )
    return {a: [int(fem.get((a, m), 0)) if m <= ultimo[a] else None for m in range(1, 13)]
            for a in anos if a in ultimo}


def _estudo_do_mj(estudos: list[dict], ano: int, mj: list[int | None]) -> tuple[int, int]:
    """Índice do estudo mais recente que bate, mês a mês, com o MJ no ano (só os
    meses que o MJ já traz) e a diferença dele, em casos. Sem nenhum igual, o
    que menos difere (o mais recente, no empate)."""
    def diferenca(meses):
        return sum(abs(meses.get(m, 0) - v) for m, v in enumerate(mj, start=1) if v is not None)

    candidatos = [(diferenca(e["mes"][ano]), -i) for i, e in enumerate(estudos) if ano in e["mes"]]
    dif, menos_i = min(candidatos)
    return -menos_i, dif


def main() -> None:
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    estudos, ignorados = [], []
    for p in sorted(ESTUDOS_DIR.glob("*/*.pdf")):
        try:
            estudos.append(ler_estudo(p))
        except EstudoSemTabelas as e:
            ignorados.append(f"{p.relative_to(ESTUDOS_DIR).as_posix()} ({e})")
    estudos.sort(key=lambda e: e["fim"])
    print(f"{len(estudos)} estudos: {estudos[0]['arquivo']} … {estudos[-1]['arquivo']}")
    if ignorados:
        print(f"{len(ignorados)} ignorados:\n  " + "\n  ".join(ignorados))
    anos = _anos_reconstruiveis(estudos)
    referencia = _feminicidio_mj(anos)
    sem_mj = sorted(set(anos) - set(referencia))
    if sem_mj:
        raise ValueError(f"Sem feminicídio do DF no BancoVDE em {sem_mj}: rode consolidar_mj.py")

    casos, linhas = [], []
    for ano in anos:
        i, dif = _estudo_do_mj(estudos, ano, referencia[ano])
        ultimo = estudos[i]
        if dif > TOLERANCIA_MJ:
            print(f"{ano} fica de fora: o estudo mais próximo do MJ ({ultimo['arquivo']}) "
                  f"difere em {dif} casos, mês a mês")
            continue
        if dif:
            print(f"{ano}: nenhum estudo bate com o MJ mês a mês; o mais próximo "
                  f"({ultimo['arquivo']}) difere em {dif} caso(s), dentro da tolerância")
        com_tabela_mes = any(e["fim"][0] == ano and ano in e["mes"] for e in estudos)
        metodo = reconstruir if com_tabela_mes else reconstruir_por_entrada
        final, incertas, entrada = metodo(estudos[: i + 1], ano)
        por_ra = Counter(ra for ra, _ in final)
        por_mes = Counter(mes for _, mes in final)
        if por_ra != Counter(ultimo["ra"][ano]) or por_mes != Counter(ultimo["mes"][ano]):
            raise ValueError(f"{ano}: a reconstrução não bate com {ultimo['arquivo']}")
        # Nas RAs do painel: Planaltina → Arapoanga não é caso novo.
        def _no_painel(cont):
            return Counter(RAS[RA_DA_CTMHF[ra]] for ra in cont.elements())
        depois = _no_painel(Counter(estudos[-1]["ra"][ano])) - _no_painel(por_ra)
        print(f"{ano}: estudo {ultimo['arquivo']}"
              + (f" | incluídos depois, fora do painel: {dict(depois)}" if depois else ""))
        for ra, mes in final:
            casos.append({
                "ano": ano, "mes": mes, "ra_ctmhf": ra, "ra_numero": RA_DA_CTMHF[ra],
                "estudo_entrada": entrada.get((ra, mes)),
                "mes_incerto": (RA_DA_CTMHF[ra], mes) in incertas,
            })
        # Meses depois do último estudo ainda não aconteceram.
        ate = ultimo["fim"][1] if ano == ultimo["fim"][0] else 12
        cont = Counter((RA_DA_CTMHF[ra], mes) for ra, mes in final)
        for numero, nome in RAS.items():
            meses = [cont[(numero, m)] if m <= ate else None for m in range(1, 13)]
            linhas.append({
                "ra_numero": numero, "ra": nome, "ano": ano, "eixo": EIXO,
                "natureza": NATUREZA, "contagem": "OCORRÊNCIA",
                "total": sum(v for v in meses if v is not None),
                **dict(zip(MESES, meses)), "fonte": "ctmhf", "arquivo": ultimo["arquivo"],
            })
        n_inc = sum(1 for ra, mes in final if (RA_DA_CTMHF[ra], mes) in incertas)
        print(f"{ano}: {len(final)} casos até {MESES[ate - 1]} | {n_inc} com mês incerto")

    pl.DataFrame(linhas).write_csv(SAIDA_RA_CSV)
    pl.DataFrame(casos).sort("ano", "mes", "ra_ctmhf").write_csv(SAIDA_CASOS_CSV)
    print(f"Salvos: {SAIDA_RA_CSV} e {SAIDA_CASOS_CSV}")


if __name__ == "__main__":
    main()
