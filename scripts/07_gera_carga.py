#!/usr/bin/env python
"""Gera os artefatos de carga na GeoReDUS: zona × município e locais de votação.

Para cada ano, copia as geometrias e os resultados para `data-src/tse/eleicoes/`
e escreve, em `collections/`, o `table.sh`/`schema.sql`/`down.sql` de cada
tabela do schema `tse` e uma view por ano × turno em `public_read_api`, que
junta a malha com os resultados pelo id (join simples — ver PLAN.md > Etapa 05).

A camada por setor não entra aqui: ela reaproveita a malha de setores e a RPC
`cem_censo_2022_data_tile` que já existem no banco.

Uso:
    python scripts/07_gera_carga.py --anos 2018 2022 2026 \
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

from eleicoes import cores, exporta
from eleicoes.config import (
    CARGA_DIR,
    CODIGOS_NAO_CANDIDATO,
    GEOMETRIAS_DIR,
    NR_BOLSONARO_POR_ANO,
    NR_PT,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_PG = "tse"
FONTE_RAIZ = "tse/eleicoes"  # dentro de data-src (local e S3)

# Colunas do parquet de mudança que não vão para o banco: são do mapa "de onde
# veio a mudança" (sobre o eleitorado apto), que ainda não foi validado.
PREFIXOS_FORA_DA_MUDANCA = ("tipo_mudanca", "pc_aptos_", "var_pc_aptos_", "var_rel_")

# Nome do município e UF para os tooltips (o schema `tse` só guarda `cd_mun`).
TABELA_MUNICIPIO = "ibge.malha_br_municipio_2024"
SCHEMA_MUNICIPIO = Path("ibge") / "malha" / "br_municipio_2024" / "schema.sql"
JOIN_MUNICIPIO = f"  LEFT JOIN {TABELA_MUNICIPIO} AS mun\n    ON malha.cd_mun = mun.cd_mun"

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


# ==== Colunas derivadas em SQL ================================================
#
# As views carregam a classe de cor já calculada, com os mesmos cortes do site
# (`cores.py`): na GeoReDUS as camadas são pintadas por classe, com os presets
# categóricos. Os presets contínuos da UI não servem aqui: o de pontos não tem
# cor por valor, e o de polígonos não permite voltar aos cortes fixos depois de
# trocar o método de classificação.


def sql_cortes(quebras: tuple[float, ...]) -> str:
    return f"ARRAY[{', '.join(str(q) for q in quebras)}]::double precision[]"


def sql_classe_divergente(expr: str, quebras: tuple[float, ...]) -> str:
    """Equivalente SQL de `cores.classifica_divergente`; sem dado fica `NULL`."""
    n = len(quebras) + 1
    passo = f"width_bucket(abs({expr}), {sql_cortes(quebras)})"
    return (
        f"CASE\n    WHEN {expr} > 0 THEN {n - 1} - {passo}\n"
        f"    WHEN {expr} < 0 THEN {n + 1} + {passo}\n"
        f"    WHEN {expr} = 0 THEN {n}\n  END"
    )


def sql_classe_crescimento(lado: str, magnitude: str) -> str:
    """Equivalente SQL de `cores.classifica_crescimento`; sem dado fica `NULL`."""
    n = len(cores.QUEBRAS_MUDANCA) + 1
    # `greatest` ignora NULL: magnitude ausente ou negativa cai na classe mais clara.
    passo = f"width_bucket(greatest({magnitude}, 0), {sql_cortes(cores.QUEBRAS_MUDANCA)})"
    return (
        f"CASE {lado}\n    WHEN 'pt' THEN {n - 1} - {passo}\n"
        f"    WHEN 'bolsonaro' THEN {n + 1} + {passo}\n"
        f"    WHEN 'empate' THEN {n}\n"
        f"    WHEN 'terceiro' THEN {2 * n + 1}"
        f" + (coalesce({magnitude}, 0) > {cores.QUEBRA_TERCEIRO})::int\n"
        "  END"
    )


def sql_classe_sequencial(expr: str, quebras: tuple[float, ...]) -> str:
    """Equivalente SQL de `cores.classifica_sequencial`; sem dado fica `NULL`."""
    return f"width_bucket({expr}, {sql_cortes(quebras)})"


# Os percentuais das classes são recalculados das contagens, com as fórmulas de
# `resultados.calcula_indicadores`, em vez de lidos das colunas `pc_*`: o
# `ogr2ogr` grava decimais com 15 dígitos significativos, e num empate exato
# com um corte (abstenção de 21,0%, margem de 50,0 p.p.) essa diferença na 16ª
# casa troca a classe. A divisão de inteiros dá o mesmo double do pandas.


def sql_fracao(numerador: str, denominador: str) -> str:
    """Divisão em `double precision`; `NULL` com denominador zero (o NaN do pandas)."""
    return f"({numerador})::double precision / NULLIF({denominador}, 0)"


def sql_qt_votos(colunas: dict[str, str], *, candidatos: bool) -> str:
    """Soma dos votos em candidatos, ou em branco, nulo e anulado."""
    cols = [
        col
        for col in colunas
        if col.startswith("qt_votos_")
        and (int(col.removeprefix("qt_votos_")) not in CODIGOS_NAO_CANDIDATO) == candidatos
    ]
    return " + ".join(f"res.{col}" for col in cols)


def sql_qt_validos(colunas: dict[str, str]) -> str:
    return sql_qt_votos(colunas, candidatos=True)


def sql_pc_abstencao() -> str:
    return f"1 - {sql_fracao('res.qt_comparecimento', 'res.qt_aptos')}"


def sql_pc_brancos_nulos(colunas: dict[str, str]) -> str:
    return sql_fracao(sql_qt_votos(colunas, candidatos=False), "res.qt_comparecimento")


def colunas_margem(ano: int, colunas: dict[str, str]) -> list[str]:
    """`margem_pt_bolsonaro` (Lula/Haddad − Bolsonaro nos votos válidos) e `classe_margem`."""
    if ano not in NR_BOLSONARO_POR_ANO:
        logger.warning("%d: sem número de Bolsonaro — views sem margem e classe_margem", ano)
        return []
    validos = f"({sql_qt_validos(colunas)})"
    margem = (
        f"{sql_fracao(f'res.qt_votos_{NR_PT}', validos)}"
        f" - {sql_fracao(f'res.qt_votos_{NR_BOLSONARO_POR_ANO[ano]}', validos)}"
    )
    return [
        f"{margem} AS margem_pt_bolsonaro",
        f"{sql_classe_divergente(f'({margem})', cores.QUEBRAS_MARGEM)} AS classe_margem",
    ]


def sql_pct(coluna: str) -> str:
    """Percentual em escala 0–100, para quem baixa os dados da camada."""
    return f"round((100 * res.{coluna})::numeric, 2)::double precision"


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
    schema_municipio = saida / "collections" / SCHEMA_MUNICIPIO
    for turno in turnos:
        eleicao = f"presidente_{ano}_t{turno}"
        if not (RESULTADOS_DIR / f"res_{eleicao}_local.parquet").exists():
            logger.warning("%d, turno %d: sem resultados — fora da carga", ano, turno)
            continue
        schemas_res: dict[str, Path] = {}
        colunas_res: dict[str, list[str]] = {}
        tipos_res: dict[str, dict[str, str]] = {}
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
            tipos_res[nivel] = colunas
            colunas_res[nivel] = [f"res.{col}" for col in colunas if col != chave]

        pasta = dir_api / SCHEMA_PG / f"malha_zona_mun_{ano}_t{turno}"
        exporta.gera_view_publica(
            f"{SCHEMA_PG}_malha_zona_mun_{ano}_t{turno}",
            [
                "malha.id_zona_mun AS id",
                "malha.id_zona_mun",
                "malha.cd_mun",
                "mun.nm_mun",
                "mun.sigla_uf",
                "malha.nr_zona",
                "malha.n_zonas_mun",
                "malha.geom_estimada",
                "malha.geom",
                *colunas_res["zona_mun"],
                *colunas_margem(ano, tipos_res["zona_mun"]),
            ],
            from_sql=(
                f"FROM\n  {SCHEMA_PG}.malha_zona_mun_{ano} AS malha\n"
                f"  LEFT JOIN {SCHEMA_PG}.{eleicao}_zona_mun AS res\n"
                "    ON malha.id_zona_mun = res.id_zona_mun\n"
                f"{JOIN_MUNICIPIO}"
            ),
            diretorio_saida=pasta,
            requires=requires(
                pasta, dir_api / "schema.sql", schema_zona, schemas_res["zona_mun"], schema_municipio
            ),
        )

        # Locais sem coordenada confiável ficam no centróide do município; como
        # no site, não são desenhados (os presets genéricos não têm filtro).
        pasta = dir_api / SCHEMA_PG / f"malha_locais_votacao_{ano}_t{turno}"
        exporta.gera_view_publica(
            f"{SCHEMA_PG}_malha_locais_votacao_{ano}_t{turno}",
            [
                "malha.id_local AS id",
                "malha.id_local",
                "malha.cd_mun",
                "mun.nm_mun",
                "mun.sigla_uf",
                f"{SQL_ID_ZONA_MUN} AS id_zona_mun",
                "malha.nr_zona",
                "malha.fonte_coord",
                "malha.precisao",
                "malha.flag_especial",
                "malha.qt_eleitores",
                "loc.nm_local_votacao",
                "loc.ds_endereco",
                "loc.nm_bairro",
                "loc.nr_cep",
                "loc.ds_tipo_local",
                "malha.geom",
                *colunas_res["local"],
                f"{sql_qt_validos(tipos_res['local'])} AS qt_validos",
                *colunas_margem(ano, tipos_res["local"]),
                f"{sql_pct('pc_brancos_nulos')} AS pct_brancos_nulos",
                sql_classe_sequencial(
                    sql_pc_brancos_nulos(tipos_res["local"]), cores.QUEBRAS_BRANCO_NULO
                )
                + " AS classe_brancos_nulos",
                f"{sql_pct('pc_abstencao')} AS pct_abstencao",
                sql_classe_sequencial(sql_pc_abstencao(), cores.QUEBRAS_ABSTENCAO)
                + " AS classe_abstencao",
            ],
            from_sql=(
                f"FROM\n  {SCHEMA_PG}.malha_locais_votacao_{ano} AS malha\n"
                f"  LEFT JOIN {SCHEMA_PG}.locais_votacao_{ano} AS loc\n"
                "    ON malha.id_local = loc.id_local\n"
                f"  LEFT JOIN {SCHEMA_PG}.{eleicao}_local AS res\n"
                "    ON malha.id_local = res.id_local\n"
                f"{JOIN_MUNICIPIO}\n"
                "WHERE\n  NOT malha.flag_sem_coord"
            ),
            diretorio_saida=pasta,
            requires=requires(
                pasta,
                dir_api / "schema.sql",
                schema_pontos,
                schema_locais,
                schemas_res["local"],
                schema_municipio,
            ),
        )
        logger.info("%d, turno %d: tabelas e views geradas", ano, turno)

    return {"zona_mun": schema_zona}


def gera_mudanca(
    ano_antes: int, ano_depois: int, turno: int, saida: Path, schema_raiz: Path, schema_zona: Path
) -> None:
    """Tabela e view da mudança de votação entre dois anos, no mesmo turno (ids
    e malha do ano mais recente). A cor é de quem cresceu mais nos votos válidos."""
    dir_tse = saida / "collections" / SCHEMA_PG
    dir_api = saida / "collections" / "public_read_api"
    nome = f"presidente_mudanca_{ano_antes}_{ano_depois}_t{turno}"

    parquet = RESULTADOS_DIR / (
        f"mudanca_presidente_{ano_antes}_{ano_depois}_t{turno}_zona_mun.parquet"
    )
    if not parquet.exists():
        logger.warning("%s não existe — fora da carga", parquet.name)
        return

    todas = colunas_parquet(parquet)
    colunas = {c: t for c, t in todas.items() if not c.startswith(PREFIXOS_FORA_DA_MUDANCA)}
    opcoes = {}
    if len(colunas) < len(todas):
        opcoes["src_sql"] = f"SELECT {', '.join(colunas)} FROM {parquet.stem}"
    schema_mudanca = gera_tabela(
        f"{nome}_zona_mun",
        dir_tse / nome / "zona_mun",
        colunas,
        caminho_fonte=copia_fonte(parquet, saida, str(ano_depois)),
        chave="id_zona_mun",
        schema_raiz=schema_raiz,
        **opcoes,
    )

    # Parquets anteriores a `cresceu_mais_validos` (só 2º turno) usam a variação
    # de Lula/Haddad: com dois candidatos um sobe o que o outro cai, e a classe é a mesma.
    if "cresceu_mais_validos" in colunas:
        classe = sql_classe_crescimento("res.cresceu_mais_validos", "res.var_pc_validos_maior")
    else:
        classe = sql_classe_divergente("res.var_pc_validos_pt", cores.QUEBRAS_MUDANCA)

    pasta = dir_api / SCHEMA_PG / f"malha_zona_mun_mudanca_{ano_antes}_{ano_depois}_t{turno}"
    exporta.gera_view_publica(
        f"{SCHEMA_PG}_malha_zona_mun_mudanca_{ano_antes}_{ano_depois}_t{turno}",
        [
            "malha.id_zona_mun AS id",
            "malha.id_zona_mun",
            "malha.cd_mun",
            "mun.nm_mun",
            "mun.sigla_uf",
            "malha.nr_zona",
            "malha.n_zonas_mun",
            "malha.geom_estimada",
            "malha.geom",
            *[f"res.{col}" for col in colunas if col != "id_zona_mun"],
            f"{classe} AS classe_mudanca",
        ],
        from_sql=(
            f"FROM\n  {SCHEMA_PG}.malha_zona_mun_{ano_depois} AS malha\n"
            f"  LEFT JOIN {SCHEMA_PG}.{nome}_zona_mun AS res\n"
            "    ON malha.id_zona_mun = res.id_zona_mun\n"
            f"{JOIN_MUNICIPIO}"
        ),
        diretorio_saida=pasta,
        requires=requires(
            pasta,
            dir_api / "schema.sql",
            schema_zona,
            schema_mudanca,
            saida / "collections" / SCHEMA_MUNICIPIO,
        ),
    )
    logger.info("Mudança %d -> %d, turno %d: tabela e view geradas", ano_antes, ano_depois, turno)


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

    for ano_antes, ano_depois in zip(sorted(anos), sorted(anos)[1:]):
        for turno in turnos:
            gera_mudanca(ano_antes, ano_depois, turno, saida, schema_raiz, schemas_zona[ano_depois])

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
