"""Bases do painel: MJ/Sinesp 2016–2026 (Brasil, por UF e município) e SSP-DF
2015–2026 (Distrito Federal, por Região Administrativa).

Tudo é carregado uma vez, na subida do app; os callbacks só filtram e agregam
DataFrames já enxutos.
"""

from dataclasses import dataclass

import geopandas as gpd
import polars as pl

from tcc.geo import carregar_setores, centroides_por_ra, delegacias_com_subdistrito, dissolver_por_ra
from tcc.mapas import EIXO_FORA_DA_TAXA
from tcc.paths import MJ_CENSO_GEO_PARQUET, SSP_RA_GEO_PARQUET
from tcc.populacao import RAS_DESMEMBRADAS

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

# Base da taxa no MJ. No DF a base é mil (ver `tcc.populacao.BASE_TAXA`), porque
# há RAs com poucos milhares de habitantes; entre UFs e municípios brasileiros a
# convenção — e a base do Anuário do FBSP — é 100 mil.
BASE_TAXA_MJ = 100_000

# Eventos do BancoVDE que o painel mostra, por categoria. Cada item é um evento
# da fonte (str) ou um grupo `(nome, [membros])`, que soma os membros — todos na
# mesma unidade. O que não está aqui fica de fora: atividades de bombeiros e de
# licenciamento (atendimento pré-hospitalar, busca e salvamento, combate a
# incêndios, alvarás, vistorias), pessoas desaparecidas/localizadas, suicídio,
# mortes a esclarecer, mortes no trânsito, mortes e suicídios de agentes do
# Estado, mortes por intervenção deles e mandados de prisão cumpridos.
CATEGORIAS_MJ: dict[str, list[str | tuple[str, list[str]]]] = {
    "Crimes violentos letais intencionais": [
        # Supõe que o Sinesp conta feminicídio à parte do homicídio doloso.
        ("CVLI (total)", ["Homicídio doloso", "Feminicídio",
                          "Roubo seguido de morte (latrocínio)",
                          "Lesão corporal seguida de morte"]),
        "Homicídio doloso",
        "Feminicídio",
        "Roubo seguido de morte (latrocínio)",
        "Lesão corporal seguida de morte",
    ],
    "Tentativas": [
        "Tentativa de homicídio",
        "Tentativa de feminicídio",
    ],
    "Violência sexual": [
        ("Estupro (total)", ["Estupro", "Estupro de vulnerável"]),
        "Estupro",
        "Estupro de vulnerável",
    ],
    "Crimes contra o patrimônio": [
        ("Roubo e furto de veículo", ["Roubo de veículo", "Furto de veículo"]),
        "Roubo de veículo",
        "Furto de veículo",
        "Roubo de carga",
        "Roubo a instituição financeira",
    ],
    "Tráfico de drogas": [
        "Tráfico de drogas",
    ],
    "Armas de fogo": [
        "Arma de Fogo Apreendida",
    ],
    "Apreensão de drogas": [
        ("Apreensão de drogas (cocaína + maconha)", ["Apreensão de Cocaína",
                                                     "Apreensão de Maconha"]),
        "Apreensão de Cocaína",
        "Apreensão de Maconha",
    ],
}

# Tolerância do simplify (graus) nos polígonos das RAs: o mapa folium vai
# inteiro para o navegador a cada troca de filtro, e a malha dos setores
# dissolvida tem vértices demais para a escala do DF.
_SIMPLIFICACAO_RA = 0.0002


# ----------------------------------------------------------------------------
# MJ / Sinesp
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class BaseMJ:
    fatos: pl.DataFrame
    """Uma linha por evento × abrangência × município × ano × mês (valor ≠ 0)."""
    municipios: pl.DataFrame
    """Município e UF (uma linha por município)."""
    ufs: pl.DataFrame
    """UF e região (uma linha por UF)."""
    pop_municipio: pl.DataFrame
    """`codigo_municipio_dv`, `ano`, `populacao`: população do município no ano."""
    pop_uf: pl.DataFrame
    """`sigla_uf`, `ano`, `populacao`: soma dos municípios no ano."""
    fonte_populacao: dict[int, str]
    """Ano → de onde vem a população daquele ano (estimativa, Censo...)."""
    ultimo_mes: dict[int, int]
    """Ano → último mês com dados (12, menos no ano corrente)."""
    unidade: dict[str, str]
    """Evento → o que o valor conta: vítimas, ocorrências ou kg."""
    municipal: set[str]
    """Eventos que o BancoVDE detalha por município; os demais só por UF."""
    categorias: dict[str, list[str]]
    """Categoria → eventos e grupos do painel, na ordem do seletor."""
    grupos: dict[str, list[str]]
    """Grupo → eventos da fonte que ele soma."""

    @property
    def anos(self) -> list[int]:
        return sorted(self.ultimo_mes)


def _unidade_por_evento(mj: pl.LazyFrame) -> dict[str, str]:
    """O BancoVDE guarda o valor em colunas diferentes conforme o evento: vítimas
    em `total_vitima`, apreensões de droga em `total_peso` (kg) e o resto em
    `total`. Em cada evento só uma delas é preenchida."""
    somas = mj.group_by("evento").agg(
        pl.col("total").sum(), pl.col("total_vitima").sum(), pl.col("total_peso").sum()
    ).collect()
    unidade = {}
    for r in somas.to_dicts():
        if r["total_peso"]:
            unidade[r["evento"]] = "kg"
        elif r["total_vitima"]:
            unidade[r["evento"]] = "vítimas"
        else:
            unidade[r["evento"]] = "ocorrências"
    return unidade


def carregar_mj() -> BaseMJ:
    """Lê o BancoVDE de todos os anos (`scripts/consolidar_mj.py`)."""
    mj = pl.scan_parquet(MJ_CENSO_GEO_PARQUET).with_columns(
        pl.col("data_referencia").dt.month().cast(pl.Int8).alias("mes")
    )

    fatos = (
        mj.with_columns(
            (pl.col("total") + pl.col("total_vitima") + pl.col("total_peso")).alias("valor")
        )
        # Agente, arma e faixa etária somem aqui: o painel não os filtra.
        .group_by("evento", "abrangencia", "sigla_uf", "codigo_municipio_dv", "ano", "mes")
        .agg(pl.col("valor", "feminino", "masculino", "nao_informado").sum())
        .filter(pl.col("valor") != 0)
        .collect()
    )

    com_municipio = mj.filter(pl.col("codigo_municipio_dv").is_not_null())
    pop_municipio = (
        com_municipio.select("codigo_municipio_dv", "ano", "populacao", "fonte_populacao")
        .unique(["codigo_municipio_dv", "ano"])
        .collect()
    )
    municipios = (
        com_municipio.select("codigo_municipio_dv", "nome_municipio", "sigla_uf")
        .unique("codigo_municipio_dv")
        .collect()
    )
    pop_uf = (
        pop_municipio.join(municipios.select("codigo_municipio_dv", "sigla_uf"),
                           on="codigo_municipio_dv")
        .group_by("sigla_uf", "ano").agg(pl.col("populacao").sum())
    )
    fonte_populacao = dict(
        pop_municipio.group_by("ano").agg(pl.col("fonte_populacao").mode().first()).rows()
    )
    ufs = (
        mj.select("cd_uf", "sigla_uf", "nome_uf", "nome_regiao")
        .unique("sigla_uf")
        .collect()
        .sort("nome_uf")
    )
    ultimo_mes = dict(mj.group_by("ano").agg(pl.col("mes").max()).collect().rows())
    municipal = set(com_municipio.select(pl.col("evento").unique()).collect()["evento"])
    unidade = _unidade_por_evento(mj)

    categorias = {
        cat: [it if isinstance(it, str) else it[0] for it in itens]
        for cat, itens in CATEGORIAS_MJ.items()
    }
    grupos = {it[0]: it[1] for itens in CATEGORIAS_MJ.values()
              for it in itens if not isinstance(it, str)}
    simples = {it for itens in CATEGORIAS_MJ.values() for it in itens if isinstance(it, str)}
    faltando = (simples | {m for ms in grupos.values() for m in ms}) - set(unidade)
    if faltando:
        raise ValueError(f"Eventos de CATEGORIAS_MJ ausentes do BancoVDE: {sorted(faltando)}")
    for nome, membros in grupos.items():
        unidades = {unidade[m] for m in membros}
        if len(unidades) > 1:
            raise ValueError(f"O grupo {nome!r} mistura unidades: {sorted(unidades)}")
        unidade[nome] = unidades.pop()
        # O mapa municipal só vale se todos os membros vêm por município.
        if all(m in municipal for m in membros):
            municipal.add(nome)

    chaves = ["evento", "abrangencia", "sigla_uf", "codigo_municipio_dv", "ano", "mes"]
    fatos = pl.concat([
        fatos.filter(pl.col("evento").is_in(list(simples))),
        *(
            fatos.filter(pl.col("evento").is_in(membros))
            .with_columns(pl.lit(nome).alias("evento"))
            .group_by(chaves)
            .agg(pl.col("valor", "feminino", "masculino", "nao_informado").sum())
            .select(fatos.columns)
            for nome, membros in grupos.items()
        ),
    ])
    painel = simples | set(grupos)
    return BaseMJ(
        fatos=fatos,
        municipios=municipios,
        ufs=ufs,
        pop_municipio=pop_municipio.drop("fonte_populacao"),
        pop_uf=pop_uf,
        fonte_populacao=fonte_populacao,
        ultimo_mes=ultimo_mes,
        unidade={ev: u for ev, u in unidade.items() if ev in painel},
        municipal=municipal & painel,
        categorias=categorias,
        grupos=grupos,
    )


# ----------------------------------------------------------------------------
# SSP-DF
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class BaseSSP:
    anual: pl.DataFrame
    """`cd_subdist`, `ano`, `natureza`, `total`: o total do ano no balanço da RA."""
    mensal: pl.DataFrame
    """`cd_subdist`, `ano`, `natureza`, `mes`, `valor`; `valor` nulo quando o
    balanço só traz o total do ano. Só os meses já fechados de cada ano."""
    ras: pl.DataFrame
    """RA (nome mais recente), população do Censo 2022 e delegacias."""
    pop: pl.DataFrame
    """`cd_subdist`, `ano`, `populacao`: só as RAs/anos que têm balanço."""
    ausentes: pl.DataFrame
    """`cd_subdist`, `ano` das RAs que já existiam mas não têm balanço válido."""
    criacao: dict[str, int]
    """RA desmembrada → primeiro ano com balanço próprio."""
    fonte_populacao: dict[int, str]
    ultimo_mes: dict[int, int]
    """Ano → último mês com dados (12, menos no ano corrente)."""
    anos_natureza: dict[str, set[int]]
    """Natureza → anos em que ela aparece nos balanços das RAs."""
    grupos: list[dict]
    """Naturezas por eixo, na ordem da fonte (`eixo`, `naturezas`, `fora_taxa`)."""
    padrao: list[str]
    """Naturezas de vitimização: todas menos as de produtividade policial."""
    gdf_ra: gpd.GeoDataFrame
    delegacias: gpd.GeoDataFrame
    centroides: dict[str, dict]

    @property
    def anos(self) -> list[int]:
        return sorted(self.ultimo_mes)


def carregar_ssp() -> BaseSSP:
    """Lê a série da SSP-DF por RA (`scripts/consolidar_ssp.py`). A linha do DF
    inteiro fica de fora: o balanço do DF separa naturezas (feminicídio, estupro
    de vulnerável) que o das RAs não separa, então os totais do DF no painel são
    a soma das RAs."""
    ssp = pl.read_parquet(SSP_RA_GEO_PARQUET).filter(
        (pl.col("ra_numero") != 0) & (pl.col("contagem") == "OCORRÊNCIA")
    )
    # Último mês com algum valor em cada ano: os seguintes ainda não fecharam.
    ultimo_mes = {
        ano: max(i + 1 for i, m in enumerate(MESES) if g[m].is_not_null().any())
        for (ano,), g in ssp.group_by("ano")
    }
    anual = ssp.select("cd_subdist", "ano", "natureza", "total")
    mensal = (
        ssp.unpivot(MESES, index=["cd_subdist", "ano", "natureza"],
                    variable_name="mes", value_name="valor")
        .with_columns(pl.col("mes").replace_strict({m: i + 1 for i, m in enumerate(MESES)},
                                                   return_dtype=pl.Int8))
        .filter(pl.col("mes") <= pl.col("ano").replace_strict(ultimo_mes))
    )

    ras = (
        ssp.sort("ano", descending=True)
        .select("cd_subdist", "ra", "populacao_censo", "n_delegacias", "delegacias")
        .unique("cd_subdist", keep="first")
        .with_columns(pl.col("n_delegacias").fill_null(0))
        .sort("ra")
    )
    pop = ssp.select("cd_subdist", "ano", "populacao").unique(["cd_subdist", "ano"])
    criacao = {filha: ano for filha, (_, ano) in RAS_DESMEMBRADAS.items()}
    ausentes = (
        ras.select("cd_subdist")
        .join(pl.DataFrame({"ano": sorted(ultimo_mes)}), how="cross")
        .join(pop, on=["cd_subdist", "ano"], how="anti")
        .filter(pl.col("ano") >= pl.col("cd_subdist").replace_strict(criacao, default=0))
        .sort("ano", "cd_subdist")
    )
    fonte_populacao = dict(
        ssp.group_by("ano").agg(pl.col("fonte_populacao").first()).rows()
    )
    anos_natureza = {
        n: set(anos) for n, anos in ssp.group_by("natureza").agg(pl.col("ano").unique()).rows()
    }

    grupos = [
        {
            "eixo": eixo,
            "naturezas": sorted(nats),
            "fora_taxa": eixo.startswith(EIXO_FORA_DA_TAXA),
        }
        for eixo, nats in sorted(
            ssp.group_by("eixo").agg(pl.col("natureza").unique()).rows()
        )
    ]
    padrao = [n for g in grupos if not g["fora_taxa"] for n in g["naturezas"]]

    gdf = carregar_setores()
    gdf_ra = dissolver_por_ra(gdf)
    # Centroides antes do simplify, para não deslocar as bolhas.
    centroides = centroides_por_ra(gdf_ra)
    gdf_ra["geometry"] = gdf_ra.geometry.simplify(_SIMPLIFICACAO_RA, preserve_topology=True)
    return BaseSSP(
        anual=anual,
        mensal=mensal,
        ras=ras,
        pop=pop,
        ausentes=ausentes,
        criacao=criacao,
        fonte_populacao=fonte_populacao,
        ultimo_mes=ultimo_mes,
        anos_natureza=anos_natureza,
        grupos=grupos,
        padrao=padrao,
        gdf_ra=gdf_ra,
        delegacias=delegacias_com_subdistrito(gdf),
        centroides=centroides,
    )


def ssp_por_ra_ano(ssp: BaseSSP, naturezas: list[str], mes_ini: int, mes_fim: int) -> pl.DataFrame:
    """Ocorrências das `naturezas` nos meses `mes_ini`..`mes_fim` de cada ano, por
    RA e ano: `cd_subdist`, `ano`, `total`, `populacao`. Só as RAs/anos com
    balanço. `total` é nulo quando o período não é o ano inteiro e o balanço da
    RA não detalha esses meses."""
    ano_inteiro = (mes_ini, mes_fim) == (1, 12)
    if ano_inteiro:
        soma = ssp.anual.filter(pl.col("natureza").is_in(naturezas))
        sem_detalhe = None
    else:
        meses = ssp.mensal.filter(pl.col("mes").is_between(mes_ini, mes_fim))
        soma = meses.filter(pl.col("natureza").is_in(naturezas)).rename({"valor": "total"})
        sem_detalhe = (meses.filter(pl.col("valor").is_null())
                       .select("cd_subdist", "ano").unique()
                       .with_columns(pl.lit(True).alias("sem_detalhe")))
    df = (
        ssp.pop.join(soma.group_by("cd_subdist", "ano").agg(pl.col("total").sum()),
                     on=["cd_subdist", "ano"], how="left")
        # RA/ano com balanço e sem nenhuma das naturezas no período: zero.
        .with_columns(pl.col("total").fill_null(0))
    )
    if sem_detalhe is not None:
        df = (df.join(sem_detalhe, on=["cd_subdist", "ano"], how="left")
              .with_columns(pl.when(pl.col("sem_detalhe")).then(None)
                            .otherwise(pl.col("total")).alias("total"))
              .drop("sem_detalhe"))
    return df
