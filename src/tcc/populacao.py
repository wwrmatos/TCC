"""População residente por Região Administrativa (Censo 2022, IBGE)."""

import polars as pl

from tcc.paths import POPULACAO_CSV

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
