"""Helpers dos artefatos de carga na GeoReDUS (`07_carga_georedus.qmd`).

Leitura dos esquemas dos arquivos, colunas derivadas em SQL (classes de cor com
os mesmos cortes de `cores.py`) e escrita de uma tabela do schema `tse`. A
definição de cada tabela e view, por ano e turno, fica no notebook.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pyogrio

from . import cores, exporta
from .cadastro import NIVEL_MUNICIPIO, NIVEL_UF, fonte_por_zoom
from .config import CODIGOS_NAO_CANDIDATO, NR_BOLSONARO_POR_ANO, NR_PT
from .resultados import SENTINELA_EXTERIOR, calcula_indicadores

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

# Nível UF: nome da UF e geometria para o agrupamento por zoom.
TABELA_UF = "ibge.malha_br_uf_2024"
SCHEMA_UF = Path("ibge") / "malha" / "br_uf_2024" / "schema.sql"

# Último tile z de cada nível agrupado. O mapa de zoom Z usa tiles z = floor(Z),
# então UF até o zoom 5 e município até o 8, os mesmos cortes do censo
# (`GeoReDUS.jsx`: intramun acima de 8, intrauf acima de 5). O nível mais
# detalhado (zona ou local) vale de lá em diante.
Z_MAX_POR_NIVEL = {NIVEL_UF: 4, NIVEL_MUNICIPIO: 7}

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


def colunas_brancos_abstencao(colunas: dict[str, str]) -> list[str]:
    """Percentual e classe de brancos e nulos e de abstenção (camadas de pontos)."""
    return [
        f"{sql_pct('pc_brancos_nulos')} AS pct_brancos_nulos",
        sql_classe_sequencial(sql_pc_brancos_nulos(colunas), cores.QUEBRAS_BRANCO_NULO)
        + " AS classe_brancos_nulos",
        f"{sql_pct('pc_abstencao')} AS pct_abstencao",
        sql_classe_sequencial(sql_pc_abstencao(), cores.QUEBRAS_ABSTENCAO) + " AS classe_abstencao",
    ]


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


# ==== Níveis agregados por zoom ===============================================
#
# Ao dar zoom out, as camadas passam a mostrar a UF e depois o município, em vez
# de zonas ou locais de votação. O nível vem de uma função de tile (publicada
# pelo Martin) que escolhe a view pelo `z`; o cadastro aponta para ela
# (`cadastro.fonte_por_zoom`). As contagens são somadas e os indicadores e
# classes recalculados — nunca a média dos indicadores do nível de baixo.


def agrega_uf(res_municipio: pd.DataFrame) -> pd.DataFrame:
    """Resultados por UF: soma as contagens dos municípios e recalcula os indicadores.

    O exterior (`SENTINELA_EXTERIOR`) fica de fora: não tem UF e não aparece nos
    níveis agrupados. O código da UF são os 2 primeiros dígitos do `cd_mun` (IBGE).

    Args:
        res_municipio: `res_{eleicao}_municipio`, com `cd_mun` e as colunas
            `qt_votos_*`, `qt_comparecimento` e `qt_aptos`.

    Returns:
        Uma linha por `cd_uf`, com as mesmas colunas do município (menos `cd_mun`).
    """
    base = res_municipio[~res_municipio["cd_mun"].str.startswith(SENTINELA_EXTERIOR)].copy()
    base["cd_uf"] = base["cd_mun"].str[:2]
    colunas_qt = [col for col in base.columns if col.startswith("qt_")]
    validos = [
        col
        for col in colunas_qt
        if col.startswith("qt_votos_")
        and int(col.removeprefix("qt_votos_")) not in CODIGOS_NAO_CANDIDATO
    ]
    # `min_count=1`: aptos ausente (NA) em todos os municípios da UF continua NA.
    uf = base.groupby("cd_uf")[colunas_qt].sum(min_count=1).reset_index()
    uf = calcula_indicadores(uf, cols_votos_validos=validos)
    return uf[["cd_uf", *[col for col in res_municipio.columns if col != "cd_mun"]]]


def nome_coluna(expressao: str) -> str:
    """Nome da coluna de uma expressão do `SELECT`: o alias, ou o campo após o ponto."""
    if " AS " in expressao:
        return expressao.rsplit(" AS ", 1)[1].strip()
    return expressao.rsplit(".", 1)[-1].strip()


@dataclass(frozen=True)
class Vista:
    """View gerada em `public_read_api`: o que a função de tile precisa saber dela."""

    nome: str
    schema: Path
    colunas: tuple[str, ...]  # sem a geometria


def gera_vista(nome_view: str, colunas_select: list[str], **kwargs) -> Vista:
    """`exporta.gera_view_publica` devolvendo também o nome das colunas da view."""
    arquivos = exporta.gera_view_publica(nome_view, colunas_select, **kwargs)
    nomes = tuple(n for n in map(nome_coluna, colunas_select) if n != "geom")
    return Vista(nome_view, arquivos["schema.sql"], nomes)


def sql_funcao_tile(
    nome: str,
    niveis: list[tuple[Vista, int]],
    *,
    com_nivel: bool,
    padroes: dict[str, str] | None = None,
    titulos: dict[int, str] | None = None,
) -> str:
    """SQL da função de tile que escolhe o nível pelo `z`.

    Args:
        nome: nome da função em `public_read_api` (e do layer do tile).
        niveis: do mais agregado ao mais detalhado, cada um com a sua `Vista` e o
            número do nível (`cadastro.NIVEL_*`). O último vale dos tiles acima
            de `Z_MAX_POR_NIVEL` do penúltimo em diante.
        com_nivel: se verdadeiro, o tile traz a coluna `nivel` (o raio das
            bolinhas usa uma referência de tamanho por nível).
        padroes: SQL no lugar do `NULL` para a coluna que o nível agrupado não
            tem (ex.: `{"geom_estimada": "false"}`: a fronteira do IBGE não é estimada).
        titulos: SQL do atributo `titulo` de cada nível (alias `v`). O título do
            tooltip da zona leva o número da zona, que o nível agrupado não tem.

    Returns:
        O SQL (`DROP`, `CREATE OR REPLACE FUNCTION` e `GRANT`).
    """
    saida = niveis[-1][0].colunas  # o nível mais detalhado define os atributos do tile
    padroes = padroes or {}
    ramos = []
    for i, (vista, nivel) in enumerate(niveis):
        atributos = [
            f"v.{col}" if col in vista.colunas else f"{padroes.get(col, 'NULL::text')} AS {col}"
            for col in saida
        ]
        if titulos:
            atributos.append(f"{titulos[nivel]} AS titulo")
        if com_nivel:
            atributos.append(f"{nivel} AS nivel")
        consulta = (
            f"    SELECT ST_AsMVT(t, '{nome}', 4096, 'geom') INTO mvt FROM (\n"
            "      SELECT\n"
            "        ST_AsMVTGeom(ST_Transform(v.geom, 3857), env, 4096, 64, true) AS geom,\n        "
            + ",\n        ".join(atributos)
            + f"\n      FROM public_read_api.{vista.nome} AS v\n"
            "      WHERE v.geom && ST_Transform(env, 4674)\n"
            "    ) AS t WHERE t.geom IS NOT NULL;\n"
        )
        if i == 0:
            condicao = f"  IF z <= {Z_MAX_POR_NIVEL[nivel]} THEN\n"
        elif i < len(niveis) - 1:
            condicao = f"  ELSIF z <= {Z_MAX_POR_NIVEL[nivel]} THEN\n"
        else:
            condicao = "  ELSE\n"
        ramos.append(condicao + consulta)

    assinatura = "integer, integer, integer, jsonb"
    return (
        f"DROP FUNCTION IF EXISTS public_read_api.{nome}({assinatura});\n"
        f"CREATE OR REPLACE FUNCTION public_read_api.{nome}(\n"
        "  z integer, x integer, y integer, query_params jsonb\n"
        ") RETURNS bytea AS $$\n"
        "DECLARE\n  env geometry := ST_TileEnvelope(z, x, y);\n  mvt bytea;\n"
        "BEGIN\n" + "".join(ramos) + "  END IF;\n  RETURN mvt;\nEND;\n"
        "$$ LANGUAGE plpgsql STABLE PARALLEL SAFE;\n\n"
        f"GRANT EXECUTE ON FUNCTION public_read_api.{nome}({assinatura})\n"
        "TO mvt_system_reader, pgrst_public_anonymous;\n"
    )


def gera_funcao_tile(
    niveis: list[tuple[Vista, int]],
    *,
    pasta_raiz: Path,
    schema_api: Path,
    com_nivel: bool = False,
    padroes: dict[str, str] | None = None,
    titulos: dict[int, str] | None = None,
) -> str:
    """Escreve o `schema.sql`/`down.sql` da função de tile de uma camada.

    O nome é o da view mais detalhada mais `_nivel` (`cadastro.fonte_por_zoom`).

    Args:
        niveis: ver `sql_funcao_tile`.
        pasta_raiz: `collections/public_read_api/tse`.
        schema_api: `schema.sql` de `public_read_api`.
        com_nivel, padroes, titulos: ver `sql_funcao_tile`.

    Returns:
        O nome da função.
    """
    nome = fonte_por_zoom(niveis[-1][0].nome)
    pasta = pasta_raiz / nome.removeprefix(f"{SCHEMA_PG}_")
    exporta._escreve_texto(
        pasta / "schema.sql",
        "".join(
            f"-- requires: {req}\n"
            for req in requires(pasta, schema_api, *[vista.schema for vista, _ in niveis])
        )
        + "\n"
        + sql_funcao_tile(nome, niveis, com_nivel=com_nivel, padroes=padroes, titulos=titulos),
    )
    exporta._escreve_texto(
        pasta / "down.sql",
        f"DROP FUNCTION IF EXISTS public_read_api.{nome}(integer, integer, integer, jsonb);\n",
    )
    return nome
