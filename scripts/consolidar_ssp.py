"""Junta a série da SSP-DF por RA (2015–2026) às dimensões geográficas, às
delegacias e à população da RA no ano, num único arquivo Parquet:
`data/processed/ssp_ra_geo.parquet`.

Faz, para todos os anos de `ssp_df_ra_consolidado.csv` (gerado por
`scripts/consolidar_ssp_ra.py`), o mesmo que `gerar_mapas.py` +
`juntar_dimensoes.py` fazem para a planilha de 2025, e acrescenta:

- `populacao` e `fonte_populacao`: população da RA naquele ano (ver
  `tcc.populacao.populacao_ra_por_ano`). `populacao_censo` continua sendo a do
  Censo 2022;
- a linha do DF inteiro (`ra_numero` 0, sem `cd_subdist`), com a população do
  DF no ano, e as colunas `contagem`, `fonte` e `arquivo` do consolidado;
- os feminicídios por RA dos estudos da CTMHF (2021–2026)
  (`ssp_df_feminicidio_ra.csv`, de `scripts/extrair_feminicidio_ctmhf.py`), no
  eixo próprio `5. FEMINICÍDIO - CTMHF`, só até o último mês dos balanços de
  cada ano, para o ano corrente cobrir os mesmos meses das outras naturezas.

Antes, gere as estimativas com `scripts/baixar_populacao_ibge.py`.

Uso:
    uv run python scripts/consolidar_ssp.py
"""

import argparse

import polars as pl

from tcc.dimensoes import CODIGO_MUNICIPIO_DF, carregar_dimensao_geografica, juntar_dimensoes, sem_dimensao
from tcc.geo import agregar_delegacias_por_ra, carregar_setores, delegacias_com_subdistrito
from tcc.mj import carregar_censo_por_municipio
from tcc.paths import (
    MJ_CENSO_GEO_PARQUET,
    SSP_FEMINICIDIO_RA_CSV,
    SSP_RA_CONSOLIDADO_CSV,
    SSP_RA_GEO_PARQUET,
)
from tcc.populacao import carregar_populacao, populacao_municipal_por_ano, populacao_ra_por_ano
from tcc.ssp_df import RA_NUMERO_PARA_CD_SUBDIST

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

# Casos de diferença tolerados, num ano, entre o feminicídio por RA da CTMHF e o
# do MJ no DF (soma das diferenças de cada mês). Diferenças pequenas vêm de casos
# que as fontes datam em meses diferentes; mais que isso, algo está errado. O
# mesmo limite está em `scripts/extrair_feminicidio_ctmhf.py`.
TOLERANCIA_FEMINICIDIO = 3

# As de `ssp_dp_geo.csv` (juntar_dimensoes.py), com o número da RA, a população
# do ano ao lado da do Censo, o tipo de contagem e a procedência de cada linha.
_COLUNAS = [
    "codigo_municipio_dv", "codigo_municipio", "nome_municipio", "municipio_capital",
    "cd_uf", "sigla_uf", "nome_uf", "cd_regiao", "nome_regiao",
    "ra_numero", "cd_subdist", "ra", "populacao_censo", "n_delegacias", "delegacias",
    "ano", "populacao", "fonte_populacao",
    "eixo", "natureza", "contagem", "total", *MESES,
    "fonte", "arquivo",
    "longitude", "latitude",
]


def _feminicidio_ctmhf(balancos: pl.DataFrame) -> pl.DataFrame:
    """Feminicídios da CTMHF por RA, cortados no último mês com dado nos
    balanços de cada ano; o total é refeito com os meses que ficam."""
    fem = pl.read_csv(SSP_FEMINICIDIO_RA_CSV, infer_schema_length=None)
    ultimo_mes = {
        ano: max(i + 1 for i, m in enumerate(MESES) if g[m].is_not_null().any())
        for (ano,), g in balancos.group_by("ano")
    }
    sem_balanco = sorted(set(fem["ano"]) - set(ultimo_mes))
    if sem_balanco:
        raise ValueError(f"Feminicídios da CTMHF em anos sem balanço: {sem_balanco}")
    corte = pl.col("ano").replace_strict(ultimo_mes)
    fem = fem.with_columns(
        pl.when(pl.lit(i + 1) <= corte).then(pl.col(m)).otherwise(None).alias(m)
        for i, m in enumerate(MESES)
    )
    fem = fem.with_columns(pl.sum_horizontal(MESES).alias("total")).select(balancos.columns)

    _conferir_com_mj(fem)
    return fem


def _conferir_com_mj(fem: pl.DataFrame) -> None:
    """A soma das RAs tem de ficar perto do feminicídio do DF no MJ/Sinesp, mês a
    mês (até :data:`TOLERANCIA_FEMINICIDIO` casos no ano): senão os painéis do DF
    e do MJ divergem."""
    mj = (
        pl.scan_parquet(MJ_CENSO_GEO_PARQUET)
        .filter((pl.col("sigla_uf") == "DF") & (pl.col("evento") == "Feminicídio")
                & (pl.col("abrangencia") == "Estadual"))
        .group_by("ano", pl.col("data_referencia").dt.month().alias("mes"))
        .agg(pl.col("total_vitima").sum())
        .collect()
    )
    mj = {(a, m): v for a, m, v in mj.rows()}
    for ano, *meses in fem.group_by("ano").agg(pl.col(MESES).sum()).sort("ano").rows():
        dif = sum(abs(v - mj.get((ano, m), 0))
                  for m, v in enumerate(meses, start=1) if v is not None)
        if dif > TOLERANCIA_FEMINICIDIO:
            raise ValueError(f"Feminicídio {ano}: a soma das RAs difere do MJ em {dif:.0f} casos, "
                             "mês a mês. Rode scripts/extrair_feminicidio_ctmhf.py.")
        if dif:
            print(f"Feminicídio {ano}: a soma das RAs difere do MJ em {dif:.0f} caso(s), mês a mês")


def main() -> None:
    balancos = pl.read_csv(SSP_RA_CONSOLIDADO_CSV, infer_schema_length=None)
    ssp = pl.concat([balancos, _feminicidio_ctmhf(balancos)], how="vertical_relaxed").with_columns(
        pl.col("ra_numero").replace_strict(RA_NUMERO_PARA_CD_SUBDIST, default=None,
                                           return_dtype=pl.String).alias("cd_subdist"),
        # O rótulo do eixo vem com espaçamento irregular nas planilhas.
        pl.col("eixo").str.replace_all(r"\s+", " ").str.strip_chars(),
        pl.lit(CODIGO_MUNICIPIO_DF).alias("codigo_municipio_dv"),
    )
    anos = sorted(ssp["ano"].unique().to_list())
    sem_subdist = ssp.filter(pl.col("cd_subdist").is_null() & (pl.col("ra_numero") != 0))
    if sem_subdist.height:
        raise ValueError(f"RAs sem subdistrito: {sem_subdist['ra'].unique().to_list()}")

    # População: a do DF no ano (estimativa do IBGE, como no MJ) e a das RAs,
    # rateada pela participação no Censo 2022.
    pop_df = (
        populacao_municipal_por_ano(anos, carregar_censo_por_municipio())
        .filter(pl.col("codigo_municipio") == CODIGO_MUNICIPIO_DF[:6])
        .drop("codigo_municipio")
    )
    faltam = sorted(set(anos) - set(pop_df["ano"]))
    if faltam:
        raise ValueError(f"Sem população do DF em {faltam}: rode scripts/baixar_populacao_ibge.py")
    pop_ra = populacao_ra_por_ano(pop_df)

    gdf = carregar_setores()
    delegacias = agregar_delegacias_por_ra(delegacias_com_subdistrito(gdf))

    df = (
        ssp.join(pop_ra, on=["cd_subdist", "ano"], how="left")
        .join(pop_df, on="ano", how="left", suffix="_df")
        .with_columns(
            pl.coalesce("populacao", "populacao_df").alias("populacao"),
            pl.coalesce("fonte_populacao", "fonte_populacao_df").alias("fonte_populacao"),
        )
        .join(carregar_populacao().rename({"populacao": "populacao_censo"}),
              on="cd_subdist", how="left")
        .join(delegacias, on="cd_subdist", how="left")
    )
    # Para a linha do DF: o Censo e as delegacias do DF inteiro.
    df = df.with_columns(
        pl.when(pl.col("ra_numero") == 0)
        .then(pl.lit(carregar_populacao()["populacao"].sum()))
        .otherwise(pl.col("populacao_censo")).alias("populacao_censo"),
        pl.when(pl.col("ra_numero") == 0)
        .then(pl.lit(delegacias["n_delegacias"].sum()))
        .otherwise(pl.col("n_delegacias")).alias("n_delegacias"),
    )
    geo = juntar_dimensoes(df, "codigo_municipio_dv", dimensao=carregar_dimensao_geografica())
    sem_dim = sem_dimensao(geo)
    if sem_dim.height:
        raise ValueError(f"Código de município sem dimensão: {sem_dim.to_series().to_list()}")
    geo = geo.select(_COLUNAS).sort("ano", "ra_numero", "eixo", "natureza", "contagem")
    geo.write_parquet(SSP_RA_GEO_PARQUET)

    for ano in anos:
        a = geo.filter(pl.col("ano") == ano)
        ras = a.filter(pl.col("ra_numero") != 0)
        pop_ras = ras.unique("cd_subdist")["populacao"].sum()
        pop_total = a.filter(pl.col("ra_numero") == 0)["populacao"].first()
        print(
            f"{ano}: {a.height} linhas | {ras['ra_numero'].n_unique()} RAs | "
            f"população {pop_total} (soma das RAs {pop_ras}) — {a['fonte_populacao'].first()}"
        )
    sem_pop = geo.filter(pl.col("populacao").is_null())
    if sem_pop.height:
        print(f"Sem população: {sem_pop.select('ra', 'ano').unique().rows()}")
    print(f"{geo.height} linhas × {geo.width} colunas salvas em: {SSP_RA_GEO_PARQUET}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args()
    main()
