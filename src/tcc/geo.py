"""Geoprocessamento: setores censitários do DF e localização das delegacias."""

import geopandas as gpd
import pandas as pd
import polars as pl
from shapely.geometry import Point

from tcc.paths import PLANILHA_TCC, SETORES_GPKG

CRS_WGS84 = "epsg:4326"

# Typo presente na planilha de origem.
COLUNA_COORDENADAS = "Latitude/Logintude"


def carregar_setores() -> gpd.GeoDataFrame:
    """Setores censitários do CD 2022 reprojetados para WGS84."""
    return gpd.read_file(SETORES_GPKG).to_crs(CRS_WGS84)


def dissolver_por_ra(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Une os setores em um polígono por subdistrito (Região Administrativa)."""
    return (
        gdf[["CD_SUBDIST", "NM_SUBDIST", "geometry"]]
        .dissolve(by="CD_SUBDIST")
        .reset_index()
    )


def carregar_delegacias() -> gpd.GeoDataFrame:
    """Delegacias da aba `DPs`, com `lat`/`lon` extraídos da coluna de coordenadas."""
    dp = pd.read_excel(PLANILHA_TCC, sheet_name="DPs")
    coords = dp[COLUNA_COORDENADAS].astype(str).str.split(",", expand=True)
    dp["lat"] = coords[0].str.strip().astype(float)
    dp["lon"] = coords[1].str.strip().astype(float)

    return gpd.GeoDataFrame(
        dp,
        geometry=[Point(lon, lat) for lat, lon in zip(dp.lat, dp.lon)],
        crs=CRS_WGS84,
    )


def delegacias_com_subdistrito(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Delegacias anotadas com o subdistrito que as contém."""
    return gpd.sjoin(
        carregar_delegacias(),
        gdf[["CD_SUBDIST", "NM_SUBDIST", "geometry"]],
        how="left",
        predicate="within",
    ).drop_duplicates(subset="Delegacia")


def agregar_delegacias_por_ra(dp_joined: gpd.GeoDataFrame) -> pl.DataFrame:
    """Contagem e lista de delegacias por subdistrito."""
    agg = (
        dp_joined.groupby("CD_SUBDIST")
        .agg(
            n_delegacias=("Delegacia", "count"),
            delegacias=("Delegacia", lambda x: ", ".join(x)),
        )
        .reset_index()
    )
    return pl.DataFrame(
        {
            "cd_subdist": agg["CD_SUBDIST"].tolist(),
            "n_delegacias": agg["n_delegacias"].tolist(),
            "delegacias": agg["delegacias"].tolist(),
        }
    )


def juntar_ssp_delegacias(ssp: pl.DataFrame, dp_joined: gpd.GeoDataFrame) -> pl.DataFrame:
    """Ocorrências da SSP-DF acrescidas da contagem de delegacias da RA."""
    return ssp.join(agregar_delegacias_por_ra(dp_joined), on="cd_subdist", how="left")


def centroides_por_ra(gdf_ra: gpd.GeoDataFrame) -> dict[str, dict]:
    """Centroide de cada RA, indexado por `CD_SUBDIST`."""
    c = gdf_ra.copy()
    c["lat"] = c.geometry.centroid.y
    c["lon"] = c.geometry.centroid.x
    return {
        row["CD_SUBDIST"]: {"lat": row["lat"], "lon": row["lon"], "nm": row["NM_SUBDIST"]}
        for _, row in c.iterrows()
    }
