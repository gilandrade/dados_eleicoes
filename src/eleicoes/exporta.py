"""Exportação: arquivos no modelo GeoReDUS, artefatos de carga e cadastro.

Ver PLAN.md > Modelo de dados e > Etapa 05. Cada função aqui escreve um único
tipo de artefato; a orquestração fica em `scripts/processa_turno.py` e
`scripts/03_pesos_alocacao.py`.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from .config import CRS_SIRGAS2000


def escreve_geometria(gdf: gpd.GeoDataFrame, caminho: Path) -> Path:
    """Escreve um GeoDataFrame em GeoPackage, forçando EPSG:4674.

    Todas as geometrias exportadas para a GeoReDUS usam SIRGAS 2000 (ver
    `redus-geo-api/packages/db-manager`: `T_SRS=4674`).
    """
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if gdf.crs is None:
        raise ValueError(f"GeoDataFrame sem CRS definido antes de escrever {caminho}")
    if gdf.crs.to_epsg() != CRS_SIRGAS2000:
        gdf = gdf.to_crs(CRS_SIRGAS2000)
    gdf.to_file(caminho, driver="GPKG")
    return caminho


def escreve_resultados(df: pd.DataFrame, caminho: Path) -> Path:
    """Escreve uma tabela de resultados em parquet (e um `.csv` irmão)."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(caminho, index=False)
    df.to_csv(caminho.with_suffix(".csv"), index=False)
    return caminho


def gera_artefatos_carga(
    nome_tabela: str,
    schema_sql_colunas: dict[str, str],
    *,
    schema_pg: str = "tse",
    caminho_geometria_ou_dados: str,
    diretorio_saida: Path,
    epsg: int = CRS_SIRGAS2000,
) -> dict[str, Path]:
    """Gera `table.sh`, `schema.sql` e `down.sql` no padrão do `db-manager`.

    Ver `redus-geo-api/packages/db-manager/README.md`: cada tabela carregada
    no banco da GeoReDUS tem esses três arquivos, mais uma view em
    `public_read_api/<fonte>/<tabela>/`, que não é gerada aqui (depende do
    join com a tabela de geometria já carregada — ver a nota metodológica).

    Args:
        nome_tabela: nome da tabela no schema `schema_pg` (ex.:
            `locais_votacao_2026`, `res_presidente_2026_t1_setor`).
        schema_sql_colunas: `{nome_coluna: tipo_sql}` (ex.: `{"id_local": "text"}`).
        schema_pg: schema do Postgres (`tse`, conforme convenção da GeoReDUS).
        caminho_geometria_ou_dados: caminho (relativo ao S3 `data-src`) do
            gpkg/parquet a ser carregado pelo `ogr2pg`.
        diretorio_saida: onde escrever os três arquivos.
        epsg: SRID de destino, passado como `T_SRS` ao `ogr2pg`.

    Returns:
        `{"table.sh": caminho, "schema.sql": caminho, "down.sql": caminho}`.
    """
    diretorio_saida = Path(diretorio_saida) / schema_pg / nome_tabela
    diretorio_saida.mkdir(parents=True, exist_ok=True)

    table_sh = diretorio_saida / "table.sh"
    table_sh.write_text(
        f"""#!/bin/bash
set -euo pipefail
SRC="{caminho_geometria_ou_dados}"
PG_SCHEMA="{schema_pg}"
PG_TABLE_NAME="{nome_tabela}"
T_SRS={epsg}

ogr2ogr -f PostgreSQL \\
    PG:"$PG_CONN" "$SRC" \\
    -nln "$PG_SCHEMA.$PG_TABLE_NAME" \\
    -t_srs "EPSG:$T_SRS" \\
    -lco GEOMETRY_NAME=geom \\
    -overwrite
""",
        encoding="utf-8",
        newline="\n",
    )

    colunas_sql = ",\n    ".join(f"{col} {tipo}" for col, tipo in schema_sql_colunas.items())
    schema_sql = diretorio_saida / "schema.sql"
    schema_sql.write_text(
        f"""create schema if not exists {schema_pg};

create table if not exists {schema_pg}.{nome_tabela} (
    {colunas_sql}
);

create index if not exists {nome_tabela}_geom_idx
    on {schema_pg}.{nome_tabela} using gist (geom);
""",
        encoding="utf-8",
        newline="\n",
    )

    down_sql = diretorio_saida / "down.sql"
    down_sql.write_text(
        f"drop table if exists {schema_pg}.{nome_tabela};\n", encoding="utf-8", newline="\n"
    )

    return {"table.sh": table_sh, "schema.sql": schema_sql, "down.sql": down_sql}


def linha_cadastro(
    *,
    collection_id: str,
    source_table_id: str,
    variable_id: str,
    preset: str,
    year: int,
    indicator_path: str,
    color_scheme: str,
    classification_method: str,
    measure_unit: str,
    metodology_url: str,
) -> dict[str, str]:
    """Monta uma linha no formato da planilha de cadastro da GeoReDUS.

    Ver `georedus-ui/public/georedus/data/cem_censo_2022.csv` para o exemplo
    de referência (colunas replicadas aqui).
    """
    return {
        "collection_id": collection_id,
        "source_table_id": source_table_id,
        "variable_id": variable_id,
        "preset": preset,
        "year": str(year),
        "indicator_path": indicator_path,
        "colorScheme": color_scheme,
        "classificationMethod": classification_method,
        "measure_unit": measure_unit,
        "metodology": metodology_url,
    }


def escreve_cadastro(linhas: list[dict[str, str]], caminho: Path) -> Path:
    """Escreve as linhas de cadastro em CSV, prontas para colar na planilha."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(linhas).to_csv(caminho, index=False)
    return caminho
