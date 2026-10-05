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
    CNEFE_MUN_DIR,
    CRS_SIRGAS2000,
    GEOMETRIAS_DIR,
    POP18_SETOR_PARQUET,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
    SETORES_2022_GPKG,
    SETORES_MUN_DIR,
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

    Lê os `cnefe_{cd_mun}.parquet` de `CNEFE_MUN_DIR` (gerados por
    `scripts/00b_insumos_brasil.R` ou `09_insumos_municipio.R`).
    """
    arquivos = sorted(CNEFE_MUN_DIR.glob("cnefe_[0-9]*.parquet"))
    if not arquivos:
        raise FileNotFoundError(
            f"Nenhum cnefe_<cd_mun>.parquet em {CNEFE_MUN_DIR}. Rode "
            "scripts/00b_insumos_brasil.R (Brasil) ou 09_insumos_municipio.R (um município)."
        )
    if cd_mun_filtro:
        arquivos = [a for a in arquivos if a.stem.removeprefix("cnefe_") in set(cd_mun_filtro)]

    especies = ", ".join(str(e) for e in CNEFE_ESPECIES_DOMICILIO)
    lista = ", ".join("'" + str(a).replace("\\", "/").replace("'", "''") + "'" for a in arquivos)
    df = duckdb.connect().sql(
        f"select cd_mun, cd_setor, cod_especie, latitude, longitude "
        f"from read_parquet([{lista}]) where cod_especie in ({especies})"
    ).df()
    return gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs=CRS_SIRGAS2000
    )


def carrega_locais_elegiveis(
    ano: int, cd_mun_filtro: list[str] | None = None
) -> gpd.GeoDataFrame:
    """Lê `locais_votacao_{ano}.gpkg` (saída da etapa 02, R) e filtra os
    locais elegíveis para a distribuição (sem voto em trânsito nem especiais).

    Sem a camada nacional, aceita a de cada município pedido
    (`geom_locais_votacao_{ano}_{cd_mun}.gpkg`, de
    `scripts/09_insumos_municipio.R`): mesma estrutura, mas só com a
    coordenada do TSE validada, sem a cadeia de fallback da etapa 02.
    """
    caminho = GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}.gpkg"
    if caminho.exists():
        gdf = gpd.read_file(caminho)
        return gdf.loc[~gdf["flag_especial"]].copy()

    caminhos = [
        GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}_{c}.gpkg" for c in cd_mun_filtro or []
    ]
    if not caminhos or not all(c.exists() for c in caminhos):
        raise FileNotFoundError(
            f"{caminho} não existe. Rode 01_locais_votacao.qmd e "
            "02_geocodificacao_locais.qmd (R) antes desta etapa, ou "
            "scripts/09_insumos_municipio.R para um município só."
        )
    gdf = pd.concat([gpd.read_file(c) for c in caminhos], ignore_index=True)
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
    """Lê a malha de setores 2022 (geobr, ver config.py).

    Args:
        cd_mun_filtro: se informado, lê só esses municípios (via `where` do
            GDAL, sem materializar o Brasil inteiro — o arquivo tem ~1,5 GB).
            Se todos já têm `setores_{cd_mun}.gpkg` em `SETORES_MUN_DIR`
            (`scripts/09_insumos_municipio.R`), lê esses arquivos.
    """
    if cd_mun_filtro:
        caminhos = [SETORES_MUN_DIR / f"setores_{c}.gpkg" for c in cd_mun_filtro]
        if all(c.exists() for c in caminhos):
            gdf = pd.concat([gpd.read_file(c) for c in caminhos], ignore_index=True)
            return gdf.rename(columns={"CD_SETOR": "cd_setor", "CD_MUN": "cd_mun"})

    caminho = SETORES_2022_GPKG
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe. Rode scripts/00b_insumos_brasil.R."
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

    locais = carrega_locais_elegiveis(ano, cd_mun_filtro)
    if cd_mun_filtro:
        locais = locais.loc[locais["cd_mun"].isin(cd_mun_filtro)].copy()
    # Locais no centróide do município (sem coordenada real) não atraem
    # domicílios: a posição deles não é a do local. Continuam na camada de
    # zona × município (contam na zona e nos inscritos dela), e os votos deles
    # ficam em local/zona_mun/município, fora do setor — como os especiais.
    sem_coord = locais["flag_sem_coord"].astype(bool)
    locais_alocacao = locais.loc[~sem_coord].copy()
    logger.info(
        "%d locais no centróide do município ficam fora da alocação (%d inscritos).",
        int(sem_coord.sum()), int(locais.loc[sem_coord, "qt_eleitores"].sum()),
    )
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
    domicilios["id_local"] = alocacao.aloca_domicilios_a_locais(domicilios, locais_alocacao)

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
    areas_locais = alocacao.poligonos_voronoi_por_municipio(locais_alocacao, municipios)

    logger.info("Calculando o diagnóstico de razão de eleitores...")
    diagnostico = alocacao.diagnostico_razao_eleitores(locais_alocacao, pesos)

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
