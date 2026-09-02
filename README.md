# TCC — Criminalidade e distribuição de delegacias no Distrito Federal

Cruza as ocorrências registradas pela SSP-DF por Região Administrativa com a
localização das delegacias, e gera dois mapas interativos:

| Mapa | Arquivo gerado | O que mostra |
| --- | --- | --- |
| Coroplético | `output/mapa_ssp_delegacias.html` | Polígono da RA colorido pela **taxa** de ocorrências por mil habitantes |
| Bolhas | `output/mapa_bolhas_delegacias.html` | Círculo no centroide da RA, raio proporcional ao total absoluto |

Nos dois, um painel lateral filtra por natureza do crime e os marcadores azuis
são as delegacias. Os tooltips trazem população, total absoluto e taxa, então dá
para comparar volume (bolhas) com incidência relativa (coroplético).

O painel agrupa as naturezas pelos quatro eixos da SSP-DF. O eixo
`4. PRODUTIVIDADE POLICIAL` (tráfico, uso/porte de drogas, posse de arma,
localização de veículo) vem **desmarcado por padrão**: esses registros nascem da
atuação da polícia, não da vítima, então medem policiamento e não vitimização —
e num trabalho sobre distribuição de delegacias incluí-los tornaria a análise
circular. Marcá-los é possível; o painel avisa que a taxa deixou de medir só
vitimização.

A taxa é por **mil** habitantes, e não por 100 mil: várias RAs têm população bem
abaixo disso (o SIA tem ~5 mil residentes) e a base de 100 mil produziria números
sem leitura direta. A população vem do Censo 2022 e é a *residente* — em RAs de
perfil não residencial, como o SIA, a taxa fica superestimada.

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
  populacao.py    # população por RA e cálculo da taxa
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
| `data/qtd_subdistritos_df.csv` | População residente por subdistrito (IBGE, Censo 2022) |
| `data/processed/` | Gerado a partir dos anteriores pelos notebooks |

Só as planilhas em `data/dados_ssp_df/` (nível raiz, sem subpastas), o `.gpkg` e
o `qtd_subdistritos_df.csv` são necessários para gerar os mapas.
