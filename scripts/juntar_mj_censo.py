"""Junta o BancoVDE (MJ) de um ano ao Censo 2022 por município e salva em
`data/processed/mj_censo_<ano>.csv`.

Uso:
    uv run python scripts/juntar_mj_censo.py [--ano 2025]
"""

import argparse

from tcc.mj import carregar_banco_vde, juntar_mj_censo, municipios_sem_par
from tcc.paths import PROCESSED_DIR


def main(ano: int) -> None:
    joined = juntar_mj_censo(carregar_banco_vde(ano))
    sem_par = municipios_sem_par(joined)
    if sem_par.height:
        print("Sem par no Censo (cd_mun nulo):")
        for r in sem_par.to_dicts():
            print(f"  {r['uf']} - {r['municipio']}")

    PROCESSED_DIR.mkdir(exist_ok=True)
    destino = PROCESSED_DIR / f"mj_censo_{ano}.csv"
    joined.write_csv(destino)
    print(f"{joined.height} linhas salvas em: {destino}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, default=2025)
    main(**vars(parser.parse_args()))
