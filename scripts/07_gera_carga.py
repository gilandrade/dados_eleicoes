#!/usr/bin/env python
"""Gera os artefatos de carga na GeoReDUS: zona × município e locais de votação.

Para cada ano, copia as geometrias e os resultados para `data-src/tse/eleicoes/`
e escreve, em `collections/`, o `table.sh`/`schema.sql`/`down.sql` de cada
tabela do schema `tse` e uma view por ano × turno em `public_read_api`, que
junta a malha com os resultados pelo id (join simples — ver PLAN.md > Etapa 05).

A camada por setor não entra aqui: ela reaproveita a malha de setores e a RPC
`cem_censo_2022_data_tile` que já existem no banco.

Uso:
    python scripts/07_gera_carga.py --anos 2018 2022 \
        --saida ../redus-geo-api/packages/db-manager
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pyarrow.parquet as pq
import pyogrio

from eleicoes import exporta
from eleicoes.config import CARGA_DIR, GEOMETRIAS_DIR, PROCESSADO_DIR, RESULTADOS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_PG = "tse"
FONTE_RAIZ = "tse/eleicoes"  # dentro de data-src (local e S3)
TURNO_MUDANCA = 2  # a tabela de mudança só existe para o 2º turno

# Colunas de `locais_{ano}.parquet` que vão para o banco: só o que a malha de
# pontos não tem. `latitude`/`longitude` ficam de fora (coordenada bruta do
# TSE, antes da validação da etapa 02).
COLUNAS_LOCAIS = (
    "id_local",
    "cd_mun",
    "sg_uf",
    "nr_zona",
    "nm_local_votacao",
    "ds_endereco",
    "nm_bairro",
    "nr_cep",
    "ds_tipo_local",
)

# O gpkg de pontos não traz `id_zona_mun`; mesma regra de `resultados.py`.
SQL_ID_ZONA_MUN = "malha.cd_mun || lpad(malha.nr_zona::text, 4, '0')"


# ==== Leitura dos esquemas ====================================================


def colunas_parquet(caminho: Path) -> dict[str, str]:
    schema = pq.read_schema(caminho)
    return {nome: exporta.tipo_sql(tipo) for nome, tipo in zip(schema.names, schema.types)}


def colunas_gpkg(caminho: Path) -> tuple[dict[str, str], str]:
    info = pyogrio.read_info(caminho)
    colunas = {nome: exporta.tipo_sql(tipo) for nome, tipo in zip(info["fields"], info["dtypes"])}
    return colunas, info["geometry_type"]


def exige(caminho: Path) -> Path:
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode as etapas anteriores primeiro.")
    return caminho


# ==== Escrita =================================================================


def copia_fonte(origem: Path, saida: Path, subpasta: str) -> str:
    """Copia o arquivo para `data-src` e devolve o caminho relativo a essa raiz."""
    relativo = f"{FONTE_RAIZ}/{subpasta}/{origem.name}"
    destino = saida / "data-src" / relativo
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, destino)
    return relativo


def requires(de: Path, *schemas: Path) -> list[str]:
    return [Path(os.path.relpath(schema, de)).as_posix() for schema in schemas]


def gera_tabela(
    nome_tabela: str,
    pasta: Path,
    colunas: dict[str, str],
    *,
    caminho_fonte: str,
    chave: str,
    schema_raiz: Path,
    **kwargs,
) -> Path:
    exporta.gera_artefatos_carga(
        nome_tabela,
        colunas,
        caminho_fonte=caminho_fonte,
        diretorio_saida=pasta,
        requires=requires(pasta, schema_raiz),
        chave=chave,
        schema_pg=SCHEMA_PG,
        **kwargs,
    )
    return pasta / "schema.sql"


def gera_ano(ano: int, turnos: list[int], saida: Path, schema_raiz: Path) -> dict[str, Path]:
    """Gera as tabelas e as views de um ano; devolve os `schema.sql` das malhas."""
    dir_tse = saida / "collections" / SCHEMA_PG
    dir_api = saida / "collections" / "public_read_api"

    # ---- Malhas -----------------------------------------------------------
    gpkg_zona = exige(GEOMETRIAS_DIR / f"geom_zona_mun_{ano}.gpkg")
    colunas_zona, geom_zona = colunas_gpkg(gpkg_zona)
    schema_zona = gera_tabela(
        f"malha_zona_mun_{ano}",
        dir_tse / "malha" / f"zona_mun_{ano}",
        colunas_zona,
        caminho_fonte=copia_fonte(gpkg_zona, saida, str(ano)),
        chave="id_zona_mun",
        indices=("cd_mun",),
        tipo_geometria=geom_zona,
        schema_raiz=schema_raiz,
    )

    gpkg_locais = exige(GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}.gpkg")
    colunas_pontos, geom_pontos = colunas_gpkg(gpkg_locais)
    schema_pontos = gera_tabela(
        f"malha_locais_votacao_{ano}",
        dir_tse / "malha" / f"locais_votacao_{ano}",
        colunas_pontos,
        caminho_fonte=copia_fonte(gpkg_locais, saida, str(ano)),
        chave="id_local",
        indices=("cd_mun",),
        tipo_geometria=geom_pontos,
        schema_raiz=schema_raiz,
    )

    # ---- Nome e endereço dos locais ---------------------------------------
    parquet_locais = exige(PROCESSADO_DIR / f"locais_{ano}.parquet")
    tipos_locais = colunas_parquet(parquet_locais)
    schema_locais = gera_tabela(
        f"locais_votacao_{ano}",
        dir_tse / f"locais_votacao_{ano}",
        {col: tipos_locais[col] for col in COLUNAS_LOCAIS},
        caminho_fonte=copia_fonte(parquet_locais, saida, str(ano)),
        chave="id_local",
        src_sql=f"SELECT {', '.join(COLUNAS_LOCAIS)} FROM {parquet_locais.stem}",
        schema_raiz=schema_raiz,
    )

    # ---- Resultados e views, por turno ------------------------------------
    for turno in turnos:
        eleicao = f"presidente_{ano}_t{turno}"
        schemas_res: dict[str, Path] = {}
        colunas_res: dict[str, list[str]] = {}
        for nivel, chave in (("zona_mun", "id_zona_mun"), ("local", "id_local")):
            parquet = exige(RESULTADOS_DIR / f"res_{eleicao}_{nivel}.parquet")
            colunas = colunas_parquet(parquet)
            schemas_res[nivel] = gera_tabela(
                f"{eleicao}_{nivel}",
                dir_tse / eleicao / nivel,
                colunas,
                caminho_fonte=copia_fonte(parquet, saida, str(ano)),
                chave=chave,
                schema_raiz=schema_raiz,
            )
            colunas_res[nivel] = [f"res.{col}" for col in colunas if col != chave]

        pasta = dir_api / SCHEMA_PG / f"malha_zona_mun_{ano}_t{turno}"
        exporta.gera_view_publica(
            f"{SCHEMA_PG}_malha_zona_mun_{ano}_t{turno}",
            [
                "malha.id_zona_mun AS id",
                "malha.id_zona_mun",
                "malha.cd_mun",
                "malha.nr_zona",
                "malha.n_zonas_mun",
                "malha.geom_estimada",
                "malha.geom",
                *colunas_res["zona_mun"],
            ],
            from_sql=(
                f"FROM\n  {SCHEMA_PG}.malha_zona_mun_{ano} AS malha\n"
                f"  LEFT JOIN {SCHEMA_PG}.{eleicao}_zona_mun AS res\n"
                "    ON malha.id_zona_mun = res.id_zona_mun"
            ),
            diretorio_saida=pasta,
            requires=requires(pasta, dir_api / "schema.sql", schema_zona, schemas_res["zona_mun"]),
        )

        pasta = dir_api / SCHEMA_PG / f"malha_locais_votacao_{ano}_t{turno}"
        exporta.gera_view_publica(
            f"{SCHEMA_PG}_malha_locais_votacao_{ano}_t{turno}",
            [
                "malha.id_local AS id",
                "malha.id_local",
                "malha.cd_mun",
                f"{SQL_ID_ZONA_MUN} AS id_zona_mun",
                "malha.nr_zona",
                "malha.fonte_coord",
                "malha.precisao",
                "malha.flag_especial",
                "malha.flag_sem_coord",
                "malha.qt_eleitores",
                "loc.nm_local_votacao",
                "loc.ds_endereco",
                "loc.nm_bairro",
                "loc.nr_cep",
                "loc.ds_tipo_local",
                "malha.geom",
                *colunas_res["local"],
            ],
            from_sql=(
                f"FROM\n  {SCHEMA_PG}.malha_locais_votacao_{ano} AS malha\n"
                f"  LEFT JOIN {SCHEMA_PG}.locais_votacao_{ano} AS loc\n"
                "    ON malha.id_local = loc.id_local\n"
                f"  LEFT JOIN {SCHEMA_PG}.{eleicao}_local AS res\n"
                "    ON malha.id_local = res.id_local"
            ),
            diretorio_saida=pasta,
            requires=requires(
                pasta, dir_api / "schema.sql", schema_pontos, schema_locais, schemas_res["local"]
            ),
        )
        logger.info("%d, turno %d: tabelas e views geradas", ano, turno)

    return {"zona_mun": schema_zona}


def gera_mudanca(
    ano_antes: int, ano_depois: int, saida: Path, schema_raiz: Path, schema_zona: Path
) -> None:
    """Tabela e view da mudança de votação (ids e malha do ano mais recente)."""
    dir_tse = saida / "collections" / SCHEMA_PG
    dir_api = saida / "collections" / "public_read_api"
    nome = f"presidente_mudanca_{ano_antes}_{ano_depois}_t{TURNO_MUDANCA}"

    parquet = RESULTADOS_DIR / (
        f"mudanca_presidente_{ano_antes}_{ano_depois}_t{TURNO_MUDANCA}_zona_mun.parquet"
    )
    if not parquet.exists():
        logger.warning("%s não existe — mudança %d -> %d fora da carga", parquet.name, ano_antes, ano_depois)
        return

    colunas = colunas_parquet(parquet)
    schema_mudanca = gera_tabela(
        f"{nome}_zona_mun",
        dir_tse / nome / "zona_mun",
        colunas,
        caminho_fonte=copia_fonte(parquet, saida, str(ano_depois)),
        chave="id_zona_mun",
        schema_raiz=schema_raiz,
    )

    pasta = dir_api / SCHEMA_PG / f"malha_zona_mun_mudanca_{ano_antes}_{ano_depois}_t{TURNO_MUDANCA}"
    exporta.gera_view_publica(
        f"{SCHEMA_PG}_malha_zona_mun_mudanca_{ano_antes}_{ano_depois}_t{TURNO_MUDANCA}",
        [
            "malha.id_zona_mun AS id",
            "malha.id_zona_mun",
            "malha.cd_mun",
            "malha.nr_zona",
            "malha.n_zonas_mun",
            "malha.geom_estimada",
            "malha.geom",
            *[f"res.{col}" for col in colunas if col != "id_zona_mun"],
        ],
        from_sql=(
            f"FROM\n  {SCHEMA_PG}.malha_zona_mun_{ano_depois} AS malha\n"
            f"  LEFT JOIN {SCHEMA_PG}.{nome}_zona_mun AS res\n"
            "    ON malha.id_zona_mun = res.id_zona_mun"
        ),
        diretorio_saida=pasta,
        requires=requires(pasta, dir_api / "schema.sql", schema_zona, schema_mudanca),
    )
    logger.info("Mudança %d -> %d: tabela e view geradas", ano_antes, ano_depois)


def main(anos: list[int], turnos: list[int], saida: Path) -> None:
    saida = saida.resolve()
    dir_tse = saida / "collections" / SCHEMA_PG
    logger.info("Artefatos de carga em %s", saida)

    schema_raiz = exporta._escreve_texto(
        dir_tse / "schema.sql",
        f"-- requires: ../extensions/postgis/schema.sql\n\nCREATE SCHEMA IF NOT EXISTS {SCHEMA_PG};\n",
    )
    exporta._escreve_texto(
        dir_tse / "down.sql", f"-- migrate:down\nDROP SCHEMA IF EXISTS {SCHEMA_PG};\n"
    )

    schemas_zona = {ano: gera_ano(ano, turnos, saida, schema_raiz)["zona_mun"] for ano in anos}

    if TURNO_MUDANCA in turnos:
        for ano_antes, ano_depois in zip(sorted(anos), sorted(anos)[1:]):
            gera_mudanca(ano_antes, ano_depois, saida, schema_raiz, schemas_zona[ano_depois])

    logger.info("Concluído.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anos", type=int, nargs="+", required=True)
    parser.add_argument("--turnos", type=int, nargs="+", default=[1, 2])
    parser.add_argument(
        "--saida",
        type=Path,
        default=CARGA_DIR,
        help="raiz com `collections/` e `data-src/` (ex.: redus-geo-api/packages/db-manager)",
    )
    args = parser.parse_args()
    main(args.anos, args.turnos, args.saida)
