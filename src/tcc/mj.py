"""Ocorrências do BancoVDE (SINESP/MJ) cruzadas com o Censo 2022 por município.

O BancoVDE identifica o município só pelo nome (em caixa alta) e pela sigla da
UF; o Censo traz `cd_mun` (código IBGE), nome e UF por extenso. Como há dezenas
de nomes repetidos entre estados (cinco "São Domingos", cinco "Bom Jesus"...),
a junção é feita pelo par (UF, nome normalizado), nunca só pelo nome.
"""

from pathlib import Path

import polars as pl

from tcc.paths import CENSO_IBGE_CSV, MJ_DIR
from tcc.ssp_df import _sem_acento

CD_UF_PARA_SIGLA: dict[str, str] = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
    "28": "SE", "29": "BA",
    "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
    "41": "PR", "42": "SC", "43": "RS",
    "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}

# Grafias do BancoVDE que divergem do IBGE (chave: (UF, nome normalizado)).
# "NÃO INFORMADO" não tem par e fica com `cd_mun` nulo de propósito.
DE_PARA_MUNICIPIO: dict[tuple[str, str], str] = {
    ("BA", "UNA"): "UNAS",
    ("MG", "BARAO DE MONTE ALTO"): "BARAO DO MONTE ALTO",
    ("MT", "SANTO ANTONIO DO LEVERGER"): "SANTO ANTONIO DE LEVERGER",
    ("SE", "GRACHO CARDOSO"): "GRACCHO CARDOSO",
}

# Variáveis do Censo somáveis por município (contagens). v0005 e v0006 são
# médias por setor e não podem ser somadas.
_VARS_SOMAVEIS = ["area_km2", "v0001", "v0002", "v0003", "v0004", "v0007"]


def _chave_municipio(col: str) -> pl.Expr:
    """Nome em caixa alta, sem acento e com espaços colapsados."""
    return (
        pl.col(col)
        .str.to_uppercase()
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
        .map_elements(_sem_acento, return_dtype=pl.String)
        .alias("chave_mun")
    )


_COLUNAS_NUMERICAS = ["feminino", "masculino", "nao_informado", "total_vitima", "total", "total_peso"]


def consolidar_duplicatas(mj: pl.DataFrame) -> pl.DataFrame:
    """Soma linhas repetidas da mesma chave (UF, município, evento, mês, ...).

    A nota do BancoVDE avisa que "Tentativa de Homicídio" e "Estupro" podem ter
    duas linhas no mesmo mês, a serem somadas. Na prática o caso maior é o DF:
    como só há um município, as 33 RAs aparecem como 33 linhas de "BRASÍLIA"
    para cada evento×mês, sem agregação.
    """
    dims = [c for c in mj.columns if c not in _COLUNAS_NUMERICAS]
    # feminino/masculino/nao_informado chegam como texto do read_excel.
    mj = mj.with_columns(
        pl.col(c).cast(pl.Float64, strict=False) for c in _COLUNAS_NUMERICAS
    )
    return mj.group_by(dims, maintain_order=True).agg(
        [pl.col(c).sum() for c in _COLUNAS_NUMERICAS]
    )


def carregar_banco_vde(ano: int) -> pl.DataFrame:
    """BancoVDE de um ano, já com as linhas duplicadas consolidadas."""
    return consolidar_duplicatas(
        pl.read_excel(MJ_DIR / f"BancoVDE {ano}.xlsx", infer_schema_length=None)
    )


def carregar_censo_por_municipio() -> pl.DataFrame:
    """Agrega os setores censitários por município (`cd_mun`)."""
    return (
        pl.scan_csv(CENSO_IBGE_CSV, schema_overrides={"cd_uf": pl.String, "cd_mun": pl.String})
        .group_by("cd_uf", "cd_mun", "nm_mun")
        .agg([pl.col(v).sum() for v in _VARS_SOMAVEIS])
        .with_columns(pl.col("cd_uf").replace_strict(CD_UF_PARA_SIGLA).alias("uf"))
        .collect()
    )


def juntar_mj_censo(mj: pl.DataFrame, censo: pl.DataFrame | None = None) -> pl.DataFrame:
    """Junta o BancoVDE ao Censo por (UF, município). Linhas sem par ficam com
    `cd_mun` nulo — use :func:`municipios_sem_par` para listá-las."""
    censo = carregar_censo_por_municipio() if censo is None else censo
    mj = mj.with_columns(_chave_municipio("municipio"))
    for (uf, de), para in DE_PARA_MUNICIPIO.items():
        mj = mj.with_columns(
            pl.when((pl.col("uf") == uf) & (pl.col("chave_mun") == de))
            .then(pl.lit(para))
            .otherwise(pl.col("chave_mun"))
            .alias("chave_mun")
        )
    return mj.join(
        censo.with_columns(_chave_municipio("nm_mun")).drop("cd_uf"),
        on=["uf", "chave_mun"],
        how="left",
    ).drop("chave_mun")


def municipios_sem_par(joined: pl.DataFrame) -> pl.DataFrame:
    return (
        joined.filter(pl.col("cd_mun").is_null())
        .select("uf", "municipio")
        .unique()
        .sort("uf", "municipio")
    )


# Ordem das colunas do MJ/Censo (a mesma do DDL em sql/ddl.sql): município e
# suas dimensões, variáveis do Censo, colunas do BancoVDE e, por fim, as
# coordenadas.
COLUNAS_MJ_GEO = [
    "codigo_municipio_dv", "codigo_municipio", "nome_municipio", "municipio_capital",
    "cd_uf", "sigla_uf", "nome_uf", "cd_regiao", "nome_regiao",
    "area_km2", "v0001", "v0002", "v0003", "v0004", "v0007",
    "evento", "data_referencia", "agente", "arma", "faixa_etaria", "abrangencia",
    "feminino", "masculino", "nao_informado", "total_vitima", "total", "total_peso",
    "longitude", "latitude",
]


def organizar_mj_geo(mj_geo: pl.DataFrame) -> pl.DataFrame:
    """Deixa um só nome de município e põe as colunas na ordem do DDL.

    `municipio` (BancoVDE, caixa alta) e `nm_mun` (Censo) repetem
    `nome_municipio` (IBGE). Fica o do IBGE; nas linhas sem par ("NÃO
    INFORMADO") ele herda o texto do BancoVDE.
    """
    return mj_geo.with_columns(
        pl.coalesce("nome_municipio", "municipio").alias("nome_municipio")
    ).select(COLUNAS_MJ_GEO)
