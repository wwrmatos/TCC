# TCC — Criminalidade e distribuição de delegacias no Distrito Federal

Cruza as ocorrências registradas pela SSP-DF por Região Administrativa com a
localização das delegacias, e gera dois mapas interativos:

| Mapa | Arquivo gerado | O que mostra |
| --- | --- | --- |
| Coroplético | `output/mapa_ssp_delegacias.html` | Polígono da RA colorido pelo total de ocorrências |
| Bolhas | `output/mapa_bolhas_delegacias.html` | Círculo no centroide da RA, raio proporcional ao total |

Nos dois, um painel lateral filtra por natureza do crime e os marcadores azuis
são as delegacias.

## Como rodar

Requer [uv](https://docs.astral.sh/uv/) e Python 3.13+.

```bash
uv sync                              # cria o .venv e instala tudo, incl. o pacote `tcc`
uv run python scripts/gerar_mapas.py --abrir
```

Para os notebooks:

```bash
uv run --group dev jupyter lab       # ou selecione o .venv como kernel na sua IDE
```

## Estrutura

```
src/tcc/          # pacote com toda a lógica reutilizável
  paths.py        # caminhos do projeto
  ssp_df.py       # parser das planilhas da SSP-DF
  geo.py          # setores censitários, delegacias e o spatial join
  mapas.py        # construção dos dois mapas folium
scripts/
  gerar_mapas.py  # ponto de entrada reproduzível
notebooks/        # exploração; a lógica pesada vive em src/tcc
data/             # dados brutos — NÃO versionado
output/           # HTML gerado — NÃO versionado
```

## Dados

`data/` não vai para o Git (são centenas de MB de planilhas públicas). Para
reproduzir, baixe e coloque nos caminhos abaixo:

| Caminho | Fonte |
| --- | --- |
| `data/dados_ssp_df/*.xlsx` | [SSP-DF — Dados abertos](https://www.ssp.df.gov.br/dados-abertos/), uma planilha por RA |
| `data/DF_setores_CD2022.gpkg` | IBGE — malha de setores censitários do Censo 2022 (DF) |
| `data/dados tcc.xlsx` | Planilha própria; a aba `DPs` lista as delegacias com coordenadas |
| `data/portal-abertos-df/*.xlsx` | Portal de Dados Abertos do DF — séries históricas por natureza |
| `data/Dados MJ/BancoVDE *.xlsx` | Ministério da Justiça — Banco de dados VDE (Sinesp) |
| `data/Anuario de segurança publica/*.csv` | FBSP — Anuário Brasileiro de Segurança Pública |
| `data/processed/` | Gerado a partir dos anteriores pelos notebooks |

Só as planilhas em `data/dados_ssp_df/` (nível raiz, sem subpastas) e o `.gpkg`
são necessários para gerar os mapas.
