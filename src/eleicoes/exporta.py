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


def tipo_sql(dtype: object) -> str:
    """Converte um dtype (pyarrow ou pyogrio) no tipo de coluna do Postgres.

    Ids ficam como `text` (ver CLAUDE.md: nunca inteiro, para preservar zeros à
    esquerda) porque já são texto nos arquivos de origem.
    """
    dtype = str(dtype)
    if dtype in ("object", "string", "large_string") or dtype.startswith("str"):
        return "text"
    if dtype.startswith("int"):
        return "integer"
    if dtype in ("double", "float", "float32", "float64"):
        return "double precision"
    if dtype == "bool":
        return "boolean"
    raise ValueError(f"dtype sem tipo SQL mapeado: {dtype}")


def _escreve_texto(caminho: Path, texto: str) -> Path:
    """Escreve com LF: o `table.sh` roda em bash dentro do container Linux."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8", newline="\n")
    return caminho


def gera_artefatos_carga(
    nome_tabela: str,
    colunas_sql: dict[str, str],
    *,
    caminho_fonte: str,
    diretorio_saida: Path,
    requires: list[str],
    chave: str,
    indices: tuple[str, ...] = (),
    tipo_geometria: str | None = None,
    src_sql: str = "",
    schema_pg: str = "tse",
    epsg: int = CRS_SIRGAS2000,
) -> dict[str, Path]:
    """Gera `table.sh`, `schema.sql` e `down.sql` no padrão do `db-manager`.

    Ver `redus-geo-api/packages/db-manager/README.md`: o `table.sh` só declara
    variáveis (quem chama o `ogr2ogr` é o `ogr2pg`), e o `schema.sql` cria a
    tabela já tipada, que o `ogr2pg` depois só alimenta.

    Args:
        nome_tabela: nome da tabela no schema `schema_pg` (ex.:
            `malha_locais_votacao_2026`, `presidente_2026_t1_local`).
        colunas_sql: `{nome_coluna: tipo_sql}`, sem `ogc_fid` e sem `geom`.
        caminho_fonte: caminho do gpkg/parquet relativo à raiz `data-src`.
        diretorio_saida: pasta da tabela dentro de `collections/`.
        requires: caminhos (relativos a `diretorio_saida`) dos `schema.sql`
            que precisam ser aplicados antes.
        chave: coluna de id; recebe índice btree único.
        indices: demais colunas com índice btree (ex.: `cd_mun`).
        tipo_geometria: `Point`, `MultiPolygon`...; `None` para tabela sem
            geometria.
        src_sql: SQL do OGR para escolher colunas da fonte (opcional).
        schema_pg: schema do Postgres (`tse`, conforme convenção da GeoReDUS).
        epsg: SRID de destino, passado como `T_SRS` ao `ogr2pg`.

    Returns:
        `{"table.sh": caminho, "schema.sql": caminho, "down.sql": caminho}`.
    """
    diretorio_saida = Path(diretorio_saida)
    tabela = f"{schema_pg}.{nome_tabela}"

    table_sh = _escreve_texto(
        diretorio_saida / "table.sh",
        f"""# Source
SRC="$S3_SRC_ROOT/{caminho_fonte}"

SRC_LAYER=
SRC_SQL="{src_sql}"

# Target
COLUMN_TYPES=
INDEXES=
SPATIAL_INDEX={"GIST" if tipo_geometria else "NONE"}

# PostgreSQL
PG_SCHEMA={schema_pg}
PG_TABLE_NAME={nome_tabela}
PG_OVERWRITE_BEHAVIOR=skip_non_empty_table

# Other
T_SRS={epsg}
SKIP_FAILURES=n
GROUP_TRANSACTIONS=1000
PRELUDE_STATEMENTS=""
""",
    )

    linhas = ["ogc_fid INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY"]
    linhas += [f"{col} {tipo}" for col, tipo in colunas_sql.items()]
    if tipo_geometria:
        linhas.append(f"geom geometry({tipo_geometria}, {epsg})")

    sql_indices = [
        f"CREATE UNIQUE INDEX IF NOT EXISTS {nome_tabela}_{chave}\n"
        f"  ON {tabela} USING BTREE ({chave});"
    ]
    sql_indices += [
        f"CREATE INDEX IF NOT EXISTS {nome_tabela}_{col}\n  ON {tabela} USING BTREE ({col});"
        for col in indices
    ]
    if tipo_geometria:
        sql_indices.append(
            f"CREATE INDEX IF NOT EXISTS {nome_tabela}_geom\n  ON {tabela} USING GIST (geom);"
        )

    schema_sql = _escreve_texto(
        diretorio_saida / "schema.sql",
        "".join(f"-- requires: {req}\n" for req in requires)
        + f"\nCREATE TABLE IF NOT EXISTS {tabela} (\n  "
        + ",\n  ".join(linhas)
        + "\n);\n\n"
        + "\n".join(sql_indices)
        + "\n",
    )

    down_sql = _escreve_texto(diretorio_saida / "down.sql", f"DROP TABLE IF EXISTS {tabela};\n")

    return {"table.sh": table_sh, "schema.sql": schema_sql, "down.sql": down_sql}


def gera_view_publica(
    nome_view: str,
    colunas_select: list[str],
    *,
    from_sql: str,
    diretorio_saida: Path,
    requires: list[str],
    schema_pg: str = "public_read_api",
) -> dict[str, Path]:
    """Gera o `schema.sql` e o `down.sql` de uma view em `public_read_api`.

    A view junta a malha com os resultados pelo id (join simples, no modelo de
    `public_read_api/cem/malha_estabelecimentos_saude_2022/schema.sql`) e é o
    que o Martin (tiles) e o PostgREST (API) enxergam.

    Args:
        nome_view: nome da view (ex.: `tse_malha_zona_mun_2022_t2`).
        colunas_select: expressões do `SELECT`, já com o alias da tabela.
        from_sql: cláusula `FROM ... LEFT JOIN ...`, sem o ponto e vírgula.
        diretorio_saida: pasta da view dentro de `collections/public_read_api/`.
        requires: caminhos (relativos a `diretorio_saida`) dos `schema.sql`
            das tabelas usadas.
        schema_pg: schema exposto pela API.

    Returns:
        `{"schema.sql": caminho, "down.sql": caminho}`.
    """
    diretorio_saida = Path(diretorio_saida)
    view = f"{schema_pg}.{nome_view}"

    schema_sql = _escreve_texto(
        diretorio_saida / "schema.sql",
        "".join(f"-- requires: {req}\n" for req in requires)
        + f"\nDROP VIEW IF EXISTS {view};\n\n"
        + f"CREATE OR REPLACE VIEW {view} AS\nSELECT\n  "
        + ",\n  ".join(colunas_select)
        + f"\n{from_sql};\n\n"
        + f"GRANT SELECT ON\n  {view}\nTO\n  mvt_system_reader,\n  pgrst_public_anonymous;\n",
    )
    down_sql = _escreve_texto(diretorio_saida / "down.sql", f"DROP VIEW IF EXISTS {view};\n")

    return {"schema.sql": schema_sql, "down.sql": down_sql}


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
