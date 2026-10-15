"""Junta o BancoVDE (MJ/Sinesp) de vários anos ao Censo 2022, às dimensões
geográficas e à população estimada do ano, num único arquivo
Parquet: `data/processed/mj_censo_geo.parquet`.

Faz, para cada ano, o mesmo que `juntar_mj_censo.py` + `juntar_dimensoes.py`
fazem para um ano só, e acrescenta:

- `ano`: o ano da `data_referencia`;
- `populacao` e `fonte_populacao`: população do município naquele ano (ver
  `tcc.populacao.populacao_municipal_por_ano`). As colunas `v0001`… continuam
  sendo as do Censo 2022.

Antes, gere as estimativas com `scripts/baixar_populacao_ibge.py`.

Uso:
    uv run python scripts/consolidar_mj.py [--ano-inicial 2016] [--ano-final 2026]
"""

import argparse
import shutil

import polars as pl

from tcc.dimensoes import (
    carregar_dimensao_geografica,
    completar_uf_por_sigla,
    juntar_dimensoes,
    sem_dimensao,
)
from tcc.mj import (
    COLUNAS_MJ_GEO,
    carregar_banco_vde,
    carregar_censo_por_municipio,
    juntar_mj_censo,
    municipios_sem_par,
    organizar_mj_geo,
)
from tcc.paths import MJ_CENSO_GEO_PARQUET, MJ_DIR
from tcc.populacao import populacao_municipal_por_ano

# Colunas do MJ/Censo com ano e população do ano logo depois das do Censo.
_COLUNAS = [
    *COLUNAS_MJ_GEO[: COLUNAS_MJ_GEO.index("evento")],
    "ano", "populacao", "fonte_populacao",
    *COLUNAS_MJ_GEO[COLUNAS_MJ_GEO.index("evento"):],
]


def main(ano_inicial: int, ano_final: int) -> None:
    anos = [a for a in range(ano_inicial, ano_final + 1) if (MJ_DIR / f"BancoVDE {a}.xlsx").exists()]
    dimensao = carregar_dimensao_geografica()
    censo = carregar_censo_por_municipio()
    populacao = populacao_municipal_por_ano(anos, censo)

    # Um Parquet por ano numa pasta temporária, juntados no fim por streaming,
    # para não ter todos os anos na memória.
    temp = MJ_CENSO_GEO_PARQUET.with_suffix(".tmp")
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True)
    total = 0
    for ano in anos:
        mj = juntar_mj_censo(carregar_banco_vde(ano), censo)
        sem_par = municipios_sem_par(mj)

        geo = completar_uf_por_sigla(
            juntar_dimensoes(mj, "cd_mun", coluna_sigla="uf", dimensao=dimensao),
            "sigla_uf",
        )
        sem_dim = sem_dimensao(geo)

        df = (
            organizar_mj_geo(geo)
            .with_columns(
                pl.col("data_referencia").cast(pl.String).str.slice(0, 4).cast(pl.Int64).alias("ano"),
                pl.col("codigo_municipio").cast(pl.String),
            )
            .join(populacao, on=["codigo_municipio", "ano"], how="left")
            .select(_COLUNAS)
        )
        df.write_parquet(temp / f"{ano}.parquet")
        total += df.height

        # Linhas com município (não "NÃO INFORMADO") que ficaram sem população.
        sem_pop = (
            df.filter(pl.col("codigo_municipio").is_not_null() & pl.col("populacao").is_null())
            .select("sigla_uf", "nome_municipio").unique()
        )
        outros_anos = df.filter(pl.col("ano") != ano).height
        print(
            f"{ano}: {df.height} linhas | "
            f"sem par no Censo: {sem_par.height} | sem dimensão: {sem_dim.height} | "
            f"município sem população: {sem_pop.height}"
            + (f" | {outros_anos} linhas com data de outro ano" if outros_anos else "")
        )
        if sem_pop.height:
            print("   ", ", ".join(f"{r['nome_municipio']}/{r['sigla_uf']}" for r in sem_pop.to_dicts()))

    pl.scan_parquet(temp / "*.parquet").sink_parquet(MJ_CENSO_GEO_PARQUET)
    shutil.rmtree(temp)
    print(f"{total} linhas salvas em: {MJ_CENSO_GEO_PARQUET}")
    print("Fonte da população por ano:")
    print(populacao.group_by("ano", "fonte_populacao").len().sort("ano"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ano-inicial", type=int, default=2016)
    parser.add_argument("--ano-final", type=int, default=2026)
    main(**vars(parser.parse_args()))
