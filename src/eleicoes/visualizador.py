"""Dados do visualizador de mapas do site (`docs/mapas/dados/`, lidos por `assets/mapas.js`).

JSON compacto e colunar, carregado sob demanda pela página `mapas.qmd`, no
lugar de um HTML Folium por mapa (~30 MB cada; com 2018, 2022 e 2026 o site
passaria do limite de 1 GB do GitHub Pages).

- Percentuais viram inteiros **por mil** (452 = 45,2%), `null` = sem dado.
- A classe de cor já vem calculada (`cores.py`): o JS só pinta.
- A geometria é separada dos resultados: `locais_{ano}.json` e
  `zona_mun_{ano}_geo.json` valem para os dois turnos do ano.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from . import cores
from .config import (
    BRUTO_DIR,
    CARGO_PRESIDENTE,
    CARGOS,
    CODIGOS_NAO_CANDIDATO,
    CRS_SIRGAS2000,
    GEOMETRIAS_DIR,
    NOME_BOLSONARO_POR_ANO,
    NOME_PT_POR_ANO,
    NR_BOLSONARO_POR_ANO,
    NR_PT,
    PROCESSADO_DIR,
    REPO_ROOT,
    RESULTADOS_DIR,
    TILES_VISUALIZADOR,
    TILES_VISUALIZADOR_ATTR,
)
from .fontes import TIPO_ELEICAO_ORDINARIA, _sql_read_csv, converte_zip_em_parquet
from .resultados import SENTINELA_EXTERIOR

logger = logging.getLogger(__name__)

DADOS_VISUALIZADOR_DIR = REPO_ROOT / "docs" / "mapas" / "dados"

# Medido em 29/09/2026 na malha de 2022, nos 20% menores polígonos (< 138 km²):
# com 0,005° (~500 m) a borda se deslocava até 18% do tamanho do polígono
# (p90) e as zonas urbanas ficavam deformadas; com 0,0005° (~50 m), 3%. O
# arquivo vai de 13 para 32 MB, mas comprimido (o que o navegador baixa) só
# de 3,6 para 8,3 MB. A simplificação é de cobertura: vizinhos continuam
# colados, sem frestas, ao contrário do `simplify` por feição.
TOLERANCIA_SIMPLIFICACAO = 0.0005
GRADE_COORDENADA = 0.0001
CASAS_COORDENADA = 4


def _escreve_json(obj, caminho: Path) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    logger.info("%s (%.1f MB)", caminho.name, caminho.stat().st_size / 1e6)
    return caminho


def _por_mil(serie: pd.Series) -> list:
    v = pd.to_numeric(serie, errors="coerce").to_numpy(dtype=float)
    return [None if np.isnan(x) else int(round(1000 * x)) for x in v]


def _inteiro(serie: pd.Series) -> list:
    v = pd.to_numeric(serie, errors="coerce").to_numpy(dtype=float)
    return [None if np.isnan(x) else int(round(x)) for x in v]


def _nome_municipio() -> pd.Series:
    """`cd_mun` -> "Município – UF"."""
    mun = gpd.read_file(
        PROCESSADO_DIR / "aux" / "municipios.gpkg", columns=["cd_mun", "name_muni", "abbrev_state"],
        ignore_geometry=True,
    )
    return pd.Series(
        (mun["name_muni"] + " – " + mun["abbrev_state"]).to_numpy(), index=mun["cd_mun"]
    )


def nomes_candidatos(ano: int, turno: int) -> dict[str, str]:
    """`{nr: nome}` dos candidatos a Presidente, lidos do `votacao_secao` bruto.

    Turno que ainda não está no `votacao_secao` (versão preliminar da etapa 04c)
    usa os nomes gravados por ela, `candidatos_presidente_{ano}_t{turno}.parquet`.
    """
    caminho = BRUTO_DIR / str(ano) / f"votacao_secao_{ano}_BR.csv"
    linhas = duckdb.sql(
        f"""select distinct cast(nr_votavel as integer) as nr, nm_votavel
            from {_sql_read_csv(caminho)}
            where cast(cd_cargo as integer) = {CARGO_PRESIDENTE}
              and cast(nr_turno as integer) = {turno}"""
    ).fetchall()
    if not linhas:
        nomes = pd.read_parquet(RESULTADOS_DIR / f"candidatos_presidente_{ano}_t{turno}.parquet")
        linhas = list(nomes[["nr_votavel", "nm_votavel"]].itertuples(index=False, name=None))
    # Ordenado: o `distinct` do duckdb não tem ordem fixa, e o JSON mudaria a
    # cada exportação sem nada ter mudado.
    return {
        str(nr): nome.title() for nr, nome in sorted(linhas) if nr not in CODIGOS_NAO_CANDIDATO
    }


def _margem_pt_bolsonaro(df: pd.DataFrame, ano: int) -> pd.Series:
    return df[f"pc_validos_{NR_PT}"] - df[f"pc_validos_{NR_BOLSONARO_POR_ANO[ano]}"]


# ==== Pontos (locais de votação) ==============================================


def exporta_locais(ano: int) -> list[str]:
    """`locais_{ano}.json`: posição, nome e município de cada local (ordem fixa).

    Returns:
        A lista de `id_local` na ordem gravada — os arquivos por turno são
        alinhados a ela.
    """
    geom = gpd.read_file(GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}.gpkg")
    geom = geom.loc[~geom["flag_sem_coord"].astype(bool)].to_crs(CRS_SIRGAS2000)
    geom = geom.sort_values("id_local").reset_index(drop=True)

    locais = pd.read_parquet(
        PROCESSADO_DIR / f"locais_{ano}.parquet",
        columns=["id_local", "nm_local_votacao", "sg_uf", "nm_bairro"],
    )
    geom = geom.drop(columns=["sg_uf", "nm_bairro"], errors="ignore").merge(
        locais, on="id_local", how="left"
    )

    nome_mun = _nome_municipio()
    municipios = sorted(geom["cd_mun"].dropna().unique())
    idx_mun = {cd: i for i, cd in enumerate(municipios)}

    # Tabelas de índices para os filtros do mapa (UF -> município -> bairro).
    geom["sg_uf"] = geom["sg_uf"].fillna("")
    ufs = sorted(geom["sg_uf"].unique())
    idx_uf = {uf: i for i, uf in enumerate(ufs)}
    bairro = geom["nm_bairro"].fillna("").str.strip().str.title().replace("", "(sem bairro)")
    bairros = sorted(bairro.unique())
    idx_bairro = {b: i for i, b in enumerate(bairros)}

    _escreve_json(
        {
            "id": geom["id_local"].tolist(),
            "lon": np.round(geom.geometry.x, 5).tolist(),
            "lat": np.round(geom.geometry.y, 5).tolist(),
            "nome": geom["nm_local_votacao"].fillna("").str.title().tolist(),
            "mun": [idx_mun[cd] for cd in geom["cd_mun"]],
            "municipios": [nome_mun.get(cd, cd) for cd in municipios],
            "uf": [idx_uf[u] for u in geom["sg_uf"]],
            "ufs": ufs,
            "bairro": [idx_bairro[b] for b in bairro],
            "bairros": bairros,
        },
        DADOS_VISUALIZADOR_DIR / f"locais_{ano}.json",
    )
    return geom["id_local"].tolist()


def exporta_locais_turno(ano: int, turno: int, ids: list[str]) -> Path:
    """`locais_{ano}_t{turno}.json`: classes e valores alinhados a `locais_{ano}.json`."""
    res = pd.read_parquet(RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_local.parquet")
    res = res.set_index("id_local").reindex(ids)
    margem = _margem_pt_bolsonaro(res, ano)
    cols_candidatos = [
        c for c in res.columns
        if c.startswith("qt_votos_") and int(c.removeprefix("qt_votos_")) not in CODIGOS_NAO_CANDIDATO
    ]
    # min_count=1: local sem resultado fica sem dado (null), não com 0 votos.
    qt_validos = res[cols_candidatos].sum(axis=1, min_count=1)

    return _escreve_json(
        {
            "c_pres": cores.classifica_divergente(margem, cores.QUEBRAS_MARGEM).tolist(),
            "c_bn": cores.classifica_sequencial(res["pc_brancos_nulos"], cores.QUEBRAS_BRANCO_NULO).tolist(),
            "c_abst": cores.classifica_sequencial(res["pc_abstencao"], cores.QUEBRAS_ABSTENCAO).tolist(),
            "pt": _por_mil(res[f"pc_validos_{NR_PT}"]),
            "bo": _por_mil(res[f"pc_validos_{NR_BOLSONARO_POR_ANO[ano]}"]),
            "bn": _por_mil(res["pc_brancos_nulos"]),
            "abst": _por_mil(res["pc_abstencao"]),
            "aptos": _inteiro(res["qt_aptos"]),
            "comp": _inteiro(res["qt_comparecimento"]),
            "val": _inteiro(qt_validos),
            "venc": _inteiro(res["nr_vencedor"]),
            "candidatos": nomes_candidatos(ano, turno),
        },
        DADOS_VISUALIZADOR_DIR / f"locais_{ano}_t{turno}.json",
    )


# ==== Partido mais votado (eleições municipais) ================================


def siglas_partidos(ano: int) -> dict[int, str]:
    """`{número: sigla}` dos partidos do ano, do `consulta_cand_{ano}.zip`
    (`03b_baixa_resultados.qmd`). Só a eleição ordinária: o arquivo traz as
    suplementares dos anos seguintes, com as siglas de quando aconteceram."""
    destino = BRUTO_DIR / str(ano)
    parquet = converte_zip_em_parquet(
        destino / f"consulta_cand_{ano}.zip",
        destino / f"consulta_cand_{ano}_partidos.parquet",
        ("cd_tipo_eleicao", "nr_partido", "sg_partido"),
        nome_interno=f"consulta_cand_{ano}_BRASIL.csv",
    )
    caminho = str(parquet).replace("'", "''")
    linhas = duckdb.sql(
        f"""select cast(nr_partido as integer) as nr, mode(sg_partido) as sigla
            from read_parquet('{caminho}')
            where cast(cd_tipo_eleicao as integer) = {TIPO_ELEICAO_ORDINARIA}
            group by 1 order by 1"""
    ).fetchall()
    return dict(linhas)


def _tres_mais_votados(res: pd.DataFrame) -> tuple[list[list], list[list]]:
    """Números e fatias dos válidos (por mil) dos três partidos mais votados de
    cada linha: `([nr_1, nr_2, nr_3], [pc_1, pc_2, pc_3])`, cada item uma lista
    alinhada a `res`. Linha sem resultado, ou posição sem voto, fica `null`."""
    cols = [c for c in res.columns if c.startswith("pc_validos_")]
    numeros = np.array([int(c.removeprefix("pc_validos_")) for c in cols])
    pc = res[cols].to_numpy(dtype=float, na_value=np.nan)
    ordem = np.argsort(-np.nan_to_num(pc, nan=-1.0), axis=1, kind="stable")[:, :3]
    pc_top = np.take_along_axis(pc, ordem, axis=1)
    com_voto = pc_top > 0  # NaN > 0 é False
    nrs = [[int(n) if ok else None for n, ok in zip(numeros[ordem[:, k]], com_voto[:, k])] for k in range(3)]
    pcs = [[int(round(1000 * v)) if ok else None for v, ok in zip(pc_top[:, k], com_voto[:, k])] for k in range(3)]
    return nrs, pcs


def _nomes_candidatos_municipio(ano: int, turno: int, cargo: str, chave: str) -> dict | None:
    """`{município: {nr: nome}}` dos candidatos (etapa 04), pela coluna `chave`
    (`cd_mun_tse` nos pontos, `cd_mun` nas zonas). `None` em cargo por partido."""
    if CARGOS[cargo]["por_partido"]:
        return None
    nomes = pd.read_parquet(RESULTADOS_DIR / f"candidatos_{cargo}_{ano}_t{turno}.parquet")
    nomes = nomes.sort_values([chave, "nr_votavel"])
    return {
        mun: dict(zip(grupo["nr_votavel"].astype(str), grupo["nm_votavel"].str.title()))
        for mun, grupo in nomes.groupby(chave)
    }


def exporta_locais_partido(ano: int, turno: int, cargo: str, ids: list[str]) -> Path:
    """`locais_{ano}_t{turno}_{cargo}.json`, alinhado a `locais_{ano}.json`:
    classe do partido mais votado (`c_part`), os três mais votados (`nr`/`pc`)
    e, como no arquivo de Presidente, brancos e nulos, abstenção e os tamanhos."""
    res = pd.read_parquet(RESULTADOS_DIR / f"res_{cargo}_{ano}_t{turno}_local.parquet")
    res = res.set_index("id_local").reindex(ids)
    siglas = siglas_partidos(ano)
    cols_candidatos = [
        c for c in res.columns
        if c.startswith("qt_votos_") and int(c.removeprefix("qt_votos_")) not in CODIGOS_NAO_CANDIDATO
    ]
    qt_validos = res[cols_candidatos].sum(axis=1, min_count=1)
    # Local sem resultado (DF, município fora do 2º turno) ou sem voto válido
    # não tem partido mais votado: `nr_vencedor` viria do reindex como NaN no
    # primeiro caso, mas no segundo seria a primeira coluna.
    vencedor = res["nr_vencedor"].where(qt_validos > 0)
    nrs, pcs = _tres_mais_votados(res)

    saida = {
        "c_part": cores.classifica_partido(vencedor, siglas).tolist(),
        "c_bn": cores.classifica_sequencial(res["pc_brancos_nulos"], cores.QUEBRAS_BRANCO_NULO).tolist(),
        "c_abst": cores.classifica_sequencial(res["pc_abstencao"], cores.QUEBRAS_ABSTENCAO).tolist(),
        "nr": nrs,
        "pc": pcs,
        "bn": _por_mil(res["pc_brancos_nulos"]),
        "abst": _por_mil(res["pc_abstencao"]),
        "aptos": _inteiro(res["qt_aptos"]),
        "comp": _inteiro(res["qt_comparecimento"]),
        "val": _inteiro(qt_validos),
        "partidos": {str(nr): sigla for nr, sigla in siglas.items()},
    }
    nomes = _nomes_candidatos_municipio(ano, turno, cargo, "cd_mun_tse")
    if nomes is not None:
        saida["nomes"] = nomes
    return _escreve_json(saida, DADOS_VISUALIZADOR_DIR / f"locais_{ano}_t{turno}_{cargo}.json")


def exporta_zona_mun_partido(ano: int, turno: int, cargo: str) -> Path:
    """`zona_mun_{ano}_t{turno}_{cargo}.json`:
    `{id: [classe, nr_1, pc_1, nr_2, pc_2, nr_3, pc_3, comparecimento]}`."""
    res = pd.read_parquet(RESULTADOS_DIR / f"res_{cargo}_{ano}_t{turno}_zona_mun.parquet")
    res = res.loc[~res["id_zona_mun"].str.startswith(SENTINELA_EXTERIOR)].reset_index(drop=True)
    siglas = siglas_partidos(ano)
    cols_validos = [c for c in res.columns if c.startswith("pc_validos_")]
    vencedor = res["nr_vencedor"].where(res[cols_validos].notna().any(axis=1))
    nrs, pcs = _tres_mais_votados(res)
    colunas = zip(
        cores.classifica_partido(vencedor, siglas).tolist(),
        nrs[0], pcs[0], nrs[1], pcs[1], nrs[2], pcs[2],
        _inteiro(res["qt_comparecimento"]),
    )
    saida = {
        "dados": dict(zip(res["id_zona_mun"], (list(c) for c in colunas))),
        "partidos": {str(nr): sigla for nr, sigla in siglas.items()},
    }
    nomes = _nomes_candidatos_municipio(ano, turno, cargo, "cd_mun")
    if nomes is not None:
        saida["nomes"] = nomes
    return _escreve_json(saida, DADOS_VISUALIZADOR_DIR / f"zona_mun_{ano}_t{turno}_{cargo}.json")


# ==== Polígonos (zona × município) ============================================


def exporta_zona_mun_geom(ano: int) -> Path:
    """`zona_mun_{ano}_geo.json`, simplificado como cobertura (sem frestas)."""
    gdf = gpd.read_file(GEOMETRIAS_DIR / f"geom_zona_mun_{ano}.gpkg").to_crs(CRS_SIRGAS2000)
    gdf = gdf.loc[~gdf["id_zona_mun"].str.startswith(SENTINELA_EXTERIOR)].reset_index(drop=True)

    geometria = shapely.coverage_simplify(gdf.geometry.values, TOLERANCIA_SIMPLIFICACAO)
    gdf["geometry"] = shapely.set_precision(geometria, grid_size=GRADE_COORDENADA)
    n_vazias = int(gdf.geometry.is_empty.sum())
    if n_vazias:
        logger.warning("zona_mun %d: %d geometrias vazias depois de simplificar — descartadas.", ano, n_vazias)
        gdf = gdf.loc[~gdf.geometry.is_empty]

    nome_mun = _nome_municipio()
    features = [
        {
            "type": "Feature",
            "properties": {
                "id": id_zm,
                "rot": f"{nome_mun.get(id_zm[:7], id_zm[:7])} · zona {int(id_zm[7:])}",
                "est": bool(estimada),
            },
            "geometry": _geojson_arredondado(geometria),
        }
        for id_zm, estimada, geometria in zip(gdf["id_zona_mun"], gdf["geom_estimada"], gdf.geometry)
    ]
    return _escreve_json(
        {"type": "FeatureCollection", "features": features},
        DADOS_VISUALIZADOR_DIR / f"zona_mun_{ano}_geo.json",
    )


def _geojson_arredondado(geometria) -> dict:
    """GeoJSON da geometria com coordenadas em `CASAS_COORDENADA` casas —
    `round()` do Python devolve a representação curta (-46.6333), sem o
    ruído binário (-46.633300000000004) que dobraria o tamanho."""
    def arredonda(c):
        return [round(c[0], CASAS_COORDENADA), round(c[1], CASAS_COORDENADA)] if isinstance(c[0], float) else [arredonda(x) for x in c]

    mapeada = shapely.geometry.mapping(geometria)
    return {"type": mapeada["type"], "coordinates": arredonda(mapeada["coordinates"])}


def exporta_zona_mun_turno(ano: int, turno: int) -> Path:
    """`zona_mun_{ano}_t{turno}.json`: `{id: [classe, pt, bo, margem, comparecimento, vencedor]}`."""
    res = pd.read_parquet(RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_zona_mun.parquet")
    res = res.loc[~res["id_zona_mun"].str.startswith(SENTINELA_EXTERIOR)].reset_index(drop=True)
    margem = _margem_pt_bolsonaro(res, ano)
    colunas = zip(
        cores.classifica_divergente(margem, cores.QUEBRAS_MARGEM).tolist(),
        _por_mil(res[f"pc_validos_{NR_PT}"]),
        _por_mil(res[f"pc_validos_{NR_BOLSONARO_POR_ANO[ano]}"]),
        _por_mil(margem),
        _inteiro(res["qt_comparecimento"]),
        _inteiro(res["nr_vencedor"]),
    )
    return _escreve_json(
        {
            "dados": dict(zip(res["id_zona_mun"], (list(c) for c in colunas))),
            "candidatos": nomes_candidatos(ano, turno),
        },
        DADOS_VISUALIZADOR_DIR / f"zona_mun_{ano}_t{turno}.json",
    )


def exporta_mudanca(ano_antes: int, ano_depois: int, turno: int) -> Path:
    """`mudanca_{a}_{b}_t{turno}.json`, sobre a geometria de `zona_mun_{ano_depois}_geo.json`:
    `{id: [classe, pt_antes, pt_depois, delta, virou, classe_tipo, d_pt_aptos,
    d_bolsonaro_aptos, d_nao_voto_aptos, cresc_votos_pt, cresc_votos_bolsonaro,
    aptos_antes, aptos_depois, pt_antes, pt_depois, bolsonaro_antes,
    bolsonaro_depois, validos_antes, validos_depois, nr_terceiro_antes,
    nr_terceiro_depois, terceiro_antes, terceiro_depois]}`.

    As posições 0–4 são a mudança nos votos válidos (0 = classe de quem cresceu
    mais, `cores.cores_mudanca()`; 1–3 = Lula/Haddad); 5–10, a mudança sobre o
    eleitorado apto (por mil; crescimentos também por mil, 667 = +66,7%);
    11–18, as contagens absolutas (inscritos, votos de cada candidato e votos
    válidos, antes e depois) que o tooltip mostra; 19–22, o 3º lugar de cada
    ano (número e fatia dos válidos por mil; `null` no 2º turno).
    `candidatos` traz `{ano: {nr: nome}}` para o tooltip nomear o 3º lugar."""
    mud = pd.read_parquet(
        RESULTADOS_DIR / f"mudanca_presidente_{ano_antes}_{ano_depois}_t{turno}_zona_mun.parquet"
    )
    return _escreve_json(
        {
            "dados": dict(zip(mud["id_zona_mun"], _linhas_mudanca(mud, ano_antes, ano_depois))),
            "candidatos": {str(a): nomes_candidatos(a, turno) for a in (ano_antes, ano_depois)},
        },
        DADOS_VISUALIZADOR_DIR / f"mudanca_{ano_antes}_{ano_depois}_t{turno}.json",
    )


def _linhas_mudanca(mud: pd.DataFrame, ano_antes: int, ano_depois: int) -> list[list]:
    """Os 23 valores de cada linha de `exporta_mudanca`, na ordem do `mud`."""
    colunas = zip(
        cores.classifica_crescimento(mud["cresceu_mais_validos"], mud["var_pc_validos_maior"]).tolist(),
        _por_mil(mud[f"pc_validos_pt_{ano_antes}"]),
        _por_mil(mud[f"pc_validos_pt_{ano_depois}"]),
        _por_mil(mud["var_pc_validos_pt"]),
        mud["mudou_vencedor"].astype(int).tolist(),
        cores.classifica_tipo_mudanca(mud["tipo_mudanca"]).tolist(),
        _por_mil(mud["var_pc_aptos_pt"]),
        _por_mil(mud["var_pc_aptos_bolsonaro"]),
        _por_mil(mud["var_pc_aptos_nao_voto"]),
        _por_mil(mud["var_rel_votos_pt"]),
        _por_mil(mud["var_rel_votos_bolsonaro"]),
        _inteiro(mud[f"qt_aptos_{ano_antes}"]),
        _inteiro(mud[f"qt_aptos_{ano_depois}"]),
        _inteiro(mud[f"qt_votos_pt_{ano_antes}"]),
        _inteiro(mud[f"qt_votos_pt_{ano_depois}"]),
        _inteiro(mud[f"qt_votos_bolsonaro_{ano_antes}"]),
        _inteiro(mud[f"qt_votos_bolsonaro_{ano_depois}"]),
        _inteiro(mud[f"qt_validos_{ano_antes}"]),
        _inteiro(mud[f"qt_validos_{ano_depois}"]),
        _inteiro(mud[f"nr_terceiro_{ano_antes}"]),
        _inteiro(mud[f"nr_terceiro_{ano_depois}"]),
        _por_mil(mud[f"pc_validos_terceiro_{ano_antes}"]),
        _por_mil(mud[f"pc_validos_terceiro_{ano_depois}"]),
    )
    return [list(c) for c in colunas]


# ==== Polígonos (município) ====================================================
# O município é a menor unidade com limite oficial (IBGE): sem fronteira
# estimada, e a malha é uma só para todos os anos. Mesmos formatos de valores
# das zonas × município, com `cd_mun` no lugar de `id_zona_mun`.


def exporta_municipio_geom() -> Path:
    """`municipio_geo.json`, simplificado como cobertura (sem frestas)."""
    gdf = gpd.read_file(PROCESSADO_DIR / "aux" / "municipios.gpkg").to_crs(CRS_SIRGAS2000)
    gdf = gdf.sort_values("cd_mun").reset_index(drop=True)

    geometria = shapely.coverage_simplify(gdf.geometry.values, TOLERANCIA_SIMPLIFICACAO)
    gdf["geometry"] = shapely.set_precision(geometria, grid_size=GRADE_COORDENADA)
    n_vazias = int(gdf.geometry.is_empty.sum())
    if n_vazias:
        logger.warning("município: %d geometrias vazias depois de simplificar — descartadas.", n_vazias)
        gdf = gdf.loc[~gdf.geometry.is_empty]

    nome_mun = _nome_municipio()
    features = [
        {
            "type": "Feature",
            "properties": {"id": cd, "rot": nome_mun.get(cd, cd), "est": False},
            "geometry": _geojson_arredondado(geometria),
        }
        for cd, geometria in zip(gdf["cd_mun"], gdf.geometry)
    ]
    return _escreve_json(
        {"type": "FeatureCollection", "features": features}, DADOS_VISUALIZADOR_DIR / "municipio_geo.json"
    )


def _res_municipio(cargo: str, ano: int, turno: int) -> pd.DataFrame:
    res = pd.read_parquet(RESULTADOS_DIR / f"res_{cargo}_{ano}_t{turno}_municipio.parquet")
    return res.loc[~res["cd_mun"].str.startswith(SENTINELA_EXTERIOR)].reset_index(drop=True)


def exporta_municipio_turno(ano: int, turno: int) -> Path:
    """`municipio_{ano}_t{turno}.json`: `{cd_mun: [classe, pt, bo, margem, comparecimento, vencedor]}`."""
    res = _res_municipio("presidente", ano, turno)
    margem = _margem_pt_bolsonaro(res, ano)
    colunas = zip(
        cores.classifica_divergente(margem, cores.QUEBRAS_MARGEM).tolist(),
        _por_mil(res[f"pc_validos_{NR_PT}"]),
        _por_mil(res[f"pc_validos_{NR_BOLSONARO_POR_ANO[ano]}"]),
        _por_mil(margem),
        _inteiro(res["qt_comparecimento"]),
        _inteiro(res["nr_vencedor"]),
    )
    return _escreve_json(
        {
            "dados": dict(zip(res["cd_mun"], (list(c) for c in colunas))),
            "candidatos": nomes_candidatos(ano, turno),
        },
        DADOS_VISUALIZADOR_DIR / f"municipio_{ano}_t{turno}.json",
    )


def exporta_municipio_partido(ano: int, turno: int, cargo: str) -> Path:
    """`municipio_{ano}_t{turno}_{cargo}.json`, no formato de `exporta_zona_mun_partido`."""
    res = _res_municipio(cargo, ano, turno)
    siglas = siglas_partidos(ano)
    cols_validos = [c for c in res.columns if c.startswith("pc_validos_")]
    vencedor = res["nr_vencedor"].where(res[cols_validos].notna().any(axis=1))
    nrs, pcs = _tres_mais_votados(res)
    colunas = zip(
        cores.classifica_partido(vencedor, siglas).tolist(),
        nrs[0], pcs[0], nrs[1], pcs[1], nrs[2], pcs[2],
        _inteiro(res["qt_comparecimento"]),
    )
    saida = {
        "dados": dict(zip(res["cd_mun"], (list(c) for c in colunas))),
        "partidos": {str(nr): sigla for nr, sigla in siglas.items()},
    }
    nomes = _nomes_candidatos_municipio(ano, turno, cargo, "cd_mun")
    if nomes is not None:
        saida["nomes"] = nomes
    return _escreve_json(saida, DADOS_VISUALIZADOR_DIR / f"municipio_{ano}_t{turno}_{cargo}.json")


def exporta_municipio_mudanca(ano_antes: int, ano_depois: int, turno: int) -> Path:
    """`municipio_mudanca_{a}_{b}_t{turno}.json`, com os mesmos 23 valores de `exporta_mudanca`."""
    mud = pd.read_parquet(
        RESULTADOS_DIR / f"mudanca_presidente_{ano_antes}_{ano_depois}_t{turno}_municipio.parquet"
    )
    return _escreve_json(
        {
            "dados": dict(zip(mud["cd_mun"], _linhas_mudanca(mud, ano_antes, ano_depois))),
            "candidatos": {str(a): nomes_candidatos(a, turno) for a in (ano_antes, ano_depois)},
        },
        DADOS_VISUALIZADOR_DIR / f"municipio_mudanca_{ano_antes}_{ano_depois}_t{turno}.json",
    )


# ==== Configuração (tiles, legendas, combinações disponíveis) ================


TITULO_LEGENDA_PARTIDO = {
    "prefeito": "Partido do candidato a prefeito mais votado",
    "vereador": "Partido mais votado para vereador",
}


def exporta_config(
    pontos: list[tuple[int, int]],
    zona_mun: list[tuple[int, int]],
    mudancas: list[tuple[int, int, int]],
    municipais: dict[str, list[tuple[int, int]]] | None = None,
    municipio: dict[str, list[tuple]] | None = None,
) -> Path:
    """`config.json`: fundo do mapa, legendas e combinações disponíveis.

    `municipais` é `{"pontos_prefeito": [(ano, turno), ...], "zona_mun_prefeito":
    ..., "pontos_vereador": ..., "zona_mun_vereador": ...}` — a chave é a do
    mapa em `assets/mapas.js`.

    `municipio` é o mesmo para os mapas de município: `{"municipio_presidente":
    [(ano, turno)], "municipio_prefeito": ..., "municipio_vereador": ...,
    "municipio_mudanca": [(ano_antes, ano_depois, turno)]}`.
    """
    municipio = municipio or {}
    municipais = municipais or {}
    municipais_municipio = {
        chave: combos for chave, combos in municipio.items() if chave.split("_")[-1] in TITULO_LEGENDA_PARTIDO
    }
    anos_municipais = sorted({a for combos in municipais.values() for a, _ in combos})
    legendas_partido = {
        chave: {
            str(a): cores.legenda_partido(TITULO_LEGENDA_PARTIDO[chave.split("_")[-1]], siglas_partidos(a))
            for a in sorted({a for a, _ in combos})
        }
        for chave, combos in {**municipais, **municipais_municipio}.items()
    }
    municipio_presidente = municipio.get("municipio_presidente", [])
    anos = sorted({a for a, _ in pontos} | {a for a, _ in zona_mun} | {a for a, _ in municipio_presidente})
    legenda_presidente = {str(a): cores.legenda_margem(NOME_PT_POR_ANO[a]) for a in anos}
    chave = lambda a, b, t: f"{a}-{b}-{t}"
    todas_mudancas = sorted(set(mudancas) | set(municipio.get("municipio_mudanca", [])))
    legendas_mudanca = {chave(*m): cores.legenda_mudanca(*m) for m in todas_mudancas}
    legendas_mudanca_tipo = {chave(*m): cores.legenda_tipo_mudanca(*m) for m in todas_mudancas}
    return _escreve_json(
        {
            "cores_tipo_mudanca": cores.cores_tipo_mudanca(),
            "tiles": {"url": TILES_VISUALIZADOR, "attr": TILES_VISUALIZADOR_ATTR},
            "nome_pt": {str(a): NOME_PT_POR_ANO[a] for a in anos},
            "nome_bolsonaro": {str(a): NOME_BOLSONARO_POR_ANO[a] for a in anos},
            "nr_pt": NR_PT,
            "nr_bolsonaro": {str(a): NR_BOLSONARO_POR_ANO[a] for a in anos},
            "cor_sem_dado": cores.COR_SEM_DADO,
            "cores_divergentes": cores.cores_divergentes(),
            "cores_mudanca": cores.cores_mudanca(),
            "cores_sequenciais": cores.RAMPA_SEQUENCIAL,
            "cores_partido": cores.cores_partido(),
            "anos_municipais": anos_municipais,
            "legendas": {
                **legendas_partido,
                "pontos_presidente": legenda_presidente,
                "zona_mun_presidente": legenda_presidente,
                "municipio_presidente": legenda_presidente,
                "pontos_branco_nulo": cores.legenda_sequencial(
                    "Brancos e nulos (% do total de votos)", cores.QUEBRAS_BRANCO_NULO
                ),
                "pontos_abstencao": cores.legenda_sequencial(
                    "Abstenção (% dos inscritos)", cores.QUEBRAS_ABSTENCAO
                ),
                "mudanca": legendas_mudanca,
                "mudanca_tipo": legendas_mudanca_tipo,
            },
            "disponivel": {
                "pontos": [list(x) for x in pontos],
                "zona_mun": [list(x) for x in zona_mun],
                "mudanca": [list(x) for x in mudancas],
                **{chave: [list(x) for x in combos] for chave, combos in municipais.items()},
                **{chave: [list(x) for x in combos] for chave, combos in municipio.items()},
            },
        },
        DADOS_VISUALIZADOR_DIR / "config.json",
    )
