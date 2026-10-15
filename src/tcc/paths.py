"""Caminhos do projeto, resolvidos a partir da raiz do repositório."""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

DATA_DIR = RAIZ / "data"
OUTPUT_DIR = RAIZ / "output"

SSP_DF_DIR = DATA_DIR / "dados_ssp_df"
SETORES_GPKG = DATA_DIR / "DF_setores_CD2022.gpkg"
PLANILHA_TCC = DATA_DIR / "dados tcc.xlsx"
POPULACAO_CSV = DATA_DIR / "qtd_subdistritos_df.csv"
PROCESSED_DIR = DATA_DIR / "processed"
MJ_DIR = DATA_DIR / "Dados MJ"
CENSO_IBGE_CSV = DATA_DIR / "censo_ibge.csv"
# Estimativas anuais de população por município (IBGE/SIDRA, tabela 6579).
POPULACAO_ESTIMADA_CSV = DATA_DIR / "ibge_populacao_estimada.csv"
# BancoVDE de todos os anos junto ao Censo, às dimensões e à população do ano.
MJ_CENSO_GEO_PARQUET = PROCESSED_DIR / "mj_censo_geo.parquet"
# Balanços da SSP-DF por RA, 2015–2026 (scripts/consolidar_ssp_ra.py) e a
# mesma série junta às dimensões, às delegacias e à população do ano.
SSP_RA_CONSOLIDADO_CSV = PROCESSED_DIR / "ssp_df_ra_consolidado.csv"
SSP_RA_GEO_PARQUET = PROCESSED_DIR / "ssp_ra_geo.parquet"

# Dimensões geográficas (uma linha por município / UF / região).
MUNICIPIO_CSV = PROCESSED_DIR / "municipio.csv"
UNIDADE_FEDERACAO_CSV = PROCESSED_DIR / "unidade_federacao.csv"
REGIAO_CSV = PROCESSED_DIR / "regiao.csv"

# Malhas do IBGE (GeoJSON) baixadas sob demanda pelo painel.
MALHAS_DIR = DATA_DIR / "malhas"
