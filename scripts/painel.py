"""Sobe o painel Dash em http://127.0.0.1:8050.

Requer as bases finais em `data/processed/`: `mj_censo_geo.parquet` (gerada
por `scripts/consolidar_mj.py`) e `ssp_ra_geo.parquet` (por
`scripts/consolidar_ssp.py`). Na primeira
execução, baixa as malhas de UF e município do IBGE para `data/malhas/`.

Uso:
    uv run python scripts/painel.py [--porta 8050] [--debug]
"""

import argparse

from tcc.painel.app import criar_app


def main(porta: int, debug: bool) -> None:
    criar_app().run(port=porta, debug=debug)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--porta", type=int, default=8050)
    parser.add_argument("--debug", action="store_true", help="recarrega ao salvar e mostra erros")
    main(**vars(parser.parse_args()))
