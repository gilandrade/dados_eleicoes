"""Alocação espacial: distribui votos por local de votação para setores 2022.

Método "Voronoi dasimétrico sobre os domicílios do CNEFE" (ver PLAN.md > Pesquisa):

1. cada domicílio do CNEFE vai para o local de votação mais próximo do mesmo
   município (`aloca_domicilios_a_locais`);
2. os domicílios de cada (local, setor) são convertidos em população adulta
   estimada, pela participação do setor nos domicílios da célula
   (`constroi_pesos_local_setor`);
3. a matriz de pesos resultante (`id_local`, `cd_setor`, `peso`) é reaproveitada
   todo turno: `votos_setor = pesos @ votos_local` (ver `resultados.py`).

A matriz é cara (calcula vizinho mais próximo para cada domicílio do Brasil) e
por isso é calculada uma vez por ano, antes da eleição (`scripts/03_pesos_alocacao.py`),
nunca na noite da apuração.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint

from .config import CRS_METRICO_BR, CRS_SIRGAS2000


def decodifica_wkb(df: pd.DataFrame, coluna_geom: str = "geometry") -> gpd.GeoDataFrame:
    """Decodifica uma coluna WKB crua (o formato de saída do `cnefe-etl`).

    `cnefe-etl` grava a geometria com `sf::st_as_binary()` + `arrow::write_parquet()`,
    sem metadados GeoParquet — `gpd.read_parquet()` não funciona nesse arquivo.

    `shapely.from_wkb` só aceita `bytes`/`str`, mas o duckdb (usado para
    filtrar por município antes de ler) devolve a coluna binária como
    `bytearray` — daí o `bytes(x)` explícito, que não muda nada quando já
    vem como `bytes` (leitura direta via `pd.read_parquet`).
    """
    valores = df[coluna_geom].map(bytes).to_numpy()
    geom = shapely.from_wkb(valores)
    return gpd.GeoDataFrame(
        df.drop(columns=[coluna_geom]), geometry=geom, crs=CRS_SIRGAS2000
    )


def aloca_domicilios_a_locais(
    domicilios: gpd.GeoDataFrame,
    locais: gpd.GeoDataFrame,
    *,
    col_mun_domicilios: str = "cd_mun",
    col_mun_locais: str = "cd_mun",
    col_id_local: str = "id_local",
) -> pd.Series:
    """Para cada domicílio, o `id_local` do local mais próximo no mesmo município.

    Usa uma KD-tree por município em CRS métrico (`CRS_METRICO_BR`), o que
    equivale a avaliar o diagrama de Voronoi dos locais elegíveis nos pontos de
    domicílio, sem construir os polígonos.

    Domicílios em municípios sem nenhum local elegível (não deveria ocorrer, mas
    é medido) resultam em `pd.NA`.

    Args:
        domicilios: pontos de domicílio (ex.: CNEFE espécies 1/2), com uma
            coluna de código de município.
        locais: pontos de locais de votação **elegíveis** (sem voto em
            trânsito nem seções especiais), com `id_local` e código de
            município.
        col_mun_domicilios: nome da coluna de município em `domicilios`.
        col_mun_locais: nome da coluna de município em `locais`.
        col_id_local: nome da coluna de id em `locais`.

    Returns:
        `pd.Series` alinhada ao índice de `domicilios`, com o `id_local` mais
        próximo (mesmo município) ou `pd.NA`.
    """
    dom_m = domicilios.to_crs(CRS_METRICO_BR)
    loc_m = locais.to_crs(CRS_METRICO_BR)

    resultado = pd.Series(pd.NA, index=domicilios.index, dtype="object")

    dom_xy = np.column_stack([dom_m.geometry.x, dom_m.geometry.y])
    loc_xy = np.column_stack([loc_m.geometry.x, loc_m.geometry.y])

    for cd_mun, idx_dom in dom_m.groupby(col_mun_domicilios).groups.items():
        idx_loc = loc_m.index[loc_m[col_mun_locais] == cd_mun]
        if len(idx_loc) == 0:
            continue  # município sem local elegível — fica NA, é medido depois

        pos_dom = dom_m.index.get_indexer(idx_dom)
        pos_loc = loc_m.index.get_indexer(idx_loc)

        arvore = cKDTree(loc_xy[pos_loc])
        _, vizinho = arvore.query(dom_xy[pos_dom])

        resultado.loc[idx_dom] = loc_m.loc[idx_loc, col_id_local].to_numpy()[vizinho]

    return resultado


def constroi_pesos_local_setor(
    domicilios_com_local: pd.DataFrame,
    pop18_setor: pd.DataFrame,
    *,
    col_id_local: str = "id_local",
    col_setor: str = "cd_setor",
    col_pop18: str = "pop18",
) -> pd.DataFrame:
    """Constrói a matriz de pesos `id_local -> cd_setor`.

    `adultos(L, s) = n_dom(L ∩ s) * pop18(s) / n_dom(s)`, normalizado depois
    para que `Σ_s peso(L, s) = 1` em cada local — a verificação de conservação
    do PLAN.md.

    Args:
        domicilios_com_local: uma linha por domicílio, com `col_id_local` (de
            `aloca_domicilios_a_locais`) e `col_setor`.
        pop18_setor: uma linha por setor, com `col_setor` e `col_pop18`
            (população adulta estimada — ver `pop18_por_setor`).
        col_id_local: nome da coluna de id do local.
        col_setor: nome da coluna de setor.
        col_pop18: nome da coluna de população adulta do setor.

    Returns:
        DataFrame com `col_id_local`, `col_setor`, `n_dom`, `adultos`, `peso`.
    """
    n_dom_local_setor = (
        domicilios_com_local.dropna(subset=[col_id_local])
        .groupby([col_id_local, col_setor])
        .size()
        .rename("n_dom")
        .reset_index()
    )
    n_dom_setor = (
        domicilios_com_local.groupby(col_setor).size().rename("n_dom_setor")
    )

    pesos = n_dom_local_setor.merge(n_dom_setor, on=col_setor, how="left")
    pesos = pesos.merge(pop18_setor[[col_setor, col_pop18]], on=col_setor, how="left")

    pesos["adultos"] = (
        pesos["n_dom"] * pesos[col_pop18].fillna(0) / pesos["n_dom_setor"]
    )

    total_por_local = pesos.groupby(col_id_local)["adultos"].transform("sum")
    # Local cuja célula não tem nenhum domicílio com pop18 > 0 (ex.: só espécie
    # comercial no CNEFE, ou setor sem dado de população): distribui igualmente
    # entre os setores da célula, para não perder o local no total.
    pesos["peso"] = np.where(
        total_por_local > 0,
        pesos["adultos"] / total_por_local,
        1.0 / pesos.groupby(col_id_local)[col_setor].transform("count"),
    )

    return pesos[[col_id_local, col_setor, "n_dom", "adultos", "peso"]]


def pop18_por_setor(
    cn22_pop01_abs: pd.DataFrame, cn22_pop03_abs: pd.DataFrame
) -> pd.DataFrame:
    """`pop18(s) = total(s) - [0,4] - [5,9] - [10,14] - 0,6 * [15,19]`.

    Aproxima a população com 18 anos ou mais a partir das faixas quinquenais do
    Censo 2022 (`cn22-pop`), supondo distribuição uniforme dentro da faixa
    15–19 (3 dos 5 anos são maiores de idade).

    NÃO é o caminho usado pelo pipeline: `00_auxiliares.qmd` (R) já calcula e
    grava `pop18_setor.parquet`, e `scripts/03_pesos_alocacao.py` só lê esse
    arquivo (`config.POP18_SETOR_PARQUET`) — ver CLAUDE.md. Esta função existe
    como referência da mesma conta em Python e para reuso fora do pipeline
    principal; mantida em sincronia com a versão em R.

    Args:
        cn22_pop01_abs: `cn22_pop01_abs.csv` do `cn22-pop` — atenção: a chave
            de setor ali é `cod_setor` (não `cd_setor`), e o total é o
            indicador nomeado `cn22_pop01_res_tot_0` (não a variável bruta do
            IBGE `V0001`). Renomear antes de chamar esta função.
        cn22_pop03_abs: `cn22_pop03_abs.csv`, com as mesmas ressalvas de nomes
            — faixas etárias absolutas: `cn22_pop03_0a4_tot_0`,
            `cn22_pop03_5a9_tot_0`, `cn22_pop03_10a14_tot_0`,
            `cn22_pop03_15a19_tot_0`.

    Returns:
        DataFrame com `cd_setor` e `pop18`.
    """
    total = cn22_pop01_abs[["cd_setor", "cn22_pop01_res_tot_0"]].rename(
        columns={"cn22_pop01_res_tot_0": "total"}
    )
    faixas = cn22_pop03_abs[
        ["cd_setor", "cn22_pop03_0a4_tot_0", "cn22_pop03_5a9_tot_0",
         "cn22_pop03_10a14_tot_0", "cn22_pop03_15a19_tot_0"]
    ]

    df = total.merge(faixas, on="cd_setor", how="left").fillna(0)
    df["pop18"] = (
        df["total"]
        - df["cn22_pop03_0a4_tot_0"]
        - df["cn22_pop03_5a9_tot_0"]
        - df["cn22_pop03_10a14_tot_0"]
        - 0.6 * df["cn22_pop03_15a19_tot_0"]
    ).clip(lower=0)

    return df[["cd_setor", "pop18"]]


def poligonos_voronoi_por_municipio(
    locais: gpd.GeoDataFrame,
    limites_municipio: gpd.GeoDataFrame,
    *,
    col_id_local: str = "id_local",
    col_mun: str = "cd_mun",
) -> gpd.GeoDataFrame:
    """Voronoi dos locais elegíveis, município a município, recortado pelo limite.

    Locais com coordenada idêntica (mesmo prédio, seções diferentes já
    consolidadas em um só ponto na etapa 01) são fundidos antes: o Voronoi não
    aceita pontos duplicados, e `id_local` do grupo passa a ser o menor id.

    Args:
        locais: pontos de locais elegíveis, com `col_id_local` e `col_mun`.
        limites_municipio: polígonos dos municípios (geobr), com `col_mun`.
        col_id_local: nome da coluna de id do local.
        col_mun: nome da coluna de município (em ambas as tabelas).

    Returns:
        GeoDataFrame de polígonos, `col_id_local` + `col_mun`, em CRS_SIRGAS2000.
    """
    locais_m = locais.to_crs(CRS_METRICO_BR)
    limites_m = limites_municipio.to_crs(CRS_METRICO_BR)

    saidas = []
    for cd_mun, grupo in locais_m.groupby(col_mun):
        limite = limites_m.loc[limites_m[col_mun] == cd_mun]
        if limite.empty:
            continue
        limite_geom = limite.geometry.iloc[0]

        # Funde pontos duplicados: id_local do grupo = o menor id.
        grupo = grupo.copy()
        grupo["_xy"] = list(zip(grupo.geometry.x.round(1), grupo.geometry.y.round(1)))
        grupo["_id_grupo"] = grupo.groupby("_xy")[col_id_local].transform("min")
        pontos_unicos = grupo.drop_duplicates("_xy")

        if len(pontos_unicos) < 2:
            # Um só local no município: a área é o município inteiro.
            saidas.append(
                {col_id_local: pontos_unicos[col_id_local].iloc[0], col_mun: cd_mun,
                 "geometry": limite_geom}
            )
            continue

        multiponto = MultiPoint(list(pontos_unicos.geometry))
        celulas = shapely.voronoi_polygons(multiponto, extend_to=limite_geom)

        for celula in celulas.geoms:
            celula_recortada = celula.intersection(limite_geom)
            if celula_recortada.is_empty:
                continue
            centro = celula.representative_point()
            dists = pontos_unicos.geometry.distance(centro)
            id_mais_proximo = pontos_unicos.loc[dists.idxmin(), col_id_local]
            saidas.append(
                {col_id_local: id_mais_proximo, col_mun: cd_mun,
                 "geometry": celula_recortada}
            )

    gdf = gpd.GeoDataFrame(saidas, crs=CRS_METRICO_BR)
    return gdf.dissolve(by=[col_id_local, col_mun]).reset_index().to_crs(CRS_SIRGAS2000)


def constroi_zona_mun(
    setores: gpd.GeoDataFrame,
    locais: pd.DataFrame,
    pesos: pd.DataFrame,
    municipios: gpd.GeoDataFrame,
    *,
    col_setor: str = "cd_setor",
    col_id_local: str = "id_local",
    col_mun: str = "cd_mun",
    col_zona: str = "nr_zona",
) -> gpd.GeoDataFrame:
    """Constrói a camada `id_zona_mun` (ver PLAN.md > Pesquisa > Zonas eleitorais).

    - Município com **uma só zona**: a geometria é o próprio polígono do IBGE,
      exata (`geom_estimada = False`).
    - Município com **várias zonas**: cada setor é atribuído à zona que recebe
      a maior parte dos seus adultos (via `pesos` + `locais`), e os setores da
      mesma zona são fundidos (`geom_estimada = True`).

    Args:
        setores: polígonos dos setores censitários 2022, com `col_setor`.
        locais: tabela de locais, com `col_id_local`, `col_mun`, `col_zona`.
        pesos: matriz de pesos local -> setor (de `constroi_pesos_local_setor`).
        municipios: polígonos dos municípios (geobr), com `col_mun`.
        col_setor, col_id_local, col_mun, col_zona: nomes das colunas.

    Returns:
        GeoDataFrame com `id_zona_mun`, `col_mun`, `col_zona`, `n_zonas_mun`,
        `geom_estimada`.
    """
    n_zonas_mun = locais.groupby(col_mun)[col_zona].nunique().rename("n_zonas_mun")
    locais_zona = locais[[col_id_local, col_mun, col_zona]].merge(
        n_zonas_mun, on=col_mun, how="left"
    )

    municipios_1zona = municipios.merge(
        n_zonas_mun[n_zonas_mun == 1].reset_index(), on=col_mun, how="inner"
    ).merge(
        locais_zona[[col_mun, col_zona]].drop_duplicates(), on=col_mun, how="left"
    )
    municipios_1zona["geom_estimada"] = False

    # Municípios com várias zonas: setor -> zona de maior peso.
    setor_zona = (
        pesos.merge(locais_zona, on=col_id_local, how="inner")
        .loc[lambda d: d["n_zonas_mun"] > 1]
        .groupby([col_setor, col_mun, col_zona])["adultos"]
        .sum()
        .reset_index()
        .sort_values("adultos", ascending=False)
        .drop_duplicates(col_setor)
    )
    dissolvidos = (
        # setores[[col_setor, "geometry"]]: `setores` também tem seu próprio
        # `col_mun` (CD_MUN da malha do IBGE), que colidiria em silêncio com
        # o de `setor_zona` no merge (pandas sufixa cd_mun_x/cd_mun_y e o
        # `col_mun` puro some — achado rodando de verdade). `setor_zona` já
        # é a fonte de verdade do município aqui.
        setores[[col_setor, "geometry"]]
        .merge(setor_zona, on=col_setor, how="inner")
        .dissolve(by=[col_mun, col_zona])
        .reset_index()
        .merge(n_zonas_mun, on=col_mun, how="left")
    )
    dissolvidos["geom_estimada"] = True

    colunas = [col_mun, col_zona, "n_zonas_mun", "geom_estimada", "geometry"]
    saida = pd.concat(
        [municipios_1zona[colunas], dissolvidos[colunas]], ignore_index=True
    )
    saida = gpd.GeoDataFrame(saida, geometry="geometry", crs=setores.crs)
    saida["id_zona_mun"] = (
        saida[col_mun].astype(str) + saida[col_zona].astype(int).astype(str).str.zfill(4)
    )
    return saida.to_crs(CRS_SIRGAS2000)


def mede_cobertura_setor(
    domicilios: pd.DataFrame,
    pop18_setor: pd.DataFrame,
    *,
    col_setor: str = "cd_setor",
) -> pd.DataFrame:
    """% de domicílios cujo `cd_setor` não existe na malha oficial (`pop18_setor`).

    Achado real (29/09/2026, ver CLAUDE.md): o `COD_SETOR` do CNEFE, mesmo
    depois de tirar o sufixo `"P"`, não bate com a malha 2022 do IBGE em
    ~20% dos casos — cadastro de endereços numa safra diferente da malha de
    resultados. Esses domicílios contam para `n_dom` (denominador), mas têm
    `pop18` implícito de 0, então **não** puxam peso para o setor fantasma —
    a menos que TODOS os domicílios da célula (local × setor) sejam
    fantasma, caso em que o peso cai no fallback de distribuição igual (ver
    `constroi_pesos_local_setor`) e o setor fantasma acaba aparecendo na
    matriz de pesos mesmo assim, sem geometria correspondente. Rodar isto
    por município antes de publicar, para saber onde o problema é grande.

    Returns:
        DataFrame com `cd_mun`, `n_dom`, `n_dom_setor_valido`,
        `pc_dom_setor_invalido`.
    """
    setores_validos = set(pop18_setor[col_setor])
    df = domicilios.copy()
    df["setor_valido"] = df[col_setor].isin(setores_validos)

    resumo = (
        df.groupby("cd_mun")
        .agg(n_dom=("setor_valido", "size"), n_dom_setor_valido=("setor_valido", "sum"))
        .reset_index()
    )
    resumo["pc_dom_setor_invalido"] = 1 - resumo["n_dom_setor_valido"] / resumo["n_dom"]
    return resumo


def diagnostico_razao_eleitores(
    locais: pd.DataFrame,
    pesos: pd.DataFrame,
    *,
    col_id_local: str = "id_local",
    col_mun: str = "cd_mun",
    col_qt_eleitores: str = "qt_eleitores",
) -> pd.DataFrame:
    """`razao_eleitores`: eleitores do local / adultos estimados na sua célula,
    normalizada pela mesma razão no município.

    Valores muito diferentes de 1 apontam locais que atraem eleitores de longe
    (a célula geográfica subestima quem vota ali — caso descrito para a Maré em
    Silva et al. 2022, ver PLAN.md) ou células sobredimensionadas.

    Returns:
        DataFrame com `col_id_local`, `adultos_estimados`, `razao_eleitores`.
    """
    adultos_local = pesos.groupby(col_id_local)["adultos"].sum().rename(
        "adultos_estimados"
    )
    diag = locais[[col_id_local, col_mun, col_qt_eleitores]].merge(
        adultos_local, on=col_id_local, how="left"
    )
    diag["razao_bruta"] = diag[col_qt_eleitores] / diag["adultos_estimados"].replace(
        0, np.nan
    )
    razao_mun = diag.groupby(col_mun)["razao_bruta"].transform("mean")
    diag["razao_eleitores"] = diag["razao_bruta"] / razao_mun
    return diag[[col_id_local, "adultos_estimados", "razao_eleitores"]]
