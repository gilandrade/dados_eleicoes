"""Helpers dos artefatos de carga na GeoReDUS (`07_carga_georedus.qmd`).

Leitura dos esquemas dos arquivos, colunas derivadas em SQL (classes de cor com
os mesmos cortes de `cores.py`) e escrita de uma tabela do schema `tse`. A
definição de cada tabela e view, por ano e turno, fica no notebook.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pyarrow.parquet as pq
import pyogrio

from . import cores, exporta
from .config import CODIGOS_NAO_CANDIDATO, NR_BOLSONARO_POR_ANO, NR_PT

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
