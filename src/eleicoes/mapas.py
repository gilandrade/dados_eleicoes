"""Mapas Folium (coropléticos e de pontos) dos resultados de Presidente.

Um mapa por UF, em setor censitário (o pedido original), mapas nacionais por
município e zona×município, mapas de pontos por local de votação (Presidente,
branco/nulo, abstenção) e um mapa de mudança de votação entre dois anos (2º
turno) — ver PLAN.md > "Mapas coropléticos por setor" para o porquê da
divisão por UF (um único arquivo nacional em setor dá ~380 MB só de
geometria, mesmo simplificada; inviável para o GitHub Pages).

Cor (mapas de Presidente): escala **divergente** (não duas escalas sequenciais separadas) sobre a
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
import numpy as np
import pandas as pd
import shapely
from matplotlib.colors import LinearSegmentedColormap, to_hex

logger = logging.getLogger(__name__)

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

# Rampa sequencial (azul, claro -> escuro; skill dataviz > references/palette.md
# > "Sequential hue", passos 150/300/450/600/700) — para métricas sem polo
# (branco/nulo, abstenção), onde "mais" não tem um lado bom/ruim como a
# margem Lula×Bolsonaro.
_CORES_SEQUENCIAL = ["#b7d3f6", "#6da7ec", "#2a78d6", "#184f95", "#0d366b"]


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


def calcula_quebras_quantis(serie: pd.Series, *, n_classes: int = len(_CORES_SEQUENCIAL)) -> np.ndarray:
    """Pontos de corte por quantil (não domínio fixo) — locais com poucos
    eleitores geram extremos que um domínio fixo (0-100%) esmagaria no mesmo
    tom para a maioria dos pontos."""
    validos = serie.dropna()
    if validos.empty:
        return np.array([0.0, 1.0])
    return np.unique(np.nanquantile(validos, np.linspace(0, 1, n_classes + 1)))


def cor_por_quantil(valor: float | None, quebras: np.ndarray) -> str:
    """`valor` -> hex na rampa sequencial, pela classe de `quebras`
    (`calcula_quebras_quantis`). `None`/NaN -> cor neutra de "sem dado"."""
    if valor is None or pd.isna(valor):
        return _COR_SEM_DADO
    classe = int(np.searchsorted(quebras, valor, side="right")) - 1
    classe = min(max(classe, 0), len(_CORES_SEQUENCIAL) - 1)
    return _CORES_SEQUENCIAL[classe]


def _adiciona_cor_sequencial(df: pd.DataFrame, coluna: str) -> pd.DataFrame:
    df = df.copy()
    quebras = calcula_quebras_quantis(df[coluna])
    df["cor"] = df[coluna].apply(lambda v: cor_por_quantil(v, quebras))
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
    """Lê `res_presidente_{ano}_t{turno}_municipio.parquet` — a camada
    **observada** (bate exato com o TSE, soma de `res_..._zona_mun` por
    `cd_mun`), usada na visão nacional.
    """
    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_municipio.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode processa_turno.py primeiro.")
    df = pd.read_parquet(caminho)
    return _adiciona_margem_assinada(df)


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


def carrega_resultados_local(ano: int, turno: int) -> pd.DataFrame:
    """Lê `res_presidente_{ano}_t{turno}_local.parquet` — valores observados
    por local (sem locais especiais excluídos: eles têm resultado, só não
    entram na distribuição por setor — ver `resultados.agrega_por_setor`)."""
    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_local.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode processa_turno.py primeiro.")
    df = pd.read_parquet(caminho)
    return _adiciona_margem_assinada(df)


def monta_pontos(ano: int, turno: int) -> gpd.GeoDataFrame:
    """Pontos dos locais de votação (etapa 02) + resultados por local.

    Descarta locais `flag_sem_coord` (sem nenhuma coordenada resolvida, nem
    o fallback de centróide do município) — não têm o que mostrar num mapa
    de pontos. Arredonda a coordenada para ~1 m (`grid_size=0.00001`, bem
    mais fino que a simplificação de polígono, já que aqui não há forma a
    simplificar, só a precisão de ponto flutuante do float64 original).
    """
    caminho_geom = GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}.gpkg"
    if not caminho_geom.exists():
        raise FileNotFoundError(
            f"{caminho_geom} não existe — rode 02_geocodificacao_locais.qmd (R)."
        )
    gdf = gpd.read_file(caminho_geom)

    if "flag_sem_coord" in gdf.columns:
        sem_coord = gdf["flag_sem_coord"].fillna(False).astype(bool)
        if sem_coord.any():
            logger.info(
                "Pontos: %d/%d locais sem coordenada — descartados.", sem_coord.sum(), len(gdf)
            )
        gdf = gdf.loc[~sem_coord].copy()

    gdf["geometry"] = shapely.set_precision(gdf.geometry.values, grid_size=0.00001)

    resultados_local = carrega_resultados_local(ano, turno)
    gdf = gdf.merge(resultados_local, on="id_local", how="left")
    n_sem_resultado = gdf["qt_comparecimento"].isna().sum() if "qt_comparecimento" in gdf else 0
    if n_sem_resultado:
        logger.warning(
            "Pontos: %d/%d locais sem resultado (fora da fonte de votos deste turno).",
            n_sem_resultado, len(gdf),
        )
    return gdf


def monta_pontos_presidente(ano: int, turno: int) -> gpd.GeoDataFrame:
    """Pontos coloridos pela margem Lula−Bolsonaro (mesma escala dos polígonos)."""
    gdf = monta_pontos(ano, turno)
    gdf.loc[gdf["cor"].isna(), "cor"] = _COR_SEM_DADO
    return gdf


def monta_pontos_branco_nulo(ano: int, turno: int) -> gpd.GeoDataFrame:
    """Pontos coloridos por `pc_brancos_nulos` (sobre o comparecimento — os
    votos totais), em escala sequencial (sem polo bom/ruim)."""
    gdf = monta_pontos(ano, turno)
    return _adiciona_cor_sequencial(gdf, "pc_brancos_nulos")


def monta_pontos_abstencao(ano: int, turno: int) -> gpd.GeoDataFrame:
    """Pontos coloridos por `pc_abstencao` (sobre os inscritos/aptos), em
    escala sequencial."""
    gdf = monta_pontos(ano, turno)
    return _adiciona_cor_sequencial(gdf, "pc_abstencao")


def monta_mudanca_municipio(ano_antes: int, ano_depois: int) -> gpd.GeoDataFrame:
    """Malha de municípios + `mudanca_presidente_{ano_antes}_{ano_depois}_t2_municipio.parquet`.

    Cor: escala divergente sobre `var_pc_validos_pt` (mesmos polos da margem
    Lula×Bolsonaro — positivo = foi em direção ao PT, negativo = em direção
    ao Bolsonaro), com domínio simétrico em vez de min/max observado (um
    município que virou 40 p.p. não deveria "estourar" a escala e esmagar a
    variação típica, bem menor, do resto do país).
    """
    caminho_municipios = PROCESSADO_DIR / "aux" / "municipios.gpkg"
    if not caminho_municipios.exists():
        raise FileNotFoundError(f"{caminho_municipios} não existe — rode 00_auxiliares.qmd (R).")
    caminho_mudanca = (
        RESULTADOS_DIR / f"mudanca_presidente_{ano_antes}_{ano_depois}_t2_municipio.parquet"
    )
    if not caminho_mudanca.exists():
        raise FileNotFoundError(
            f"{caminho_mudanca} não existe — rode scripts/06_mudanca_votacao.py "
            f"--de {ano_antes} --para {ano_depois}."
        )

    gdf = gpd.read_file(caminho_municipios)[["cd_mun", "geometry"]]
    gdf["geometry"] = simplifica_geometria(gdf.geometry.values, tolerancia=0.0005, grid_size=0.0001)
    n_vazias = gdf.geometry.is_empty.sum()
    if n_vazias:
        logger.warning(
            "Mudança %d-%d: %d/%d municípios viraram geometria vazia ao simplificar — descartados.",
            ano_antes, ano_depois, n_vazias, len(gdf),
        )
        gdf = gdf.loc[~gdf.geometry.is_empty].copy()

    mudanca = pd.read_parquet(caminho_mudanca)
    limite = mudanca["var_pc_validos_pt"].abs().max()
    limite = limite if pd.notna(limite) and limite > 0 else None
    mudanca["cor"] = mudanca["var_pc_validos_pt"].apply(
        lambda v: _COR_SEM_DADO if pd.isna(v) or limite is None else cor_por_margem(v / limite)
    )

    gdf = gdf.merge(mudanca, on="cd_mun", how="left")
    gdf.loc[gdf["cor"].isna(), "cor"] = _COR_SEM_DADO
    return gdf


def _pc_fmt(valor) -> str:
    return "—" if pd.isna(valor) else f"{100 * valor:.1f}%"


def _qt_fmt(valor) -> str:
    return "—" if pd.isna(valor) else f"{int(valor):,}".replace(",", ".")


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


CAMPOS_MARGEM = [
    ("comparecimento_fmt", "Comparecimento:"),
    ("lula_fmt", "Lula (13):"),
    ("bolsonaro_fmt", "Bolsonaro (22):"),
    ("vencedor_fmt", "Vencedor:"),
    ("margem_fmt", "Margem (Lula − Bolsonaro):"),
]


def campos_pontos_presidente(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, list[tuple[str, str]]]:
    return _adiciona_campos_exibicao(gdf), CAMPOS_MARGEM


def campos_pontos_branco_nulo(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, list[tuple[str, str]]]:
    gdf = gdf.copy()
    gdf["comparecimento_qt_fmt"] = gdf["qt_comparecimento"].apply(_qt_fmt)
    gdf["brancos_nulos_fmt"] = gdf["pc_brancos_nulos"].apply(_pc_fmt)
    campos = [
        ("comparecimento_qt_fmt", "Comparecimento (votos):"),
        ("brancos_nulos_fmt", "Brancos + nulos:"),
    ]
    return gdf, campos


def campos_pontos_abstencao(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, list[tuple[str, str]]]:
    gdf = gdf.copy()
    gdf["aptos_qt_fmt"] = gdf["qt_aptos"].apply(_qt_fmt)
    gdf["abstencao_fmt"] = gdf["pc_abstencao"].apply(_pc_fmt)
    campos = [
        ("aptos_qt_fmt", "Inscritos:"),
        ("abstencao_fmt", "Abstenção:"),
    ]
    return gdf, campos


def campos_mudanca_municipio(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, list[tuple[str, str]]]:
    gdf = gdf.copy()
    gdf["var_pt_fmt"] = gdf["var_pc_validos_pt"].apply(
        lambda v: "—" if pd.isna(v) else f"{100 * v:+.1f} p.p."
    )
    gdf["virada_fmt"] = gdf["sentido_virada"].fillna("—")
    # Município que virou de vencedor ganha um contorno mais grosso no mapa
    # (ver `gera_mapa`) — destaque visual além da cor, para não depender só
    # da tonalidade (o Δ pode ser pequeno mesmo numa virada apertada).
    gdf["_contorno_largura"] = gdf["mudou_vencedor"].fillna(False).map({True: 1.4, False: 0.3})
    campos = [
        ("var_pt_fmt", "Δ PT (p.p.):"),
        ("virada_fmt", "Situação:"),
    ]
    return gdf, campos


def gera_mapa(
    gdf: gpd.GeoDataFrame,
    caminho_saida: Path,
    *,
    col_id: str,
    rotulo_id: str,
    campos: list[tuple[str, str]] | None = None,
    pontos: bool = False,
) -> Path:
    """Constrói e salva um `folium.Map` coroplético (ou de pontos) a partir de `gdf`.

    `gdf` já deve ter a coluna `cor` (hex), mais as colunas de exibição já
    formatadas em texto (ver `_adiciona_campos_exibicao`/`campos_pontos_*`).
    `campos` é a lista `(coluna, rótulo)` do tooltip, sem `col_id` — que
    sempre entra primeiro. Se `None` (padrão, usado pelos mapas por
    polígono já existentes), usa `_adiciona_campos_exibicao` +
    `CAMPOS_MARGEM`, preservando o comportamento anterior.

    `pontos=True` desenha `gdf` como marcadores (`folium.CircleMarker`) em
    vez de polígonos preenchidos — usado pelos mapas de locais de votação.
    `prefer_canvas=True` no mapa é obrigatório nesse caso: ~92 mil
    marcadores SVG (o padrão do Leaflet) travam o navegador; canvas resolve.

    Uma única camada `folium.GeoJson` para o `gdf` inteiro (não uma por
    feição) — um `folium.GeoJson`/`Popup` por linha, com dezenas de milhares
    de feições, incharia o HTML e travaria o navegador; o padrão do Folium
    para isso é `style_function` lendo a propriedade de cada feição do
    próprio GeoJSON, mais `GeoJsonTooltip` declarativo.
    """
    if not CARTO_API_KEY:
        raise RuntimeError(
            "CARTO_API_KEY não configurada — copie .env.example para .env e "
            "preencha (ver src/eleicoes/config.py). Os tiles de fundo não "
            "carregam sem ela."
        )

    gdf = gdf.to_crs(CRS_SIRGAS2000)
    if campos is None:
        gdf = _adiciona_campos_exibicao(gdf)
        campos = CAMPOS_MARGEM
    minx, miny, maxx, maxy = gdf.total_bounds

    mapa = folium.Map(
        tiles=TILES_CARTO_POSITRON, attr=TILES_CARTO_ATTR, control_scale=True, prefer_canvas=pontos
    )
    mapa.fit_bounds([[miny, minx], [maxy, maxx]])

    campos_nomes = [col_id, *(c for c, _ in campos)]
    apelidos = [f"{rotulo_id}:", *(a for _, a in campos)]

    tem_contorno_custom = "_contorno_largura" in gdf.columns
    colunas_geojson = list(
        dict.fromkeys(
            [*campos_nomes, "cor", *(["_contorno_largura"] if tem_contorno_custom else []), "geometry"]
        )
    )
    marcador = folium.CircleMarker(radius=3, weight=0, fill=True, fill_opacity=0.85) if pontos else None
    folium.GeoJson(
        gdf[colunas_geojson],
        marker=marcador,
        style_function=(
            (lambda feature: {
                "fillColor": feature["properties"]["cor"],
                "color": feature["properties"]["cor"],
                "fillOpacity": 0.85 if feature["properties"]["cor"] != _COR_SEM_DADO else 0.35,
            })
            if pontos
            else (lambda feature: {
                "fillColor": feature["properties"]["cor"],
                "color": "#ffffff",
                "weight": feature["properties"].get("_contorno_largura", 0.3),
                "fillOpacity": 0.75 if feature["properties"]["cor"] != _COR_SEM_DADO else 0.35,
            })
        ),
        tooltip=folium.GeoJsonTooltip(fields=campos_nomes, aliases=apelidos, sticky=True),
        popup=(
            None if pontos
            else folium.GeoJsonPopup(fields=campos_nomes, aliases=apelidos, max_width=280)
        ),
    ).add_to(mapa)

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    mapa.save(str(caminho_saida))
    return caminho_saida
