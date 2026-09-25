#!/usr/bin/env python
"""Etapa 03 — pesos de alocação (roda 1x por ano, antes da eleição).

Lê os locais de votação georreferenciados (etapa 02, R) e o CNEFE 2022
(repo irmão `cnefe-etl`), e produz:

  - `pesos_local_setor_{ano}.parquet`  (matriz de pesos local -> setor)
  - `geom_areas_locais_{ano}.gpkg`     (Voronoi dos locais, recortado por município)
  - `geom_zona_mun_{ano}.gpkg`         (zona x município, exata ou estimada)
  - `diagnostico_alocacao_{ano}.parquet` (razao_eleitores por local)

Ver PLAN.md > Etapa 03 e CLAUDE.md > Architecture.

Uso:
    python scripts/03_pesos_alocacao.py --ano 2026
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd
import pandas as pd

from eleicoes import alocacao
from eleicoes.config import (
    CNEFE_ESPECIES_DOMICILIO,
    CNEFE_POINTS_PARQUET,
    GEOMETRIAS_DIR,
    POP18_SETOR_PARQUET,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def carrega_domicilios_cnefe() -> gpd.GeoDataFrame:
    """Lê `cnefe_points_all_mun.parquet` e filtra às espécies de domicílio.

    A geometria ali é WKB cru sem metadados GeoParquet (ver
    `alocacao.decodifica_wkb` e `cnefe-etl/CLAUDE.md`).
    """
    if not CNEFE_POINTS_PARQUET.exists():
        raise FileNotFoundError(
            f"CNEFE não encontrado em {CNEFE_POINTS_PARQUET}. Rode o pipeline de "
            "cnefe-etl primeiro (repo irmão), ou ajuste CNEFE_POINTS_PARQUET em "
            "src/eleicoes/config.py."
        )
    df = pd.read_parquet(CNEFE_POINTS_PARQUET)
    gdf = alocacao.decodifica_wkb(df)
    gdf = gdf.rename(
        columns={"COD_MUNICIPIO": "cd_mun", "COD_SETOR": "cd_setor", "COD_ESPECIE": "cod_especie"}
    )
    return gdf.loc[gdf["cod_especie"].astype(int).isin(CNEFE_ESPECIES_DOMICILIO)].copy()


def carrega_locais_elegiveis(ano: int) -> gpd.GeoDataFrame:
    """Lê `locais_votacao_{ano}.gpkg` (saída da etapa 02, R) e filtra os
    locais elegíveis para a distribuição (sem voto em trânsito nem especiais).
    """
    caminho = GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}.gpkg"
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe. Rode 01_locais_votacao.qmd e "
            "02_geocodificacao_locais.qmd (R) antes desta etapa."
        )
    gdf = gpd.read_file(caminho)
    return gdf.loc[~gdf["flag_especial"]].copy()


def carrega_pop18() -> pd.DataFrame:
    """Lê `pop18_setor.parquet`, calculado em R por `00_auxiliares.qmd`.

    Python não recalcula essa conta a partir dos CSVs do `cn22-pop` — só o R
    lê o Censo (ver `alocacao.pop18_por_setor` para a versão de referência em
    Python, não usada aqui).
    """
    if not POP18_SETOR_PARQUET.exists():
        raise FileNotFoundError(
            f"{POP18_SETOR_PARQUET} não existe — rode 00_auxiliares.qmd (R) primeiro."
        )
    return pd.read_parquet(POP18_SETOR_PARQUET)


def carrega_limites_municipio(ano: int) -> gpd.GeoDataFrame:
    caminho = PROCESSADO_DIR / "aux" / "municipios.gpkg"
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe. Rode 00_auxiliares.qmd (R) primeiro — ele "
            "grava os municípios via geobr 1.9.1."
        )
    return gpd.read_file(caminho)


def carrega_setores() -> gpd.GeoDataFrame:
    caminho = PROCESSADO_DIR / "aux" / "setores_2022.gpkg"
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe. Rode 00_auxiliares.qmd (R) primeiro."
        )
    return gpd.read_file(caminho)


def main(ano: int) -> None:
    logger.info("Etapa 03 — pesos de alocação, ano %d", ano)

    locais = carrega_locais_elegiveis(ano)
    domicilios = carrega_domicilios_cnefe()
    pop18 = carrega_pop18()
    municipios = carrega_limites_municipio(ano)

    logger.info(
        "%d locais elegíveis, %d domicílios, %d setores com pop18",
        len(locais), len(domicilios), len(pop18),
    )

    logger.info("Alocando domicílios ao local mais próximo (por município)...")
    domicilios["id_local"] = alocacao.aloca_domicilios_a_locais(domicilios, locais)

    n_sem_local = domicilios["id_local"].isna().sum()
    if n_sem_local:
        logger.warning(
            "%d domicílios (%.2f%%) sem local elegível no município — "
            "provável município sem CNEFE útil ou sem local elegível.",
            n_sem_local, 100 * n_sem_local / len(domicilios),
        )

    logger.info("Construindo a matriz de pesos local -> setor...")
    pesos = alocacao.constroi_pesos_local_setor(domicilios, pop18)

    logger.info("Gerando os polígonos de Voronoi dos locais (por município)...")
    areas_locais = alocacao.poligonos_voronoi_por_municipio(locais, municipios)

    logger.info("Calculando o diagnóstico de razão de eleitores...")
    diagnostico = alocacao.diagnostico_razao_eleitores(locais, pesos)

    RESULTADOS_DIR.mkdir(parents=True, exist_ok=True)
    GEOMETRIAS_DIR.mkdir(parents=True, exist_ok=True)

    pesos.to_parquet(RESULTADOS_DIR / f"pesos_local_setor_{ano}.parquet", index=False)
    diagnostico.to_parquet(
        RESULTADOS_DIR / f"diagnostico_alocacao_{ano}.parquet", index=False
    )
    areas_locais.to_file(
        GEOMETRIAS_DIR / f"geom_areas_locais_{ano}.gpkg", driver="GPKG"
    )

    logger.info("Construindo a camada zona x município...")
    setores = carrega_setores()
    zona_mun = alocacao.constroi_zona_mun(setores, locais, pesos, municipios)
    zona_mun.to_file(GEOMETRIAS_DIR / f"geom_zona_mun_{ano}.gpkg", driver="GPKG")

    logger.info("Concluído.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, required=True, choices=[2018, 2022, 2026])
    args = parser.parse_args()
    main(args.ano)
