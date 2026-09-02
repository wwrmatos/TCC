"""Carga das planilhas de ocorrências da SSP-DF, uma por Região Administrativa."""

import unicodedata
from pathlib import Path

import polars as pl

from tcc.paths import SSP_DF_DIR

FOOTNOTE_PREFIXES = ("Fonte", "Obs:", "*")

RA_PARA_CD_SUBDIST: dict[str, str] = {
    "RA I - BRASILIA":              "53001080506",  # Plano Piloto
    "RA II - GAMA":                 "53001080507",
    "RA III - TAGUATINGA":          "53001080508",
    "RA IV - BRAZLÂNDIA":           "53001080509",
    "RA V - SOBRADINHO":            "53001080510",
    "RA VI - PLANALTINA":           "53001080511",
    "RA VII - PARANOÁ":             "53001080512",
    "RA VIII - NÚCLEO BANDEIRANTE": "53001080514",
    "RA IX - CEILÂNDIA":            "53001080515",
    "RA X - GUARÁ":                 "53001080516",
    "RA XI - CRUZEIRO":             "53001080517",
    "RA XII - SAMAMBAIA":           "53001080518",
    "RA XIII - SANTA MARIA":        "53001080525",
    "RA XIV - SÃO SEBASTIÃO":       "53001080530",
    "RA XV - RECANTO DAS EMAS":     "53001080520",
    "RA XVI - LAGO SUL":            "53001080523",
    "RA XVII - RIACHO FUNDO":       "53001080513",
    "RA XVIII - LAGO NORTE":        "53001080521",
    "RA XIX - CANDANGOLÂNDIA":      "53001080519",
    "RA XX - ÁGUAS CLARAS":         "53001080536",
    "RA XXI - RIACHO FUNDO II":     "53001080541",
    "RA XXII - SUDOESTE":           "53001080535",  # Sudoeste/Octogonal
    "RA XXIII - VARJÃO":            "53001080543",
    "RA XXIV - PARK WAY":           "53001080542",
    "RA XXV - SCIA/ESTRUTURAL":     "53001080534",
    "RA XXVI - SOBRADINHO II":      "53001080544",
    "RA XXVII - JARDIM BOTÂNICO":   "53001080540",
    "RA XXVIII - ITAPOÃ":           "53001080538",
    "RA XXIX - S.I.A.":             "53001080533",
    "RA XXX - VICENTE PIRES":       "53001080537",
    "RA XXXI - FERCAL":             "53001080539",
    "ARNIQUEIRA":                   "53001080532",
    "SOL NASCENTE":                 "53001080531",  # Sol Nascente/Pôr do Sol
}


def parse_xlsx(path: Path) -> pl.DataFrame:
    raw = pl.read_excel(path, infer_schema_length=None)
    rows = list(raw.iter_rows())

    ra: str = (rows[2][1] or "").strip()
    ano: str = str(rows[4][3] or "").strip()
    meses: list[str] = [m for m in rows[5][3:] if m is not None]

    records = []
    current_eixo = None

    for row in rows[6:]:
        eixo_val, natureza, total = row[0], row[1], row[2]
        monthly = list(row[3 : 3 + len(meses)])

        if isinstance(eixo_val, str) and any(eixo_val.startswith(p) for p in FOOTNOTE_PREFIXES):
            continue

        if natureza is None:
            continue

        if eixo_val is not None:
            current_eixo = str(eixo_val).strip()

        records.append(
            {
                "ra": ra,
                "ano": ano,
                "eixo": current_eixo,
                "natureza": natureza,
                "total": total,
                **{mes.lower(): val for mes, val in zip(meses, monthly)},
            }
        )

    return pl.DataFrame(records)


def _sem_acento(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _canonizar_naturezas(df: pl.DataFrame) -> pl.DataFrame:
    """Unifica grafias divergentes da mesma natureza entre planilhas.

    A SSP-DF escreve, por exemplo, `TENTATIVA DE LATROCINIO` numa RA e
    `TENTATIVA DE LATROCÍNIO` nas demais. Sem isso o filtro dos mapas cria duas
    categorias e cada uma esconde parte das ocorrências.
    """
    contagem: dict[str, dict[str, int]] = {}
    for n in df["natureza"].drop_nulls().to_list():
        limpo = " ".join(str(n).split()).upper()
        chave = _sem_acento(limpo)
        contagem.setdefault(chave, {})
        contagem[chave][limpo] = contagem[chave].get(limpo, 0) + 1

    # Grafia vencedora por chave: a mais frequente entre as planilhas.
    canonica = {
        chave: max(variantes.items(), key=lambda kv: kv[1])[0]
        for chave, variantes in contagem.items()
    }
    de_para = {
        n: canonica[_sem_acento(" ".join(str(n).split()).upper())]
        for n in df["natureza"].drop_nulls().unique().to_list()
    }
    return df.with_columns(pl.col("natureza").replace(de_para))


def carregar_dados_ssp_df() -> pl.DataFrame:
    dfs = [
        parse_xlsx(f)
        for f in sorted(SSP_DF_DIR.iterdir())
        if f.is_file() and f.suffix == ".xlsx"
    ]
    df = _canonizar_naturezas(pl.concat(dfs, how="diagonal"))
    return df.with_columns(
        pl.col("ra").replace(RA_PARA_CD_SUBDIST).alias("cd_subdist")
    )
