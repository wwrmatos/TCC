"""Baixa do SIDRA/IBGE (tabela 6579, "População residente estimada") a
estimativa de população de todos os municípios em cada ano pedido e salva em
`data/ibge_populacao_estimada.csv` (uma linha por município × ano).

O IBGE não publica estimativa municipal para 2022 (ano do Censo) nem para 2023;
esses anos são simplesmente pulados aqui e tratados em `tcc.populacao`.

Uso:
    uv run python scripts/baixar_populacao_ibge.py [--ano-inicial 2015] [--ano-final 2026]
"""

import argparse
import gzip
import json
import re
import urllib.request

import polars as pl

from tcc.mj import CD_UF_PARA_SIGLA
from tcc.paths import POPULACAO_ESTIMADA_CSV

URL_PERIODOS = "https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos"
URL_VALORES = "https://apisidra.ibge.gov.br/values/t/6579/n6/all/v/9324/p/{ano}?formato=json"


def _json(url: str):
    with urllib.request.urlopen(url, timeout=120) as resp:
        corpo = resp.read()
    # A API do IBGE às vezes responde em gzip mesmo sem o pedido pedir.
    if corpo.startswith(b"\x1f\x8b"):
        corpo = gzip.decompress(corpo)
    return json.loads(corpo)


def estimativas(ano: int) -> pl.DataFrame:
    # A primeira linha é o cabeçalho. Município que ainda não existia no ano vem
    # com valor "..." (ex.: os criados em 2013 nas estimativas anteriores).
    linhas = [l for l in _json(URL_VALORES.format(ano=ano))[1:] if l["V"].isdigit()]
    return pl.DataFrame(
        {
            "ano": [ano] * len(linhas),
            "cod_municipio_dv": [l["D1C"] for l in linhas],
            # "Alta Floresta D'Oeste - RO" ou "(RO)" → "Alta Floresta D'Oeste"
            "nome_municipio": [re.sub(r" (- [A-Z]{2}|\([A-Z]{2}\))$", "", l["D1N"]) for l in linhas],
            "populacao": [int(l["V"]) for l in linhas],
        }
    ).with_columns(
        cod_uf=pl.col("cod_municipio_dv").str.slice(0, 2),
        # O arquivo usa o código de 6 dígitos (sem o dígito verificador).
        cod_municipio=pl.col("cod_municipio_dv").str.slice(0, 6),
    ).with_columns(
        uf=pl.col("cod_uf").replace_strict(CD_UF_PARA_SIGLA),
    ).select("ano", "uf", "cod_uf", "cod_municipio", "nome_municipio", "populacao")


def main(ano_inicial: int, ano_final: int) -> None:
    disponiveis = {int(p["id"]) for p in _json(URL_PERIODOS)}
    anos = [a for a in range(ano_inicial, ano_final + 1) if a in disponiveis]
    pulados = [a for a in range(ano_inicial, ano_final + 1) if a not in disponiveis]

    tabelas = []
    for ano in anos:
        df = estimativas(ano)
        print(f"{ano}: {df.height} municípios")
        tabelas.append(df)

    pl.concat(tabelas).write_csv(POPULACAO_ESTIMADA_CSV)
    print(f"Salvo em {POPULACAO_ESTIMADA_CSV}")
    if pulados:
        print(f"Sem estimativa no IBGE (ficam de fora): {pulados}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ano-inicial", type=int, default=2015)
    parser.add_argument("--ano-final", type=int, default=2026)
    main(**vars(parser.parse_args()))
