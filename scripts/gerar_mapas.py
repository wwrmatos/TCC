"""Gera os dois mapas HTML em `output/` a partir dos dados brutos em `data/`.

Uso:
    uv run python scripts/gerar_mapas.py [--abrir]
"""

import argparse
import webbrowser

from tcc.geo import (
    carregar_setores,
    centroides_por_ra,
    delegacias_com_subdistrito,
    dissolver_por_ra,
    juntar_ssp_delegacias,
)
from tcc.mapas import mapa_bolhas, mapa_coropletico, preparar_dados
from tcc.paths import OUTPUT_DIR
from tcc.ssp_df import carregar_dados_ssp_df


def main(abrir: bool = False) -> None:
    ssp = carregar_dados_ssp_df()
    print(f"SSP-DF: {ssp.shape[0]} linhas")

    gdf = carregar_setores()
    gdf_ra = dissolver_por_ra(gdf)
    dp_joined = delegacias_com_subdistrito(gdf)
    print(f"Delegacias: {len(dp_joined)}")

    ssp_dp = juntar_ssp_delegacias(ssp, dp_joined)
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
