"""Mapas Folium coropléticos dos resultados de Presidente — **uso local**.

O site publica os mapas pelo visualizador (`mapas.qmd`, dados de
`visualizador.py`); estes HTML Folium ficam para inspeção local e saem em
`dados/mapas_folium/` (git-ignorado). Um mapa por UF em setor censitário
(o nacional em setor dá ~380 MB só de geometria, mesmo simplificado), mais
os nacionais por município e por zona×município.

Cor: as mesmas classes discretas do visualizador (`cores.py`), sobre a
margem assinada Lula/Haddad − Bolsonaro dos votos válidos.
"""

from __future__ import annotations

import logging
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
import shapely

from . import cores
from .config import (
    CARTO_API_KEY,
    CD_UF_POR_SG_UF,
    CRS_SIRGAS2000,
    GEOMETRIAS_DIR,
    NOME_PT_POR_ANO,
    NR_BOLSONARO_POR_ANO,
    NR_PT,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
    SETORES_2022_GPKG,
    TILES_CARTO_ATTR,
    TILES_CARTO_POSITRON,
)

logger = logging.getLogger(__name__)


def _adiciona_margem_assinada(df: pd.DataFrame, ano: int) -> pd.DataFrame:
    df = df.copy()
    df["margem_assinada"] = df[f"pc_validos_{NR_PT}"] - df[f"pc_validos_{NR_BOLSONARO_POR_ANO[ano]}"]
    paleta = cores.cores_divergentes()
    classes = cores.classifica_divergente(df["margem_assinada"], cores.QUEBRAS_MARGEM)
    df["cor"] = [paleta[c] if c >= 0 else cores.COR_SEM_DADO for c in classes]
    return df


def _carrega_resultados(ano: int, turno: int, nivel: str) -> pd.DataFrame:
    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_{nivel}.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode processa_turno.py primeiro.")
    return _adiciona_margem_assinada(pd.read_parquet(caminho), ano)


def simplifica_geometria(
    geometria, *, tolerancia: float, grid_size: float
) -> gpd.array.GeometryArray:
    """Simplifica e arredonda a precisão das coordenadas.

    O arredondamento (`shapely.set_precision`) é o que mais pesa no tamanho
    do GeoJSON — coordenadas de setor censitário saem do IBGE em float64
    (~15 dígitos significativos), muito além da precisão que qualquer mapa
    consegue mostrar. Medido em SP (103.620 setores, o pior caso): só
    simplificar (tolerância 0,0002°) já dá 91 MB de GeoJSON; simplificar +
    arredondar para grade de 0,0001° (~10 m) cai pra ~30 MB.

    Uma fração pequena dos setores (~0,2–0,4% em SP) vira polígono vazio
    nesse processo — geralmente setores minúsculos que a simplificação some.
    Quem chama descarta essas linhas e loga quantas foram.
    """
    simplificada = shapely.simplify(geometria, tolerancia, preserve_topology=True)
    return shapely.set_precision(simplificada, grid_size=grid_size)


def _descarta_vazias(gdf: gpd.GeoDataFrame, rotulo: str) -> gpd.GeoDataFrame:
    n_vazias = gdf.geometry.is_empty.sum()
    if n_vazias:
        logger.warning(
            "%s: %d/%d geometrias viraram vazias ao simplificar — descartadas.",
            rotulo, n_vazias, len(gdf),
        )
        gdf = gdf.loc[~gdf.geometry.is_empty].copy()
    return gdf


def monta_setor_uf(
    sg_uf: str, ano: int, turno: int, *, tolerancia: float = 0.001, grid_size: float = 0.0001
) -> gpd.GeoDataFrame:
    """Malha de setores da UF (simplificada) + resultados por setor (estimados).

    `tolerancia`/`grid_size` em graus (~0,001° ≈ 100 m de tolerância de
    simplificação; grade de ~0,0001° ≈ 10 m de arredondamento de coordenada)
    — testados contra São Paulo, o pior caso (103.620 setores).
    """
    # A malha não tem sigla de 2 letras — só CD_UF (código IBGE) e NM_UF. O
    # campo do `where` tem que estar em `columns`, senão o pyogrio/OGR não
    # consegue filtrar por ele (ver CLAUDE.md).
    cd_uf = CD_UF_POR_SG_UF[sg_uf]
    gdf = gpd.read_file(
        SETORES_2022_GPKG, where=f"CD_UF = {cd_uf}", columns=["CD_SETOR", "CD_UF"]
    )
    gdf = gdf.rename(columns={"CD_SETOR": "cd_setor"}).drop(columns="CD_UF")
    gdf["geometry"] = simplifica_geometria(gdf.geometry.values, tolerancia=tolerancia, grid_size=grid_size)
    gdf = _descarta_vazias(gdf, sg_uf)

    gdf = gdf.merge(_carrega_resultados(ano, turno, "setor"), on="cd_setor", how="left")
    gdf["cor"] = gdf["cor"].fillna(cores.COR_SEM_DADO)
    return gdf


def monta_municipios(
    ano: int, turno: int, *, tolerancia: float = 0.0005, grid_size: float = 0.0001
) -> gpd.GeoDataFrame:
    """Malha de municípios (etapa 00) + `res_..._municipio` (observado)."""
    caminho_municipios = PROCESSADO_DIR / "aux" / "municipios.gpkg"
    if not caminho_municipios.exists():
        raise FileNotFoundError(f"{caminho_municipios} não existe — rode 00_auxiliares.qmd (R).")
    gdf = gpd.read_file(caminho_municipios)[["cd_mun", "geometry"]]
    gdf["geometry"] = simplifica_geometria(gdf.geometry.values, tolerancia=tolerancia, grid_size=grid_size)
    gdf = _descarta_vazias(gdf, "Municípios")

    gdf = gdf.merge(_carrega_resultados(ano, turno, "municipio"), on="cd_mun", how="left")
    gdf["cor"] = gdf["cor"].fillna(cores.COR_SEM_DADO)
    return gdf


def monta_zona_mun(
    ano: int, turno: int, *, tolerancia: float = 0.0005, grid_size: float = 0.0001
) -> gpd.GeoDataFrame:
    """Malha de zona×município (etapa 03) + resultados na mesma granularidade.

    Camada mais fiel: os valores são exatamente os que o TSE publica por
    município e zona — a única aproximação é a fronteira interna das cidades
    com mais de uma zona (`geom_estimada = True`, ver PLAN.md).
    """
    caminho_geom = GEOMETRIAS_DIR / f"geom_zona_mun_{ano}.gpkg"
    if not caminho_geom.exists():
        raise FileNotFoundError(
            f"{caminho_geom} não existe — rode scripts/03_pesos_alocacao.py --ano {ano}."
        )
    gdf = gpd.read_file(caminho_geom)[["id_zona_mun", "geom_estimada", "geometry"]]
    gdf["geometry"] = simplifica_geometria(gdf.geometry.values, tolerancia=tolerancia, grid_size=grid_size)
    gdf = _descarta_vazias(gdf, "Zona×município")

    gdf = gdf.merge(_carrega_resultados(ano, turno, "zona_mun"), on="id_zona_mun", how="left")
    gdf["cor"] = gdf["cor"].fillna(cores.COR_SEM_DADO)
    return gdf


def _pc_fmt(valor) -> str:
    return "—" if pd.isna(valor) else f"{100 * valor:.1f}%"


def gera_mapa(
    gdf: gpd.GeoDataFrame,
    caminho_saida: Path,
    *,
    ano: int,
    col_id: str,
    rotulo_id: str,
) -> Path:
    """Constrói e salva um `folium.Map` coroplético a partir de `gdf` (com `cor`).

    Uma única camada `folium.GeoJson` para o `gdf` inteiro (não uma por
    feição) — um `folium.GeoJson`/`Popup` por linha, com dezenas de milhares
    de setores por UF, incharia o HTML e travaria o navegador. Os campos do
    tooltip são colunas já formatadas como texto (o Folium não formata valor
    por valor).
    """
    if not CARTO_API_KEY:
        raise RuntimeError(
            "CARTO_API_KEY não configurada — copie .env.example para .env e "
            "preencha (ver src/eleicoes/config.py). Os tiles de fundo não "
            "carregam sem ela."
        )

    nome_pt = NOME_PT_POR_ANO[ano]
    gdf = gdf.to_crs(CRS_SIRGAS2000).copy()
    gdf["pt_fmt"] = gdf[f"pc_validos_{NR_PT}"].apply(_pc_fmt)
    gdf["bolsonaro_fmt"] = gdf[f"pc_validos_{NR_BOLSONARO_POR_ANO[ano]}"].apply(_pc_fmt)
    gdf["comparecimento_fmt"] = gdf["pc_comparecimento"].apply(_pc_fmt)
    gdf["margem_fmt"] = gdf["margem_assinada"].apply(
        lambda v: "—" if pd.isna(v) else f"{100 * v:+.1f} p.p."
    )

    campos = [col_id, "comparecimento_fmt", "pt_fmt", "bolsonaro_fmt", "margem_fmt"]
    apelidos = [
        f"{rotulo_id}:", "Comparecimento:", f"{nome_pt}:", "Bolsonaro:",
        f"Margem ({nome_pt} − Bolsonaro):",
    ]

    minx, miny, maxx, maxy = gdf.total_bounds
    mapa = folium.Map(tiles=TILES_CARTO_POSITRON, attr=TILES_CARTO_ATTR, control_scale=True)
    mapa.fit_bounds([[miny, minx], [maxy, maxx]])

    folium.GeoJson(
        gdf[[*campos, "cor", "geometry"]],
        style_function=lambda feature: {
            "fillColor": feature["properties"]["cor"],
            "color": "#ffffff",
            "weight": 0.3,
            "fillOpacity": 0.8,
        },
        tooltip=folium.GeoJsonTooltip(fields=campos, aliases=apelidos, sticky=True),
    ).add_to(mapa)

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    mapa.save(str(caminho_saida))
    return caminho_saida
