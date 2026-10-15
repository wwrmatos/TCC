"""Dimensões geográficas (município → UF → região) e sua junção às bases finais.

As três tabelas de `data/processed/` formam uma hierarquia: `municipio` aponta
para `unidade_federacao` por `cd_uf`, que aponta para `regiao` por `cd_regiao`.
Os códigos são lidos como texto — `cd_uf` e `cd_regiao` têm zeros e espaços à
esquerda/direita que o parser numérico comeria, e `codigo_municipio` só tem 6
dígitos justamente por não trazer o dígito verificador.

O município é identificado pelos dois códigos do IBGE:

* `codigo_municipio_dv` — 7 dígitos, com o dígito verificador (ex.: 5300108);
* `codigo_municipio`    — 6 dígitos, sem o DV (ex.: 530010).

A SSP-DF não traz município (é tudo Brasília); o código sai dos 7 primeiros
dígitos de `cd_subdist`. O MJ/Censo já traz `cd_mun` com DV.
"""

import polars as pl

from tcc.paths import MUNICIPIO_CSV, REGIAO_CSV, UNIDADE_FEDERACAO_CSV

CODIGO_MUNICIPIO_DF = "5300108"

_CODIGOS = ["codigo_municipio_dv", "codigo_municipio", "cd_uf", "cd_regiao"]


def _limpar_codigos(df: pl.DataFrame) -> pl.DataFrame:
    """Tira os espaços que sobram nos códigos (`cd_regiao` vem como `"1 "`)."""
    return df.with_columns(
        pl.col(c).str.strip_chars() for c in _CODIGOS if c in df.columns
    )


def carregar_dimensao_geografica() -> pl.DataFrame:
    """As três dimensões já encadeadas: uma linha por município, com UF e região."""
    municipio = _limpar_codigos(pl.read_csv(MUNICIPIO_CSV, infer_schema_length=0))
    uf = _limpar_codigos(pl.read_csv(UNIDADE_FEDERACAO_CSV, infer_schema_length=0))
    regiao = _limpar_codigos(pl.read_csv(REGIAO_CSV, infer_schema_length=0))

    return (
        municipio.join(uf, on="cd_uf", how="left")
        .join(regiao, on="cd_regiao", how="left")
        .with_columns(
            pl.col("longitude").cast(pl.Float64, strict=False),
            pl.col("latitude").cast(pl.Float64, strict=False),
        )
    )


def juntar_dimensoes(
    df: pl.DataFrame,
    coluna_codigo: str,
    *,
    coluna_sigla: str | None = None,
    dimensao: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Junta `df` à dimensão geográfica pelo código do município **com DV**.

    `coluna_codigo` é a coluna de `df` que carrega o código de 7 dígitos; ela é
    renomeada para `codigo_municipio_dv`. Se `df` também traz a sigla da UF,
    passe-a em `coluna_sigla`: ela é renomeada para `sigla_uf` e entra na chave,
    para a coluna não sair duplicada. A junção é `left`: município sem par
    (no MJ, "NÃO INFORMADO") fica com as colunas da dimensão nulas.
    """
    dimensao = carregar_dimensao_geografica() if dimensao is None else dimensao

    chaves = {coluna_codigo: "codigo_municipio_dv"}
    if coluna_sigla is not None:
        chaves[coluna_sigla] = "sigla_uf"
    df = df.rename({de: para for de, para in chaves.items() if de != para})
    df = df.with_columns(
        pl.col("codigo_municipio_dv").cast(pl.String).str.strip_chars()
    )
    return df.join(dimensao, on=list(chaves.values()), how="left")


def sem_dimensao(joined: pl.DataFrame) -> pl.DataFrame:
    """Códigos de município que não acharam par na dimensão."""
    return (
        joined.filter(pl.col("nome_municipio").is_null())
        .select("codigo_municipio_dv")
        .unique()
        .sort("codigo_municipio_dv")
    )


def carregar_dimensao_uf() -> pl.DataFrame:
    """UF já encadeada com a região: uma linha por unidade da federação."""
    uf = _limpar_codigos(pl.read_csv(UNIDADE_FEDERACAO_CSV, infer_schema_length=0))
    regiao = _limpar_codigos(pl.read_csv(REGIAO_CSV, infer_schema_length=0))
    return uf.join(regiao, on="cd_regiao", how="left")


def completar_uf_por_sigla(
    df: pl.DataFrame,
    coluna_sigla: str,
    *,
    dimensao_uf: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Preenche UF e região nas linhas sem município a partir da sigla da UF.

    No BancoVDE o município pode vir como "NÃO INFORMADO", mas a UF é sempre
    conhecida — sem isso essas ocorrências sumiriam de qualquer agregação por
    UF ou região.
    """
    dimensao_uf = carregar_dimensao_uf() if dimensao_uf is None else dimensao_uf
    sufixo = "_por_sigla"
    df = df.join(
        dimensao_uf.rename({"sigla_uf": coluna_sigla}),
        on=coluna_sigla,
        how="left",
        suffix=sufixo,
    )
    colunas = [c for c in dimensao_uf.columns if c != "sigla_uf"]
    preenchidas = [
        pl.coalesce(pl.col(c), pl.col(f"{c}{sufixo}")).alias(c) for c in colunas
    ]
    return df.with_columns(preenchidas).drop(f"{c}{sufixo}" for c in colunas)
