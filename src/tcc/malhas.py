"""Malhas territoriais do IBGE (API de malhas v3) com cache em `data/malhas/`.

A API devolve GeoJSON em que cada feature traz o código IBGE em
`properties.codarea`: 2 dígitos para UF, 7 (com DV) para município. É a mesma
chave de `cd_uf` e `codigo_municipio_dv` nas bases finais.
"""

import gzip
import json
import urllib.request

from shapely.geometry import mapping, shape
from shapely.geometry.polygon import orient

from tcc.paths import MALHAS_DIR

_API = "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"


def _baixar(intrarregiao: str) -> dict:
    destino = MALHAS_DIR / f"br_{intrarregiao.lower()}.json"
    if not destino.exists():
        MALHAS_DIR.mkdir(parents=True, exist_ok=True)
        url = (
            f"{_API}?formato=application/vnd.geo+json"
            f"&qualidade=minima&intrarregiao={intrarregiao}"
        )
        with urllib.request.urlopen(url, timeout=120) as resp:
            corpo = resp.read()
            # A API responde com gzip mesmo sem `Accept-Encoding`.
            if resp.headers.get("Content-Encoding") == "gzip":
                corpo = gzip.decompress(corpo)
        destino.write_bytes(corpo)
    return _orientar_para_d3(json.loads(destino.read_text(encoding="utf-8")))


def _orientar_para_d3(geojson: dict) -> dict:
    """Põe o anel externo de cada polígono em sentido horário.

    O `plotly.geo` desenha com d3-geo, que lê a orientação ao contrário do
    padrão GeoJSON (RFC 7946): com o anel anti-horário que o IBGE entrega, o
    polígono vira "o mundo menos a UF" e o mapa sai coberto por um retângulo.
    """
    for feature in geojson["features"]:
        geom = shape(feature["geometry"])
        if geom.geom_type == "Polygon":
            geom = orient(geom, sign=-1.0)
        elif geom.geom_type == "MultiPolygon":
            geom = type(geom)([orient(p, sign=-1.0) for p in geom.geoms])
        feature["geometry"] = mapping(geom)
    return geojson


def malha_uf() -> dict:
    """Um polígono por UF; `codarea` é o `cd_uf`."""
    return _baixar("UF")


def malha_municipios() -> dict:
    """Um polígono por município; `codarea` é o código com DV (7 dígitos)."""
    return _baixar("municipio")
