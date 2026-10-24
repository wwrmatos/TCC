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

### Painel (Dash)

```bash
uv run python scripts/painel.py      # http://127.0.0.1:8050  (--debug recarrega ao salvar)
```

Lê as bases finais `data/processed/mj_censo_geo.parquet` (gerada por
`scripts/consolidar_mj.py`) e `data/processed/ssp_ra_geo.parquet` (gerada por
`scripts/consolidar_ssp.py`), ver abaixo, e, na primeira execução, baixa as malhas de UF e município do IBGE para
`data/malhas/`. São duas abas:

- **Brasil — MJ/Sinesp (2016–2026)**: evento, localidade, abrangência, anos e
  meses. Anos e meses são botões que ligam e desligam, em qualquer combinação
  (fevereiro e dezembro de 2016 e 2025, por exemplo), com atalhos (todos os anos,
  último ano completo, ano todo, limpar); os meses valem em cada ano escolhido. A série
  histórica cobre todo o período: evolução mensal com média de 12 meses,
  comparação anual (os mesmos meses em todos os anos) e mapa de calor UF × ano
  (ou, numa UF, os 20 maiores municípios × ano). KPIs, mapa coroplético por UF
  (clicar numa UF abre os municípios dela), ranking, vítimas por sexo e tabela
  somam os anos escolhidos. Com um ano só, a variação é sobre o ano anterior;
  com vários, é do primeiro para o último ano escolhido, nos mesmos meses. Clicar
  num ano em qualquer gráfico da série seleciona só aquele ano. Taxa por 100 mil
  habitantes, com a população do próprio ano; com vários anos, é a média anual
  (soma dos registros ÷ soma das populações).
  Quando uma UF tem o ano inteiro zerado num evento que ela registra com
  frequência nos outros anos (média ≥ 12 por ano), o painel trata o ano como
  lacuna (provável falta de envio ao Sinesp), não como zero, e avisa. Isso é
  comum nos primeiros anos: em tentativa de feminicídio, 20 UFs não têm nenhum
  registro em 2016. O ano corrente (2026, até agosto) aparece hachurado.
  Só parte dos eventos do BancoVDE vem por município; os demais ficam por UF
  (marcados "só por UF" no filtro). O valor de cada evento está em colunas
  diferentes — vítimas, ocorrências ou kg (apreensões de droga) — e o painel
  mostra a unidade certa em cada caso.
  O filtro de evento separa os eventos por categoria (CVLI, tentativas,
  violência sexual, patrimônio, tráfico, armas e apreensão de drogas) e deixa
  de fora atividades de bombeiros e de licenciamento, pessoas
  desaparecidas/localizadas, suicídio, mortes a esclarecer, mortes no trânsito,
  mortes e suicídios de agentes do Estado, mortes por intervenção deles e
  mandados de prisão cumpridos. Há também grupos que somam eventos da
  mesma unidade — CVLI, estupro total, roubo e furto de veículo, apreensão de
  drogas —, com aviso quando falta um dos membros numa UF. A lista fica em
  `CATEGORIAS_MJ` (`src/tcc/painel/dados.py`).
- **Distrito Federal — SSP-DF (2015–2026)**: naturezas (com atalhos por eixo),
  anos e meses, como na aba do MJ. Os mapas folium deste
  repositório (coroplético/bolhas) abrem com a seleção do painel; há taxa por RA
  destacando as RAs sem delegacia, série mensal com média de 12 meses,
  comparação anual da taxa do DF, mapa de calor RA × ano e tabela. Taxa por mil
  habitantes; com vários anos, é a média anual. Os totais do DF são a soma das
  RAs, não o balanço do DF, que separa naturezas (feminicídio, estupro de
  vulnerável) que o das RAs não separa. De 2023 a 2026, o feminicídio por RA vem
  dos estudos da CTMHF (ver abaixo), num eixo à parte e fora do padrão, porque
  já está contado no HOMICÍDIO. Nos anos e meses em que nenhuma das naturezas
  escolhidas existe (feminicídio sozinho antes de 2025, por exemplo), a série
  mensal, a comparação anual, o mapa de calor e a variação ficam sem dado, em vez
  de zero; a taxa média de vários anos usa só os anos com dado. Se ao menos uma
  das escolhidas existe no ano, as outras contam como zero, como antes. RA/ano sem balanço válido (Sobradinho
  2015–2016, Planaltina 2016–2017, Riacho Fundo 2017, Sudoeste 2023) fica fora
  do total e da taxa, com aviso. A variação usa só as RAs com dado nos dois anos.
  As delegacias são as de hoje em todos os anos. `?aba=ssp` na URL abre direto
  nesta aba.

**Nomes dos eventos.** O que existe nas duas fontes tem o mesmo nome nas duas
abas (`NOMES_MJ` e `NOMES_SSP` em `src/tcc/painel/dados.py`): homicídio doloso,
feminicídio, latrocínio, lesão corporal seguida de morte, tentativa de homicídio,
estupro, estupro de vulnerável, roubo de veículo e tráfico de drogas. O homicídio
e a tentativa de homicídio das RAs incluem o feminicídio e a tentativa de
feminicídio, que o Sinesp separa, e o nome diz isso. O que só existe numa fonte
(furto de veículo e arma de fogo apreendida no MJ; roubo a transeunte,
posse/porte de arma etc. na SSP-DF) mantém o nome da fonte, na SSP-DF em caixa
normal. A unidade só aparece no seletor do MJ quando é kg.

**MJ × SSP-DF no DF.** Mesmo com o nome igual, nem todo evento bate entre as
abas. Quando não bate, as duas abas avisam e explicam (`DIFERENCA_DF_MJ` em
`app.py`):

| Evento | Situação |
| --- | --- |
| Feminicídio, latrocínio, lesão corporal seguida de morte, roubo de veículo | Batem (roubo de veículo com 0 a 2 de diferença por mês) |
| Homicídio doloso | O MJ conta vítimas sem feminicídio (desde 2023, igual às vítimas do balanço do DF; antes, vítimas do balanço menos feminicídio); as RAs, ocorrências com feminicídio |
| Tentativa de homicídio | O MJ conta vítimas sem tentativa de feminicídio; as RAs, ocorrências com ela |
| Estupro, estupro de vulnerável | Até 2024, o estupro das RAs inclui o de vulnerável. Somados, batem (até 5%) em 2016, 2019, 2022, 2023 e 2026; em 2018, 2020, 2021 e 2024 o MJ tem 14% a 24% a mais, e em 2025, 12% a menos, sem causa identificada |
| Tráfico de drogas | O MJ soma a Polícia Federal, que a SSP-DF não tem; no DF, o filtro de abrangência abre só com a Estadual. Assim batem exatamente em 2016–2021 e com até 2% depois, menos 2025 (+5% no MJ, sem causa identificada) |

**Previsão nas séries mensais.** Os dois gráficos de evolução mensal trazem,
em laranja pontilhado, a previsão dos 12 meses seguintes com intervalo de 95%
(`src/tcc/previsao.py`). O modelo é um SARIMA(p,d,q)(P,D,Q)₁₂ sobre `log1p` da
contagem mensal, reajustado a cada filtro: `d` pelo teste KPSS, `D` pela força
da sazonalidade na STL (> 0,64) e `p`, `q` ≤ 2, `P`, `Q` ≤ 1 pelo menor AIC, na
busca stepwise do `auto.arima` (Hyndman & Khandakar, 2008). Abaixo do gráfico,
o painel mostra o modelo escolhido e o erro absoluto médio nos últimos 12
meses, com o modelo ajustado sem eles, ao lado do erro do sazonal ingênuo
(repetir o mesmo mês do ano anterior). Quando o modelo não supera o ingênuo, o
painel avisa. Não há previsão para séries com menos de 48 meses de dado, média
abaixo de 5 por mês ou mais de 30% de meses zerados. Meses sem dado entram como
faltantes, não como zero: no MJ, os anos em que a UF não enviou dados; na
SSP-DF, os meses em que falta alguma RA (2015–2017 e 2023) e os meses em que uma
natureza some do balanço (TRÁFICO DE DROGAS e POSSE/PORTE DE ARMA em 2019, por
exemplo). Naturezas sem registro no último mês, como FURTO A TRANSEUNTE em
2026, ficam fora da previsão. No Brasil inteiro, as UFs que não enviaram dados
em algum ano continuam somando zero, então parte da tendência pode vir da
cobertura. O ajuste leva até ~5 s e roda num callback à parte, depois do resto
da página; filtros repetidos usam cache.

### Série histórica do MJ/Sinesp (2016–2026)

```bash
uv run python scripts/baixar_populacao_ibge.py   # estimativas do IBGE → data/ibge_populacao_estimada.csv
uv run python scripts/consolidar_mj.py           # BancoVDE de todos os anos → data/processed/mj_censo_geo.parquet
```

`consolidar_mj.py` repete, para cada `data/Dados MJ/BancoVDE <ano>.xlsx`, a
junção ao Censo e às dimensões que `juntar_mj_censo.py` e `juntar_dimensoes.py`
fazem para um ano. O resultado vai para um único arquivo Parquet (~8,3 milhões
de linhas, ~10 MB; em CSV eram 1,8 GB). As colunas são as do
`mj_censo_2025_geo.csv` mais `ano`, `populacao` e `fonte_populacao`. A população é a do município **naquele ano**, enquanto `v0001`
continua sendo a do Censo 2022:

| Ano | População usada |
| --- | --- |
| 2016–2021, 2024–2026 | Estimativa do IBGE do ano (SIDRA, tabela 6579) |
| 2022 | Censo 2022 (o IBGE não publica estimativa municipal no ano do Censo) |
| 2023 | Censo 2022 (o IBGE não publicou estimativa municipal para 2023) |

As estimativas de anos diferentes não formam uma série contínua: o IBGE refaz a
projeção a cada Censo. Brasília, por exemplo, passa de 3,04 milhões em 2017 para
2,97 milhões em 2018. Em séries de taxa, saltos desse tamanho podem vir da
população e não do crime. Entre a primeira e a última versão das estimativas de
2025, o IBGE também revisou a divisa entre Aracaju e São Cristóvão (SE); o
arquivo usa os valores atuais.

### Série histórica da SSP-DF (2015–2026)

Dois scripts independentes do pacote `tcc` (as dependências estão declaradas no
cabeçalho de cada um, então rodam com `uv run --script`):

```bash
uvx playwright install chromium                  # só na primeira vez
uv run --script scripts/baixar_ssp_ra.py         # baixa PDFs/planilhas → data/ssp_df_ra_bruto/
uv run --script scripts/consolidar_ssp_ra.py     # junta tudo → data/processed/ssp_df_ra_consolidado.csv
uv run --script scripts/extrair_feminicidio_ctmhf.py  # feminicídios por RA 2023–2026 → data/processed/ssp_df_feminicidio_ra.csv (depois de consolidar_mj.py)
uv run python scripts/consolidar_ssp.py          # + dimensões, delegacias e população → data/processed/ssp_ra_geo.parquet
```

`consolidar_ssp.py` faz com a série o que `consolidar_mj.py` faz com o BancoVDE:
junta cada linha às dimensões (município/UF/região), às delegacias da RA e à
população **da RA naquele ano**, num Parquet com as colunas do `ssp_dp_geo.csv`
mais `ra_numero`, `populacao_censo` (Censo 2022), `fonte_populacao`, `contagem`,
`fonte` e `arquivo`; `populacao` passa a ser a do ano. O IBGE não estima
população abaixo do município, então a do DF no ano (a mesma regra do MJ:
estimativa; Censo 2022 em 2022 e 2023) é rateada
pela participação de cada RA no Censo 2022. Isso supõe participação constante
entre os anos: em 2018–2019 a população de RAs que cresceram rápido fica
superestimada, e a das que perderam gente, subestimada. Antes de 2020, a
população de Arniqueira vai para Águas Claras e a de Sol Nascente/Pôr do Sol
para Ceilândia, onde estão os registros desses anos. A linha do DF inteiro
(`ra_numero` 0) fica com a população do DF. A estimativa de 2015 exige rodar
`baixar_populacao_ibge.py`, que agora começa em 2015.

`baixar_ssp_ra.py` abre com Playwright a página
[Dados por Região Administrativa](https://www.ssp.df.gov.br/dados-por-regiao-administrativa)
e baixa o balanço criminal mensal do **DF inteiro** e de **cada RA**, ano a ano
(`--ano-inicial`/`--ano-final`, padrão 2015–2026; `--so-listar` só mostra os
links). Grava `data/ssp_df_ra_bruto/<ano>/<ra>.<pdf|xlsx>` e um `manifesto.csv`
com uma linha por link: tipo real do arquivo, anos encontrados no conteúdo,
`ano_confere`, `conteudo_duplicado` e `tem_excel`.

`consolidar_ssp_ra.py` gera um CSV com uma linha por RA × ano × natureza ×
contagem, no formato do `ssp_dp.csv`:

| Coluna | Conteúdo |
| --- | --- |
| `ra_numero`, `ra` | Número oficial da RA (I = 1 … XXXIII = 33) e nome; **0 = Distrito Federal** inteiro |
| `ano`, `eixo`, `natureza` | Como no balanço da SSP-DF (eixos 1 a 4) |
| `contagem` | `OCORRÊNCIA` ou `VÍTIMA` — só o DF traz vítimas, e só nas naturezas de C.V.L.I. |
| `total`, `jan` … `dez` | Totais do ano e de cada mês; meses ainda não fechados ficam vazios |
| `fonte`, `arquivo` | `xlsx` ou `pdf`, e o arquivo de origem em `data/ssp_df_ra_bruto/` |

Para cada RA/ano usa a planilha quando há uma válida; senão, o PDF. Até 2017 só
existe PDF. Os PDFs são lidos pela grade da tabela: as bordas definem as linhas e
a coluna NATUREZA, e cada número vai para o mês cujo cabeçalho está mais próximo.
Onde há planilha e PDF do mesmo ano, as duas leituras batem em ~99% dos casos, e
as diferenças restantes são de versões diferentes publicadas no site. Nos PDFs o
eixo vem escrito em texto girado, ilegível, então é herdado das planilhas pelo nome
da natureza.

#### Problemas da fonte (e o que os scripts fazem com eles)

O site da SSP-DF tem muitos links e arquivos errados. Nada é aceito só pelo rótulo
do link:

- **Tipo trocado.** O "PDF" de Brasília 2023 é um `.xlsx`. O tipo vem dos bytes
  do arquivo.
- **Arquivo de outro ano.** Em 2022–2024, 34 planilhas são de outro ano: o "XLS
  2024" de Águas Claras é a planilha de 2022, e o de Sobradinho II 2024 é a de
  2026. Alguns PDFs de 2023 são cópias de 2022, e os de Planaltina 2016/2017
  trazem o ano seguinte. O download confere o ano no conteúdo e descarta o que
  não bate (`ano_confere = false`). Nesses casos, a RA/ano usa o outro formato.
- **Arquivo de outra RA.** Sobradinho I 2015/2016 é o arquivo do Sobradinho II.
  Riacho Fundo I 2017 é o do Riacho Fundo II. O PDF2 do Varjão 2018 é o de
  Vicente Pires. O XLS do Cruzeiro 2019 é o de Ceilândia. A consolidação confere
  o número da RA no cabeçalho e ignora esses arquivos.
- **Sem nenhum arquivo correto, ficam fora do CSV:** Sobradinho I (2015, 2016),
  Planaltina (2016, 2017), Riacho Fundo I (2017) e Sudoeste/Octogonal (2023). Os
  dados de Planaltina 2017 existem, mas estão no arquivo do link "2016", que hoje
  é descartado.
- **Rótulo errado.** Em 6 arquivos de 2016–2018 (Ceilândia, São Sebastião,
  SCIA/Estrutural), a linha de TENTATIVA DE LATROCÍNIO vem rotulada "ROUBO EM
  RESIDÊNCIA". A posição na tabela e o PDF de 2018 confirmam o erro, e a
  consolidação renomeia a linha.
- **Total que não fecha.** Em 49 linhas, o TOTAL impresso difere da soma dos
  meses: Santa Maria, Sobradinho, São Sebastião e Varjão em 2017, e SIA em 2016.
  Os valores ficam como na fonte.
- **2018 tem dois PDFs.** O "PDF1" é a versão do *Viva Brasília – Pacto pela
  Vida* e o "PDF2" é o balanço normal. Só a Fercal não tem planilha em 2018, e
  nela é usado o PDF2.
- **Ano corrente.** A planilha de 2026 vem com 0 nos meses que ainda não
  aconteceram. A consolidação anula os meses a partir da data de atualização da
  nota "Obs".

#### Cuidados ao comparar anos e recortes

- **Naturezas que mudam com o tempo.** 2019 não tem o eixo 4 (PRODUTIVIDADE
  POLICIAL), nem na planilha nem no PDF. 2015 não tem TENTATIVA DE LATROCÍNIO.
  ESTUPRO DE VULNERÁVEL aparece separado nas RAs a partir de 2025 e no DF a partir
  de 2024; até 2024, o ESTUPRO das RAs inclui o de vulnerável (em 2024, 939 = 319 +
  620 do DF), e o painel avisa quando o estupro é selecionado nesses anos.
  FEMINICÍDIO e TENTATIVA DE FEMINICÍDIO só existem no DF, a partir de 2023.
- **Homicídio do DF × soma das RAs.** A partir de 2023, o balanço do DF separa
  FEMINICÍDIO de HOMICÍDIO e o das RAs não separa. Para comparar, use o HOMICÍDIO
  do DF somado ao FEMINICÍDIO, que bate com a soma das RAs. De 2020 a 2022, DF e
  soma das RAs coincidem exatamente. Em 2015–2017, a soma fica abaixo do DF porque
  faltam RAs (acima).
- **Nomes unificados.** O DF escreve "ROUBO EM TRANSPORTE COLETIVO" e
  "POSSE/PORTE DE ARMA DE FOGO", e o CSV usa os nomes das RAs ("ROUBO EM
  COLETIVO", "POSSE/PORTE DE ARMA"). Grafias que só diferem em acento também são
  unificadas, e os asteriscos de nota de rodapé ("ROUBO EM COMÉRCIO *") são
  removidos.
- **Arniqueira e Sol Nascente/Pôr do Sol** só têm dados a partir de 2020.

#### Feminicídio por RA (CTMHF, 2023–2026)

Os balanços das RAs não separam o feminicídio do HOMICÍDIO. Para tê-lo por RA,
`extrair_feminicidio_ctmhf.py` lê os estudos mensais da Câmara Técnica de
Monitoramento de Homicídios e Feminicídios (CTMHF/SSP-DF), baixados à mão em
`data/dados_ssp_df/feeminicidio/<ano>/<Mês>.pdf` (hoje, jan/2021 a set/2026). Cada estudo é acumulado desde março de 2015 e traz casos por RA × ano
e, na maioria, por mês × ano no DF, mas não RA × mês. O mês de cada
caso por RA sai da diferença entre estudos consecutivos: os casos novos de uma
RA entram no mês que cresceu na TABELA 03. Os estudos também revisam casos
antigos (um caso deixa de ser feminicídio, muda de RA ou de mês), e a
reconstrução usa o menor número de revisões que explica cada estudo.

- **Anos cobertos.** Entra o ano que tem estudo de quase todos os meses (podem
  faltar até 2). De maio/2021 a fevereiro/2023, os estudos não trazem a tabela
  por mês × ano, só um gráfico "mês do fato" que soma todos os anos, e são
  ignorados; por isso 2021 e 2022 ficam de fora, e 2023 começa no estudo de
  março (os casos de janeiro a março entram juntos). De 2015 a 2020 não há como
  separar o mês dos casos de cada RA.

- **Mês incerto.** Quando um estudo traz casos novos de RAs diferentes em meses
  diferentes, a combinação RA × mês não fica determinada. São 24 dos 97 casos
  (12 em 2023, pelo começo em março; 4 em 2024; 3 em 2025; 5 em 2026), marcados
  com `mes_incerto` em
  `data/processed/ssp_df_feminicidio_casos.csv`. Os totais por RA × ano e por
  mês no DF são exatamente os do estudo usado.
- **Perto do MJ.** Os estudos seguem incluindo casos de anos fechados (um
  homicídio que o processo passa a tratar como feminicídio) e tirando outros: o
  de setembro/2026 tem 29 casos em 2025, e o MJ/Sinesp, 28 — a diferença é um
  caso de Paranoá de novembro/2025, incluído pela CTMHF em janeiro/2026. Para os
  dois painéis baterem, cada ano usa o estudo mais recente cuja contagem mensal
  do DF coincide com a do MJ (2024: maio/2026; 2025: dezembro/2025; 2026:
  setembro/2026). Sem nenhum igual, usa o que menos difere, se a diferença for
  de até 3 casos no ano, somando as diferenças de cada mês (`TOLERANCIA_MJ`);
  acima disso, o ano fica de fora. 2023 usa o estudo de outubro/2025 e tem 30
  casos contra 31 no MJ (um caso em maio, e não junho, e um de outubro a menos).
  `consolidar_ssp.py` repete a conferência com o mesmo limite e para com erro
  acima dele. Por isso `extrair_feminicidio_ctmhf.py` roda depois de
  `consolidar_mj.py`.
- **Já está no HOMICÍDIO.** No painel, o FEMINICÍDIO fica num eixo próprio,
  `5. FEMINICÍDIO - CTMHF`, fora da seleção padrão, e o painel avisa quando ele
  é somado ao HOMICÍDIO.
- **Ano corrente.** `consolidar_ssp.py` corta os meses que os balanços ainda
  não têm (2026 vai até agosto), para o feminicídio cobrir o mesmo período das
  outras naturezas.
- Arapoanga e Água Quente, que a CTMHF já separa, vão para Planaltina e
  Recanto das Emas, como nos balanços. Rótulos de RA cortados com reticências
  no PDF ("Pôr do Sol / Sol Na…") valem pelo começo.
- **RA sem balanço.** O CSV tem as 33 RAs em todos os anos, mas RA/ano sem
  balanço válido (Sudoeste/Octogonal 2023) continua fora do total e da taxa.

Para os notebooks:

```bash
uv run --group dev jupyter lab       # ou selecione o .venv como kernel na sua IDE
```

## Estrutura

```
src/tcc/          # pacote com toda a lógica reutilizável
  paths.py        # caminhos do projeto
  dimensoes.py    # dimensões município / UF / região e a junção às bases finais
  ssp_df.py       # parser das planilhas da SSP-DF
  populacao.py    # população por RA e cálculo da taxa
  previsao.py     # previsão SARIMA das séries mensais do painel
  geo.py          # setores censitários, delegacias e o spatial join
  mapas.py        # construção dos dois mapas folium
  malhas.py       # malhas de UF/município do IBGE, com cache em data/malhas/
  painel/         # painel Dash (dados.py: carga das bases; app.py: layout e callbacks)
scripts/
  gerar_mapas.py  # ponto de entrada reproduzível
  painel.py       # sobe o painel Dash
  juntar_dimensoes.py  # anexa município/UF/região às bases finais
  baixar_ssp_ra.py     # scraper (Playwright) dos balanços da SSP-DF por RA, 2015–2026
  consolidar_ssp_ra.py # junta os PDFs/planilhas baixados num CSV único
  extrair_feminicidio_ctmhf.py  # feminicídios por RA e mês (2023–2026) dos estudos da CTMHF
  baixar_populacao_ibge.py  # estimativas anuais de população por município (SIDRA)
  consolidar_mj.py     # BancoVDE 2016–2026 + Censo + dimensões + população do ano
  consolidar_ssp.py    # série da SSP-DF 2015–2026 + dimensões + delegacias + população do ano
sql/
  ddl.sql         # modelo dimensional em PostgreSQL das bases finais
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
| `data/ibge_populacao_estimada.csv` | Baixado por `scripts/baixar_populacao_ibge.py` (IBGE/SIDRA, tabela 6579) |
| `data/Anuario de segurança publica/*.csv` | FBSP — Anuário Brasileiro de Segurança Pública |
| `data/qtd_subdistritos_df.csv` | População residente por subdistrito (IBGE, Censo 2022) |
| `data/ssp_df_ra_bruto/` | Baixado por `scripts/baixar_ssp_ra.py` (SSP-DF — dados por RA, 2015–2026) |
| `data/processed/` | Gerado a partir dos anteriores pelos notebooks e scripts |

Só as planilhas em `data/dados_ssp_df/` (nível raiz, sem subpastas), o `.gpkg` e
o `qtd_subdistritos_df.csv` são necessários para gerar os mapas.
