"""Mapas coropléticos (Folium) dos resultados de Presidente.

Um mapa por UF, em setor censitário (o pedido original), mais um mapa
nacional por município como visão geral — ver PLAN.md > "Mapas coropléticos
por setor" para o porquê da divisão (um único arquivo nacional em setor dá
~380 MB só de geometria, mesmo simplificada; inviável para o GitHub Pages).

Cor: escala **divergente** (não duas escalas sequenciais separadas) sobre a
margem **assinada** entre os dois candidatos dominantes,
`pc_validos_13 - pc_validos_22` (positivo = Lula, negativo = Bolsonaro,
0 = empate técnico) — é o desenho padrão de mapa eleitoral de dois polos
(o mesmo princípio dos mapas red/blue dos EUA), e é exatamente o que a skill
`dataviz` prescreve para "polaridade" (`references/color-formula.md`: "two
hues + a neutral gray midpoint"). Os polos e o meio-tom vêm do arquivo de
paleta da skill (`references/palette.md` > Diverging pair): vermelho
`#e34948`, cinza `#f0efec`, azul `#2a78d6`. Funciona mesmo nos poucos setores
onde um terceiro candidato teve mais votos — a cor mostra a polarização
Lula×Bolsonaro, e `nr_vencedor` continua disponível no popup para quem quiser
o dado literal.
"""

from __future__ import annotations

import logging
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
import shapely
from matplotlib.colors import LinearSegmentedColormap, to_hex

logger = logging.getLogger(__name__)

from . import resultados as resultados_mod
from .config import (
    CARTO_API_KEY,
    CD_UF_POR_SG_UF,
    CRS_SIRGAS2000,
    GEOMETRIAS_DIR,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
    SETORES_2022_GPKG,
    TILES_CARTO_ATTR,
    TILES_CARTO_POSITRON,
)

# ==== Paleta (skill dataviz > references/palette.md > Diverging pair) =======

_COR_LULA = "#e34948"  # categórico slot 8 (red)
_COR_MEIO = "#f0efec"  # neutro (light)
_COR_BOLSONARO = "#2a78d6"  # categórico slot 1 (blue)
_COR_SEM_DADO = "#c3c2b7"  # "Baseline / axis" (light) — mais escuro que o meio
# da escala divergente, para não ser confundido com "empate técnico".

CMAP_MARGEM = LinearSegmentedColormap.from_list(
    "margem_lula_bolsonaro", [_COR_BOLSONARO, _COR_MEIO, _COR_LULA]
)

NR_LULA = 13
NR_BOLSONARO = 22


def cor_por_margem(margem_assinada: float | None) -> str:
    """`margem_assinada` em [-1, 1] (negativo = Bolsonaro, positivo = Lula) -> hex.

    `None`/NaN (setor sem resultado nesta camada) devolve a cor neutra de
    "sem dado", distinta do meio-tom da escala (que significa empate, não
    ausência de dado).
    """
    if margem_assinada is None or pd.isna(margem_assinada):
        return _COR_SEM_DADO
    t = max(0.0, min(1.0, (margem_assinada + 1) / 2))
    return to_hex(CMAP_MARGEM(t))


def _adiciona_margem_assinada(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    col_lula = f"pc_validos_{NR_LULA}"
    col_bolsonaro = f"pc_validos_{NR_BOLSONARO}"
    df["margem_assinada"] = df.get(col_lula) - df.get(col_bolsonaro)
    df["cor"] = df["margem_assinada"].apply(cor_por_margem)
    return df


def carrega_resultados_setor(ano: int, turno: int) -> pd.DataFrame:
    """Lê `res_presidente_{ano}_t{turno}_setor.parquet` e recalcula os
    indicadores (o parquet já vem com eles, mas recalcular aqui evita
    depender de quais colunas `pc_validos_*` o arquivo específico tem)."""
    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_setor.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode processa_turno.py primeiro.")
    df = pd.read_parquet(caminho)
    return _adiciona_margem_assinada(df)


def carrega_resultados_zona_mun(ano: int, turno: int) -> pd.DataFrame:
    """Lê `res_presidente_{ano}_t{turno}_zona_mun.parquet` como está — já é a
    granularidade final (zona × município), sem agregar mais nada. Camada
    **observada** (bate exato com `votacao_candidato_munzona` do TSE).
    """
    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_zona_mun.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode processa_turno.py primeiro.")
    df = pd.read_parquet(caminho)
    return _adiciona_margem_assinada(df)


def carrega_resultados_municipio(ano: int, turno: int) -> pd.DataFrame:
    """Agrega `res_presidente_{ano}_t{turno}_zona_mun.parquet` por `cd_mun`
    (os 7 primeiros dígitos de `id_zona_mun`) — a camada **observada** (bate
    exato com o TSE), não a estimativa por setor, para a visão nacional.
    """
    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_zona_mun.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode processa_turno.py primeiro.")
    df = pd.read_parquet(caminho)
    df["cd_mun"] = df["id_zona_mun"].str[:7]

    colunas_qt = [c for c in df.columns if c.startswith("qt_")]
    por_mun = df.groupby("cd_mun")[colunas_qt].sum().reset_index()

    candidatos = sorted(
        int(c.replace("qt_votos_", ""))
        for c in colunas_qt
        if c.startswith("qt_votos_") and int(c.replace("qt_votos_", "")) not in (95, 96)
    )
    cols_votos_validos = [f"qt_votos_{c}" for c in candidatos]
    por_mun = resultados_mod.calcula_indicadores(por_mun, cols_votos_validos=cols_votos_validos)
    return _adiciona_margem_assinada(por_mun)


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
    `monta_setor_uf`/`monta_municipios` descartam essas linhas e logam
    quantas foram (não silenciam — mesma filosofia do resto do pipeline).
    """
    simplificada = shapely.simplify(geometria, tolerancia, preserve_topology=True)
    return shapely.set_precision(simplificada, grid_size=grid_size)


def monta_setor_uf(
    sg_uf: str, ano: int, turno: int, *, tolerancia: float = 0.001, grid_size: float = 0.0001
) -> gpd.GeoDataFrame:
    """Malha de setores da UF (simplificada) + resultados por setor.

    `tolerancia`/`grid_size` em graus (~0,001° ≈ 100 m de tolerância de
    simplificação; grade de ~0,0001° ≈ 10 m de arredondamento de coordenada)
    — testados contra São Paulo, o pior caso (103.620 setores), antes de
    virar padrão. Ajustar se o HTML final de alguma UF passar da meta do
    PLAN.md (25 MB).
    """
    # A malha não tem sigla de 2 letras — só CD_UF (código IBGE) e NM_UF (achado
    # tentando "SG_UF", que não existe no arquivo). `columns` tem que incluir
    # CD_UF mesmo só usando CD_SETOR depois — o pyogrio/OGR precisa da coluna
    # disponível para poder filtrar por ela no próprio SQL.
    cd_uf = CD_UF_POR_SG_UF[sg_uf]
    gdf = gpd.read_file(
        SETORES_2022_GPKG, where=f"CD_UF = {cd_uf}", columns=["CD_SETOR", "CD_UF"]
    )
    gdf = gdf.rename(columns={"CD_SETOR": "cd_setor"}).drop(columns="CD_UF")
    gdf["geometry"] = simplifica_geometria(
        gdf.geometry.values, tolerancia=tolerancia, grid_size=grid_size
    )

    n_vazias = gdf.geometry.is_empty.sum()
    if n_vazias:
        logger.warning(
            "%s: %d/%d setores (%.2f%%) viraram geometria vazia ao simplificar — descartados.",
            sg_uf, n_vazias, len(gdf), 100 * n_vazias / len(gdf),
        )
        gdf = gdf.loc[~gdf.geometry.is_empty].copy()

    resultados_setor = carrega_resultados_setor(ano, turno)
    gdf = gdf.merge(resultados_setor, on="cd_setor", how="left")
    gdf.loc[gdf["cor"].isna(), "cor"] = _COR_SEM_DADO
    return gdf


def monta_municipios(
    ano: int, turno: int, *, tolerancia: float = 0.0005, grid_size: float = 0.0001
) -> gpd.GeoDataFrame:
    """Malha de municípios (etapa 00) + resultados agregados por município.

    5.570 polígonos é bem menos que setor, mas ainda vale simplificar/
    arredondar (ver `simplifica_geometria`) — municípios grandes têm
    contorno bem mais detalhado do que qualquer zoom nacional mostra.
    """
    caminho_municipios = PROCESSADO_DIR / "aux" / "municipios.gpkg"
    if not caminho_municipios.exists():
        raise FileNotFoundError(f"{caminho_municipios} não existe — rode 00_auxiliares.qmd (R).")
    gdf = gpd.read_file(caminho_municipios)[["cd_mun", "geometry"]]
    gdf["geometry"] = simplifica_geometria(
        gdf.geometry.values, tolerancia=tolerancia, grid_size=grid_size
    )
    n_vazias = gdf.geometry.is_empty.sum()
    if n_vazias:
        logger.warning(
            "Nacional: %d/%d municípios viraram geometria vazia ao simplificar — descartados.",
            n_vazias, len(gdf),
        )
        gdf = gdf.loc[~gdf.geometry.is_empty].copy()

    resultados_mun = carrega_resultados_municipio(ano, turno)
    gdf = gdf.merge(resultados_mun, on="cd_mun", how="left")
    gdf.loc[gdf["cor"].isna(), "cor"] = _COR_SEM_DADO
    return gdf


def monta_zona_mun(
    ano: int, turno: int, *, tolerancia: float = 0.0005, grid_size: float = 0.0001
) -> gpd.GeoDataFrame:
    """Malha de zona×município (etapa 03) + resultados na mesma granularidade.

    6.102 polígonos, mas `geom_zona_mun_{ano}.gpkg` sai da etapa 03 **sem**
    simplificação nenhuma (ao contrário de `SETORES_2022_GPKG` e
    `municipios.gpkg`, que `monta_setor_uf`/`monta_municipios` já simplificam)
    — daí 136,5 MB de GeoJSON bruto, mais que a malha de municípios
    simplificada. Mesma receita de `simplifica_geometria` resolve: ~32 MB
    com os parâmetros padrão, sem geometria vazia (os polígonos de
    zona×município são bem maiores em média que um setor, então não somem
    com a simplificação).

    Camada mais fiel de todas: os valores são exatamente os que o TSE
    publica por município e zona — a única aproximação é a fronteira interna
    das cidades com mais de uma zona (`geom_estimada = True`, ver PLAN.md).
    """
    caminho_geom = GEOMETRIAS_DIR / f"geom_zona_mun_{ano}.gpkg"
    if not caminho_geom.exists():
        raise FileNotFoundError(
            f"{caminho_geom} não existe — rode scripts/03_pesos_alocacao.py --ano {ano}."
        )
    gdf = gpd.read_file(caminho_geom)[["id_zona_mun", "geom_estimada", "geometry"]]
    gdf["geometry"] = simplifica_geometria(
        gdf.geometry.values, tolerancia=tolerancia, grid_size=grid_size
    )
    n_vazias = gdf.geometry.is_empty.sum()
    if n_vazias:
        logger.warning(
            "Zona×município: %d/%d viraram geometria vazia ao simplificar — descartados.",
            n_vazias, len(gdf),
        )
        gdf = gdf.loc[~gdf.geometry.is_empty].copy()

    resultados_zm = carrega_resultados_zona_mun(ano, turno)
    gdf = gdf.merge(resultados_zm, on="id_zona_mun", how="left")
    gdf.loc[gdf["cor"].isna(), "cor"] = _COR_SEM_DADO
    return gdf


def _pc_fmt(valor) -> str:
    return "—" if pd.isna(valor) else f"{100 * valor:.1f}%"


def _adiciona_campos_exibicao(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Colunas de texto já formatadas para o tooltip/popup — o Folium não
    formata valor por valor, só mostra o que a coluna já tiver."""
    gdf = gdf.copy()
    gdf["comparecimento_fmt"] = gdf["pc_comparecimento"].apply(_pc_fmt)
    gdf["lula_fmt"] = gdf[f"pc_validos_{NR_LULA}"].apply(_pc_fmt)
    gdf["bolsonaro_fmt"] = gdf[f"pc_validos_{NR_BOLSONARO}"].apply(_pc_fmt)
    gdf["vencedor_fmt"] = gdf["nr_vencedor"].apply(lambda v: "—" if pd.isna(v) else str(int(v)))
    gdf["margem_fmt"] = gdf["margem_assinada"].apply(
        lambda v: "—" if pd.isna(v) else f"{100 * v:+.1f} p.p."
    )
    return gdf


def gera_mapa(
    gdf: gpd.GeoDataFrame,
    caminho_saida: Path,
    *,
    col_id: str,
    rotulo_id: str,
) -> Path:
    """Constrói e salva um `folium.Map` coroplético a partir de `gdf`.

    `gdf` já deve ter as colunas `cor` (hex) e `margem_assinada` (de
    `monta_setor_uf`/`monta_municipios`). Genérico o bastante para os dois
    níveis (setor por UF, município nacional) — só muda `col_id`/`rotulo_id`.

    Uma única camada `folium.GeoJson` para o `gdf` inteiro (não uma por
    feição) — um `folium.GeoJson`/`Popup` por linha, com dezenas de milhares
    de setores por UF, incharia o HTML e travaria o navegador; o padrão do
    Folium para isso é `style_function` lendo a propriedade de cada feição do
    próprio GeoJSON, mais `GeoJsonTooltip`/`GeoJsonPopup` declarativos.
    """
    if not CARTO_API_KEY:
        raise RuntimeError(
            "CARTO_API_KEY não configurada — copie .env.example para .env e "
            "preencha (ver src/eleicoes/config.py). Os tiles de fundo não "
            "carregam sem ela."
        )

    gdf = gdf.to_crs(CRS_SIRGAS2000)
    gdf = _adiciona_campos_exibicao(gdf)
    minx, miny, maxx, maxy = gdf.total_bounds

    mapa = folium.Map(tiles=TILES_CARTO_POSITRON, attr=TILES_CARTO_ATTR, control_scale=True)
    mapa.fit_bounds([[miny, minx], [maxy, maxx]])

    campos = [col_id, "comparecimento_fmt", "lula_fmt", "bolsonaro_fmt", "vencedor_fmt", "margem_fmt"]
    apelidos = [
        f"{rotulo_id}:", "Comparecimento:", "Lula (13):", "Bolsonaro (22):",
        "Vencedor:", "Margem (Lula − Bolsonaro):",
    ]

    colunas_geojson = [col_id, "cor", *campos[1:], "geometry"]
    folium.GeoJson(
        gdf[colunas_geojson],
        style_function=lambda feature: {
            "fillColor": feature["properties"]["cor"],
            "color": "#ffffff",
            "weight": 0.3,
            "fillOpacity": 0.75 if feature["properties"]["cor"] != _COR_SEM_DADO else 0.35,
        },
        tooltip=folium.GeoJsonTooltip(fields=campos, aliases=apelidos, sticky=True),
        popup=folium.GeoJsonPopup(fields=campos, aliases=apelidos, max_width=280),
    ).add_to(mapa)

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    mapa.save(str(caminho_saida))
    return caminho_saida
