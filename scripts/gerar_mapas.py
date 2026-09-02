"""Gera os dois mapas HTML em `output/` a partir dos dados brutos em `data/`.

Uso:
    uv run python scripts/gerar_mapas.py [--abrir]
"""

import argparse
import webbrowser

import polars as pl

from tcc.geo import (
    carregar_setores,
    centroides_por_ra,
    delegacias_com_subdistrito,
    dissolver_por_ra,
    juntar_ssp_delegacias,
)
from tcc.mapas import mapa_bolhas, mapa_coropletico, preparar_dados
from tcc.paths import OUTPUT_DIR
from tcc.populacao import juntar_populacao
from tcc.ssp_df import carregar_dados_ssp_df


def main(abrir: bool = False) -> None:
    ssp = carregar_dados_ssp_df()
    anos = sorted(ssp["ano"].drop_nulls().unique().to_list())
    print(f"SSP-DF: {ssp.shape[0]} linhas | ano(s): {', '.join(anos)}")
    if len(anos) > 1:
        # Uma planilha de ano diferente (ou de ano corrente, incompleta) torna a
        # taxa incomparável entre RAs: períodos distintos no mesmo denominador.
        por_ano = ssp.group_by("ano").agg(pl.col("ra").unique().alias("ras"))
        print("AVISO: as planilhas não cobrem o mesmo período —")
        for r in sorted(por_ano.to_dicts(), key=lambda r: r["ano"]):
            print(f"  {r['ano']}: {', '.join(sorted(r['ras']))}")

    gdf = carregar_setores()
    gdf_ra = dissolver_por_ra(gdf)
    dp_joined = delegacias_com_subdistrito(gdf)
    print(f"Delegacias: {len(dp_joined)}")

    ssp_dp = juntar_populacao(juntar_ssp_delegacias(ssp, dp_joined))
    sem_pop = ssp_dp.filter(ssp_dp["populacao"].is_null())["ra"].unique().to_list()
    if sem_pop:
        print(f"Sem população no CSV (ficam cinza no mapa): {', '.join(sorted(sem_pop))}")

    dados = preparar_dados(ssp_dp)

    OUTPUT_DIR.mkdir(exist_ok=True)
    mapas = {
        "mapa_ssp_delegacias.html": mapa_coropletico(gdf, gdf_ra, dp_joined, dados),
        "mapa_bolhas_delegacias.html": mapa_bolhas(
            gdf, gdf_ra, dp_joined, dados, centroides_por_ra(gdf_ra)
        ),
    }

    for nome, mapa in mapas.items():
        destino = OUTPUT_DIR / nome
        mapa.save(str(destino))
        print(f"Mapa salvo em: {destino}")
        if abrir:
            webbrowser.open(destino.as_uri())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--abrir", action="store_true", help="abre os mapas no navegador ao terminar"
    )
    main(**vars(parser.parse_args()))
