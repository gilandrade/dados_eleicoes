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

import duckdb
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
    SETORES_2022_GPKG,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def carrega_domicilios_cnefe(cd_mun_filtro: list[str] | None = None) -> gpd.GeoDataFrame:
    """Lê `cnefe_points_all_mun.parquet` e filtra às espécies de domicílio.

    A geometria ali é WKB cru sem metadados GeoParquet (ver
    `alocacao.decodifica_wkb` e `cnefe-etl/CLAUDE.md`).

    Duas correções de esquema, confirmadas contra o arquivo real em
    29/09/2026 (ver CLAUDE.md — não presumir de novo sem checar):

    - `COD_MUNICIPIO` é `int32` (ex.: `1100015`), sem o zero à esquerda que o
      resto do pipeline usa como texto em `cd_mun` — convertido com
      `.zfill(7)`.
    - `COD_SETOR` tem um sufixo `"P"` fixo (`"110001505000002P"`, 16
      caracteres) que a malha oficial de setores 2022 não tem (15 dígitos,
      `"110001505000002"`) — removido com `.str[:-1]`. Mesmo removendo, ~20%
      dos setores do CNEFE não existem na malha oficial em nenhum município
      testado (cadastro de endereços numa safra diferente da malha de
      resultados) — ver `qualidade_pop18` na saída de `constroi_pesos_local_setor`.

    Args:
        cd_mun_filtro: se informado, filtra a leitura a esses códigos de
            município (7 dígitos, texto) via DuckDB antes de materializar em
            pandas — evita ler os ~5,8 GB inteiros ao testar em poucos
            municípios.

    O Brasil inteiro tem 111 milhões de linhas no arquivo, das quais ~91
    milhões são domicílio (espécies 1/2) — por isso o filtro de espécie
    também vai na query do DuckDB (não só o de município), mesmo rodando
    nacional: evita materializar em pandas/shapely as ~20 milhões de linhas
    de outras espécies (comércio, indústria etc.) que seriam descartadas
    logo em seguida de qualquer jeito.
    """
    if not CNEFE_POINTS_PARQUET.exists():
        raise FileNotFoundError(
            f"CNEFE não encontrado em {CNEFE_POINTS_PARQUET}. Rode o pipeline de "
            "cnefe-etl primeiro (repo irmão), ou ajuste CNEFE_POINTS_PARQUET em "
            "src/eleicoes/config.py."
        )

    con = duckdb.connect()
    caminho_sql = str(CNEFE_POINTS_PARQUET).replace("'", "''")
    especies = ", ".join(str(e) for e in CNEFE_ESPECIES_DOMICILIO)
    where = [f"COD_ESPECIE in ({especies})"]
    if cd_mun_filtro:
        codigos = ", ".join(str(int(c)) for c in cd_mun_filtro)
        where.append(f"COD_MUNICIPIO in ({codigos})")
    df = con.sql(
        f"select * from read_parquet('{caminho_sql}') where {' and '.join(where)}"
    ).df()

    gdf = alocacao.decodifica_wkb(df)
    gdf["cd_mun"] = gdf["COD_MUNICIPIO"].astype(int).astype(str).str.zfill(7)
    gdf["cd_setor"] = gdf["COD_SETOR"].str[:-1]
    gdf["cod_especie"] = gdf["COD_ESPECIE"]
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


def carrega_setores(cd_mun_filtro: list[str] | None = None) -> gpd.GeoDataFrame:
    """Lê a malha de setores 2022 (repo irmão `process_gtfs`, ver config.py).

    Args:
        cd_mun_filtro: se informado, lê só esses municípios (via `where` do
            GDAL, sem materializar o Brasil inteiro — o arquivo tem ~1,5 GB).
    """
    caminho = SETORES_2022_GPKG
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe. Ajuste SETORES_2022_GPKG em "
            "src/eleicoes/config.py para onde a malha de setores 2022 estiver."
        )
    if cd_mun_filtro:
        where = "CD_MUN IN (" + ", ".join(f"'{c}'" for c in cd_mun_filtro) + ")"
        gdf = gpd.read_file(caminho, where=where)
    else:
        gdf = gpd.read_file(caminho)
    return gdf.rename(columns={"CD_SETOR": "cd_setor", "CD_MUN": "cd_mun"})


def main(ano: int, cd_mun_filtro: list[str] | None = None) -> None:
    logger.info("Etapa 03 — pesos de alocação, ano %d%s", ano,
                f" (só {cd_mun_filtro})" if cd_mun_filtro else "")

    locais = carrega_locais_elegiveis(ano)
    if cd_mun_filtro:
        locais = locais.loc[locais["cd_mun"].isin(cd_mun_filtro)].copy()
    domicilios = carrega_domicilios_cnefe(cd_mun_filtro)
    pop18 = carrega_pop18()
    municipios = carrega_limites_municipio(ano)
    if cd_mun_filtro:
        municipios = municipios.loc[municipios["cd_mun"].isin(cd_mun_filtro)].copy()

    logger.info(
        "%d locais elegíveis, %d domicílios, %d setores com pop18",
        len(locais), len(domicilios), len(pop18),
    )

    cobertura = alocacao.mede_cobertura_setor(domicilios, pop18)
    pc_invalido_geral = 1 - domicilios["cd_setor"].isin(set(pop18["cd_setor"])).mean()
    logger.info(
        "%.1f%% dos domicílios do CNEFE têm cd_setor fora da malha oficial "
        "(cadastro de endereços numa safra diferente — ver CLAUDE.md).",
        100 * pc_invalido_geral,
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

    sufixo = f"_{'_'.join(cd_mun_filtro)}" if cd_mun_filtro else ""
    pesos.to_parquet(RESULTADOS_DIR / f"pesos_local_setor_{ano}{sufixo}.parquet", index=False)
    diagnostico.to_parquet(
        RESULTADOS_DIR / f"diagnostico_alocacao_{ano}{sufixo}.parquet", index=False
    )
    cobertura.to_parquet(
        RESULTADOS_DIR / f"cobertura_setor_cnefe_{ano}{sufixo}.parquet", index=False
    )
    areas_locais.to_file(
        GEOMETRIAS_DIR / f"geom_areas_locais_{ano}{sufixo}.gpkg", driver="GPKG"
    )

    logger.info("Construindo a camada zona x município...")
    setores = carrega_setores(cd_mun_filtro)
    zona_mun = alocacao.constroi_zona_mun(setores, locais, pesos, municipios)
    zona_mun.to_file(GEOMETRIAS_DIR / f"geom_zona_mun_{ano}{sufixo}.gpkg", driver="GPKG")

    logger.info("Concluído.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, required=True, choices=[2018, 2022, 2026])
    parser.add_argument(
        "--cd-mun", nargs="+", default=None,
        help="testa só nesses municípios (7 dígitos IBGE), em vez do Brasil inteiro",
    )
    args = parser.parse_args()
    main(args.ano, args.cd_mun)
