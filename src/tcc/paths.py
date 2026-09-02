"""Caminhos do projeto, resolvidos a partir da raiz do repositório."""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

DATA_DIR = RAIZ / "data"
OUTPUT_DIR = RAIZ / "output"

SSP_DF_DIR = DATA_DIR / "dados_ssp_df"
SETORES_GPKG = DATA_DIR / "DF_setores_CD2022.gpkg"
PLANILHA_TCC = DATA_DIR / "dados tcc.xlsx"
POPULACAO_CSV = DATA_DIR / "qtd_subdistritos_df.csv"
