"""População residente por Região Administrativa (Censo 2022, IBGE) e
população municipal por ano (estimativas do IBGE)."""

import polars as pl

from tcc.paths import POPULACAO_CSV, POPULACAO_ESTIMADA_CSV

# Base da taxa. Não são 100 mil porque várias RAs têm população muito menor que
# isso (o SIA tem ~5 mil residentes) e a taxa por 100 mil ficaria inflada a ponto
# de perder sentido na leitura do mapa.
BASE_TAXA = 1_000


def carregar_populacao() -> pl.DataFrame:
    """`cd_subdist` → `populacao` residente."""
    return (
        pl.read_csv(POPULACAO_CSV, schema_overrides={"cd_subdist": pl.String})
        .select(
            pl.col("cd_subdist"),
            pl.col("sum").cast(pl.Int64).alias("populacao"),
        )
        .filter(pl.col("populacao") > 0)
    )


def juntar_populacao(df: pl.DataFrame) -> pl.DataFrame:
    """Acrescenta a população da RA a um DataFrame com `cd_subdist`."""
    return df.join(carregar_populacao(), on="cd_subdist", how="left")


def taxa(total: int | float | None, populacao: int | None) -> float | None:
    """Ocorrências por :data:`BASE_TAXA` habitantes."""
    if not populacao or total is None:
        return None
    return total * BASE_TAXA / populacao


# ---------------------------------------------------------------------------
# População municipal por ano (para as taxas do MJ/Sinesp)
# ---------------------------------------------------------------------------

# Anos sem estimativa municipal do IBGE, que usam a contagem do Censo 2022.
ANOS_COM_CENSO = (2022, 2023)


def populacao_municipal_por_ano(anos: list[int], censo: pl.DataFrame) -> pl.DataFrame:
    """População de cada município em cada ano: `codigo_municipio` (6 dígitos),
    `ano`, `populacao` e `fonte_populacao`.

    Usa a estimativa do IBGE do próprio ano (`data/ibge_populacao_estimada.csv`,
    gerado por `scripts/baixar_populacao_ibge.py`). O IBGE não publica estimativa
    municipal para 2022 nem para 2023, então os dois anos usam a contagem do
    Censo 2022 (`v0001` somado por município, vindo de `censo`, a saída de
    `tcc.mj.carregar_censo_por_municipio`).
    """
    estimada = pl.read_csv(
        POPULACAO_ESTIMADA_CSV, schema_overrides={"cod_municipio": pl.String}
    ).select(
        pl.col("cod_municipio").alias("codigo_municipio"),
        pl.col("ano").cast(pl.Int64),
        pl.col("populacao").cast(pl.Int64),
        pl.lit("estimativa IBGE").alias("fonte_populacao"),
    )
    do_censo = censo.select(
        pl.col("cd_mun").str.slice(0, 6).alias("codigo_municipio"),
        pl.lit(list(ANOS_COM_CENSO), pl.List(pl.Int64)).alias("ano"),
        pl.col("v0001").cast(pl.Int64).alias("populacao"),
        pl.lit("Censo 2022").alias("fonte_populacao"),
    ).explode("ano")
    return pl.concat([estimada, do_censo]).filter(pl.col("ano").is_in(anos))


# ---------------------------------------------------------------------------
# População por RA e ano (para as taxas da série da SSP-DF)
# ---------------------------------------------------------------------------

# RAs criadas a partir de outras, com o primeiro ano em que a SSP-DF publica o
# balanço delas. Antes disso, as ocorrências estão na RA de origem, e a
# população também deve estar: Arniqueira saiu de Águas Claras, e Sol
# Nascente/Pôr do Sol, de Ceilândia.
RAS_DESMEMBRADAS: dict[str, tuple[str, int]] = {
    "53001080532": ("53001080536", 2020),  # Arniqueira → Águas Claras
    "53001080531": ("53001080515", 2020),  # Sol Nascente/Pôr do Sol → Ceilândia
}


def populacao_ra_por_ano(populacao_df: pl.DataFrame) -> pl.DataFrame:
    """População de cada RA em cada ano: `cd_subdist`, `ano`, `populacao` e
    `fonte_populacao`.

    O IBGE não estima população abaixo do município, então a do DF no ano
    (`populacao_df`: `ano`, `populacao`, `fonte_populacao`, a linha de Brasília
    de :func:`populacao_municipal_por_ano`) é rateada pela participação de cada
    RA no Censo 2022. Supõe que essa participação não muda entre os anos; o
    total das RAs fecha com o do DF. Antes da criação de uma RA desmembrada
    (:data:`RAS_DESMEMBRADAS`), a população dela vai para a RA de origem.
    """
    censo = carregar_populacao()
    total_censo = censo["populacao"].sum()
    por_ano = (
        censo.join(populacao_df.rename({"populacao": "populacao_df"}), how="cross")
        .with_columns(
            (pl.col("populacao") / total_censo * pl.col("populacao_df")).alias("populacao"),
            # Em 2022 e 2023 o rateio devolve a própria contagem do Censo na RA.
            pl.when(pl.col("ano").is_in(ANOS_COM_CENSO)).then(pl.col("fonte_populacao"))
            .otherwise(pl.col("fonte_populacao")
                       + " do DF, rateada pela participação da RA no Censo 2022")
            .alias("fonte_populacao"),
        )
    )
    for filha, (mae, criacao) in RAS_DESMEMBRADAS.items():
        por_ano = por_ano.with_columns(
            pl.when((pl.col("cd_subdist") == filha) & (pl.col("ano") < criacao))
            .then(pl.lit(mae))
            .otherwise(pl.col("cd_subdist"))
            .alias("cd_subdist")
        )
    return (
        por_ano.group_by("cd_subdist", "ano")
        .agg(pl.col("populacao").sum().round().cast(pl.Int64), pl.col("fonte_populacao").first())
        .sort("ano", "cd_subdist")
    )
