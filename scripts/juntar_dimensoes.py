"""Junta as bases finais (SSP-DF e MJ/Censo) às dimensões município, unidade
federação e região, e salva em `data/processed/<base>_geo.csv`.

Uso:
    uv run python scripts/juntar_dimensoes.py [--ano 2025]
"""

import argparse

import polars as pl

from tcc.dimensoes import (
    carregar_dimensao_geografica,
    completar_uf_por_sigla,
    juntar_dimensoes,
    sem_dimensao,
)
from tcc.mj import organizar_mj_geo
from tcc.paths import PROCESSED_DIR


# Mesma lógica para a SSP-DF: município e dimensões, dados do subdistrito (RA),
# ocorrências e, por fim, as coordenadas.
_COLUNAS_SSP_GEO = [
    "codigo_municipio_dv", "codigo_municipio", "nome_municipio", "municipio_capital",
    "cd_uf", "sigla_uf", "nome_uf", "cd_regiao", "nome_regiao",
    "cd_subdist", "ra", "populacao", "n_delegacias", "delegacias",
    "ano", "eixo", "natureza", "total",
    "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez",
    "longitude", "latitude",
]


def _avisar_sem_par(nome: str, joined: pl.DataFrame) -> None:
    sem_par = sem_dimensao(joined)
    if sem_par.height:
        codigos = sem_par["codigo_municipio_dv"].to_list()
        print(f"  {nome}: {len(codigos)} código(s) sem par na dimensão: {codigos}")


def _salvar(nome: str, df: pl.DataFrame) -> None:
    destino = PROCESSED_DIR / f"{nome}_geo.csv"
    df.write_csv(destino)
    print(f"  {df.height} linhas × {df.width} colunas salvas em: {destino}")


def main(ano: int) -> None:
    dimensao = carregar_dimensao_geografica()
    print(f"Dimensão geográfica: {dimensao.height} municípios")

    # A SSP-DF não tem coluna de município (é tudo Brasília): o código do IBGE
    # com DV são os 7 primeiros dígitos do código do subdistrito.
    ssp = pl.read_csv(PROCESSED_DIR / "ssp_dp.csv", infer_schema_length=None).with_columns(
        pl.col("cd_subdist").cast(pl.String).str.slice(0, 7).alias("codigo_municipio_dv")
    )
    ssp_geo = juntar_dimensoes(ssp, "codigo_municipio_dv", dimensao=dimensao)
    print("SSP-DF:")
    _avisar_sem_par("ssp_dp", ssp_geo)
    _salvar("ssp_dp", ssp_geo.select(_COLUNAS_SSP_GEO))

    mj = pl.read_csv(
        PROCESSED_DIR / f"mj_censo_{ano}.csv",
        infer_schema_length=None,
        schema_overrides={"cd_mun": pl.String},
    )
    mj_geo = completar_uf_por_sigla(
        juntar_dimensoes(mj, "cd_mun", coluna_sigla="uf", dimensao=dimensao),
        "sigla_uf",
    )
    print(f"MJ/Censo {ano}:")
    _avisar_sem_par(f"mj_censo_{ano}", mj_geo)
    _salvar(f"mj_censo_{ano}", organizar_mj_geo(mj_geo))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, default=2025)
    main(**vars(parser.parse_args()))
