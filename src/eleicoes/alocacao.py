"""Alocação espacial: distribui votos por local de votação para setores 2022.

Método "Voronoi dasimétrico sobre os domicílios do CNEFE" (ver PLAN.md > Pesquisa):

1. cada domicílio do CNEFE vai para o local de votação mais próximo do mesmo
   município (`conta_domicilios_por_local_setor`);
2. os domicílios de cada (local, setor) são convertidos em população adulta
   estimada, pela participação do setor nos domicílios da célula
   (`calcula_pesos_local_setor`);
3. a matriz de pesos resultante (`id_local`, `cd_setor`, `peso`) é reaproveitada
   todo turno: `votos_setor = pesos @ votos_local` (ver `resultados.py`).

A matriz é cara (calcula vizinho mais próximo para cada domicílio do Brasil) e
por isso é calculada uma vez por ano, antes da eleição (`03_pesos_alocacao.qmd`),
nunca na noite da apuração.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint

from .config import CRS_METRICO_BR, CRS_SIRGAS2000

logger = logging.getLogger(__name__)

# Uma ponte (setores que ligam uma parte de 1 local ao corpo da zona) só é
# aplicada se não mudar a soma estimada de inscritos de nenhuma zona envolvida
# em mais que esta fração da soma real (inscritos dos locais da zona). Decisão
# do Gil em 30/09/2026; na malha de 2022 nenhuma ponte passou de 3,3%.
LIMITE_PONTE_INSCRITOS = 0.05
FRONTEIRA_MINIMA_M = 1.0  # contato só num ponto não conta como vizinhança
MAX_ITERACOES_CONTINUIDADE = 20
# Experimental: deixa uma ponte cortar outra zona em pedaços (padrão: recusa).
PERMITE_PONTE_CORTAR_ZONA = False


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


def conta_domicilios_por_local_setor(
    arquivos_cnefe: list[Path],
    locais: gpd.GeoDataFrame,
    setores_validos: pd.Index,
    *,
    especies: tuple[int, ...],
    col_mun_locais: str = "cd_mun",
    col_id_local: str = "id_local",
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, dict[str, int]]:
    """Aloca cada domicílio ao local mais próximo do município e conta, **um
    município por vez**, sem nunca montar os ~90 milhões de pontos do Brasil.

    O vizinho mais próximo (KD-tree em `CRS_METRICO_BR`, equivale a avaliar o
    Voronoi dos locais elegíveis nos domicílios) é calculado com arrays numpy
    de coordenadas projetadas, sem objetos shapely. A versão anterior
    construía um GeoDataFrame com todos os domicílios (um `Point` por linha) e
    ainda uma cópia reprojetada: dezenas de GB de RAM no Brasil inteiro. Aqui o
    pico é o maior município (São Paulo, ~5 milhões de linhas, ~200 MB) e o
    que sobra no fim são só as contagens por (local, setor).

    Args:
        arquivos_cnefe: um `cnefe_{cd_mun}.parquet` por município, com
            `cd_mun`, `cd_setor`, `cod_especie`, `latitude` e `longitude`
            (EPSG:4674).
        locais: pontos de locais de votação **elegíveis**, com `col_id_local`
            e `col_mun_locais`.
        setores_validos: códigos de setor da malha oficial (`pop18`), para a
            cobertura (ver `mede_cobertura_setor`).
        especies: `COD_ESPECIE` tratados como domicílio.

    Returns:
        - `n_dom_local_setor`: `col_id_local`, `cd_setor`, `n_dom`;
        - `n_dom_setor`: Series `n_dom_setor` indexada por `cd_setor`, contando
          **todos** os domicílios, inclusive os sem local elegível (mesma
          conta de `calcula_pesos_local_setor`);
        - `cobertura`: `cd_mun`, `n_dom`, `n_dom_setor_valido`,
          `pc_dom_setor_invalido`;
        - `totais`: `n_dom` e `n_sem_local` (município sem local elegível).
    """
    loc_m = locais.to_crs(CRS_METRICO_BR)
    loc_xy = np.column_stack([loc_m.geometry.x, loc_m.geometry.y])
    ids = loc_m[col_id_local].to_numpy()
    arvores = {}
    for cd_mun, posicoes in loc_m.groupby(col_mun_locais).indices.items():
        arvores[cd_mun] = (cKDTree(loc_xy[posicoes]), ids[posicoes])

    transformador = Transformer.from_crs(CRS_SIRGAS2000, CRS_METRICO_BR, always_xy=True)
    colunas = ["cd_mun", "cd_setor", "latitude", "longitude"]

    por_local_setor, por_setor, cobertura = [], [], []
    n_dom = n_sem_local = 0
    for arquivo in arquivos_cnefe:
        df = pd.read_parquet(
            arquivo, columns=[*colunas, "cod_especie"],
            filters=[("cod_especie", "in", list(especies))],
        )[colunas]
        for cd_mun, g in df.groupby("cd_mun", sort=False):
            valido = setores_validos.get_indexer(g["cd_setor"]) >= 0
            cobertura.append(
                {"cd_mun": cd_mun, "n_dom": len(g), "n_dom_setor_valido": int(valido.sum())}
            )
            n_dom += len(g)
            por_setor.append(g.groupby("cd_setor").size())

            if cd_mun not in arvores:
                n_sem_local += len(g)  # município sem local elegível: é medido
                continue
            arvore, ids_mun = arvores[cd_mun]
            x, y = transformador.transform(g["longitude"].to_numpy(), g["latitude"].to_numpy())
            _, vizinho = arvore.query(np.column_stack([x, y]))
            por_local_setor.append(
                pd.DataFrame({col_id_local: ids_mun[vizinho], "cd_setor": g["cd_setor"].to_numpy()})
                .groupby([col_id_local, "cd_setor"]).size().rename("n_dom").reset_index()
            )

    n_dom_local_setor = (
        pd.concat(por_local_setor, ignore_index=True)
        .groupby([col_id_local, "cd_setor"], as_index=False)["n_dom"].sum()
    )
    n_dom_setor = pd.concat(por_setor).groupby(level=0).sum().rename("n_dom_setor")
    n_dom_setor.index.name = "cd_setor"

    cobertura = pd.DataFrame(cobertura)
    cobertura["pc_dom_setor_invalido"] = 1 - cobertura["n_dom_setor_valido"] / cobertura["n_dom"]
    return n_dom_local_setor, n_dom_setor, cobertura, {"n_dom": n_dom, "n_sem_local": n_sem_local}


def calcula_pesos_local_setor(
    n_dom_local_setor: pd.DataFrame,
    n_dom_setor: pd.Series,
    pop18_setor: pd.DataFrame,
    *,
    col_id_local: str = "id_local",
    col_setor: str = "cd_setor",
    col_pop18: str = "pop18",
) -> pd.DataFrame:
    """Constrói a matriz de pesos `id_local -> cd_setor` a partir das contagens.

    `adultos(L, s) = n_dom(L ∩ s) * pop18(s) / n_dom(s)`, normalizado depois
    para que `Σ_s peso(L, s) = 1` em cada local — a verificação de conservação
    do PLAN.md.

    Args:
        n_dom_local_setor: `col_id_local`, `col_setor` e `n_dom` (domicílios
            de cada célula local × setor).
        n_dom_setor: Series `n_dom_setor` indexada por `col_setor`, com todos
            os domicílios do setor.
        pop18_setor: uma linha por setor, com `col_setor` e `col_pop18`
            (população adulta estimada — ver `pop18_por_setor`).

    Returns:
        DataFrame com `col_id_local`, `col_setor`, `n_dom`, `adultos`, `peso`.
    """
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

    NÃO é o caminho usado pelo pipeline: `00_insumos.qmd` (R) já calcula e
    grava `pop18_setor.parquet`, e `03_pesos_alocacao.qmd` só lê esse
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


def local_mais_proximo_por_tempo(
    tempos: pd.DataFrame,
    *,
    col_setor: str = "cd_setor",
    col_id_local: str = "id_local",
    col_minutos: str = "minutos",
) -> pd.DataFrame:
    """O local de menor tempo na rede para cada setor (saída de `03c_tempos_rede.qmd`).

    Args:
        tempos: `[col_setor, col_id_local, col_minutos, ...]`, um par por linha;
            `col_minutos` nulo = inalcançável.
        col_setor, col_id_local, col_minutos: nomes das colunas.

    Returns:
        `[col_setor, col_id_local, col_minutos]`, uma linha por setor com algum
        local alcançável. Empate: o primeiro por `col_id_local` (determinístico).
    """
    validos = tempos.dropna(subset=[col_minutos]).sort_values([col_setor, col_minutos, col_id_local])
    return validos.drop_duplicates(col_setor)[[col_setor, col_id_local, col_minutos]].reset_index(drop=True)


def _zona_do_local_mais_proximo(
    setores: gpd.GeoDataFrame,
    locais: gpd.GeoDataFrame,
    col_setor: str,
    col_mun: str,
    col_zona: str,
) -> pd.DataFrame:
    """`[col_setor, col_mun, col_zona, adultos=0]`: a zona do local mais
    próximo do ponto interior de cada setor, procurando só no mesmo município.

    Locais no centróide do município (`flag_sem_coord`) ficam fora da busca:
    não estão na posição real do local.
    """
    candidatos = locais
    if "flag_sem_coord" in locais.columns:
        candidatos = locais.loc[~locais["flag_sem_coord"].astype(bool)]
    candidatos = candidatos[[col_mun, col_zona, "geometry"]].to_crs(CRS_METRICO_BR)
    pontos = setores.to_crs(CRS_METRICO_BR)
    pontos["geometry"] = pontos.geometry.representative_point()

    partes = []
    for cd_mun, pontos_mun in pontos.groupby(col_mun):
        locais_mun = candidatos.loc[candidatos[col_mun] == cd_mun, [col_zona, "geometry"]]
        if locais_mun.empty:
            continue
        mais_proximo = gpd.sjoin_nearest(pontos_mun, locais_mun, how="left")
        partes.append(
            mais_proximo.drop_duplicates(col_setor)[[col_setor, col_mun, col_zona]]
        )
    if not partes:
        return pd.DataFrame(columns=[col_setor, col_mun, col_zona, "adultos"])
    return pd.concat(partes, ignore_index=True).assign(adultos=0.0)


def _torna_zonas_continuas(
    setores: gpd.GeoDataFrame,
    locais: gpd.GeoDataFrame,
    inscritos_reais: pd.Series,
    col_zona: str,
) -> tuple[np.ndarray, dict[str, int]]:
    """Corrige a zona dos setores de **um** município para as zonas ficarem contínuas.

    A regra "setor inteiro vai à zona com mais adultos" deixa, perto da
    fronteira entre as áreas de atração de dois locais, setores isolados do
    lado oposto ao dos vizinhos. Cada zona é quebrada nos seus pedaços
    contínuos (grafo de vizinhança dos setores), e:

    - pedaço **sem local da própria zona**: passa para a zona vizinha com mais
      fronteira em comum. Se não encosta em nenhuma outra zona (ilha no mar),
      fica como está;
    - pedaço com **1 local** da própria zona: é ligado ao pedaço principal
      pela ponte de setores de menos inscritos (Dijkstra), se nenhuma zona
      envolvida mudar a soma estimada de inscritos em mais de
      `LIMITE_PONTE_INSCRITOS` da soma real;
    - pedaço com **2 ou mais locais**: fica (a zona cobre dois territórios).

    Repete até estabilizar: uma ponte pode cortar outra zona e criar ilhas. O
    caminho de cada ponte usa a zona já corrigida pelas pontes anteriores da
    mesma passada. Uma ponte nunca atravessa setor com local de votação de
    outra zona nem setor já movido por outra ponte — sem essas duas travas as
    pontes oscilavam (391 "pontes" para 78 partes, na malha de 2022).

    Args:
        setores: setores do município, em CRS métrico, com `col_zona` e
            `inscritos` (estimados pela matriz de pesos).
        locais: locais do município com coordenada real, com `col_zona`.
        inscritos_reais: soma de inscritos dos locais, por zona.
        col_zona: coluna da zona.

    Returns:
        A zona corrigida de cada setor (na ordem de `setores`) e contagens.
    """
    setores = setores.reset_index(drop=True)
    n = len(setores)
    zona = setores[col_zona].to_numpy().copy()
    inscritos = setores["inscritos"].to_numpy(dtype=float)
    area = setores.area.to_numpy()
    stats = {"ilhas_absorvidas": 0, "pontes": 0, "pontes_recusadas": 0, "pontes_cortariam_zona": 0}

    par = gpd.sjoin(setores[["geometry"]], setores[["geometry"]], predicate="intersects")
    par = par.loc[par.index < par["index_right"]]
    a, b = par.index.to_numpy(), par["index_right"].to_numpy()
    bordas = setores.geometry.boundary.to_numpy()
    fronteira = shapely.length(shapely.intersection(bordas[a], bordas[b]))
    ok = fronteira > FRONTEIRA_MINIMA_M
    a, b, fronteira = a[ok], b[ok], fronteira[ok]
    origens, destinos = np.r_[a, b], np.r_[b, a]

    setor_do_local = gpd.sjoin(locais[[col_zona, "geometry"]], setores[["geometry"]], predicate="within")
    locais_por_setor = setor_do_local.groupby("index_right")[col_zona].agg(list).to_dict()
    zonas_com_local = {i: set(zs) for i, zs in locais_por_setor.items()}
    inscritos_do_local = (
        setor_do_local.assign(q=locais.loc[setor_do_local.index, "qt_eleitores"].fillna(0).to_numpy())
        .groupby(["index_right", col_zona])["q"].sum().to_dict()
    )
    # Setor que já mudou de zona por uma ponte não muda de novo: sem isso as
    # pontes oscilavam (uma corta a zona vizinha, que faz outra ponte de volta).
    fixado = np.zeros(n, dtype=bool)
    INTRANSPONIVEL = 1e12
    locais_da_propria_zona = lambda z_arr, z: np.array(  # noqa: E731
        [z in zonas_com_local.get(i, set()) for i in range(n)]
    ) & (z_arr == z)

    def pedacos_com_local(z_arr: np.ndarray, z) -> int:
        """Quantos pedaços contínuos da zona `z` têm pelo menos um local dela."""
        membros = z_arr == z
        dentro = membros[a] & membros[b]
        g = csr_matrix((np.ones(dentro.sum()), (a[dentro], b[dentro])), shape=(n, n))
        _, rot = connected_components(g, directed=False)
        return len(set(rot[locais_da_propria_zona(z_arr, z)]))

    for _ in range(MAX_ITERACOES_CONTINUIDADE):
        mesma = zona[a] == zona[b]
        g = csr_matrix((np.ones(mesma.sum() * 2), (np.r_[a[mesma], b[mesma]], np.r_[b[mesma], a[mesma]])), shape=(n, n))
        _, rotulo = connected_components(g, directed=False)
        pedacos = pd.DataFrame({"rotulo": rotulo, "zona": zona, "inscritos": inscritos, "area": area})
        pedacos["locais"] = [
            sum(z == zona[i] for z in locais_por_setor.get(i, [])) for i in range(n)
        ]
        pedacos["inscritos_locais"] = [inscritos_do_local.get((i, zona[i]), 0.0) for i in range(n)]
        resumo = pedacos.groupby("rotulo").agg(
            zona=("zona", "first"), inscritos_locais=("inscritos_locais", "sum"),
            inscritos=("inscritos", "sum"), area=("area", "sum"), locais=("locais", "sum"),
        )
        # O corpo da zona é o pedaço onde estão os inscritos dos locais dela
        # (desempate: inscritos estimados, área). Pelos inscritos estimados, um
        # pedaço denso sem local nenhum da zona podia virar o "principal".
        principal = set(
            resumo.sort_values(["inscritos_locais", "inscritos", "area"], ascending=False)
            .drop_duplicates("zona").index
        )
        mudou = False

        for r, p in resumo.loc[~resumo.index.isin(principal) & (resumo["locais"] == 0)].iterrows():
            membros = rotulo == r
            fora = np.r_[b[membros[a] & ~membros[b]], a[membros[b] & ~membros[a]]]
            peso_fora = np.r_[fronteira[membros[a] & ~membros[b]], fronteira[membros[b] & ~membros[a]]]
            outras = zona[fora] != p["zona"]
            if not outras.any():
                continue  # ilha no mar ou só cercada pela própria zona
            vizinha = pd.Series(peso_fora[outras]).groupby(zona[fora][outras]).sum().idxmax()
            zona[membros] = vizinha
            stats["ilhas_absorvidas"] += 1
            mudou = True
        if mudou:
            continue

        for r, p in resumo.loc[~resumo.index.isin(principal) & (resumo["locais"] == 1)].iterrows():
            origem = np.flatnonzero(rotulo == r)
            corpo = [q for q in principal if resumo.loc[q, "zona"] == p["zona"]]
            destino = np.flatnonzero(np.isin(rotulo, corpo))
            # A ponte não atravessa setor já movido por outra ponte nem setor com
            # local de votação de outra zona (o local ficaria fora da própria zona).
            custo = inscritos + 1
            outra_zona_com_local = np.array(
                [bool(zonas_com_local.get(i, set()) - {p["zona"]}) for i in range(n)]
            )
            bloqueado = (fixado | outra_zona_com_local) & (zona != p["zona"])
            custo = np.where(bloqueado, INTRANSPONIVEL, custo)
            grafo_custo = csr_matrix((custo[destinos], (origens, destinos)), shape=(n, n))
            dist, pred, _ = dijkstra(grafo_custo, indices=origem, min_only=True, return_predecessors=True)
            k = destino[np.argmin(dist[destino])]
            if not dist[k] < INTRANSPONIVEL:
                continue
            caminho, v, em_origem = [], pred[k], set(origem)
            while v >= 0 and v not in em_origem:
                caminho.append(v)
                v = pred[v]
            caminho = np.array([s for s in caminho if zona[s] != p["zona"]], dtype=int)
            if len(caminho) == 0:
                continue
            delta = pd.Series(inscritos[caminho]).groupby(zona[caminho]).sum()
            doadoras = list(delta.index)
            delta.loc[p["zona"]] = -delta.sum()
            variacao = (delta.abs() / delta.index.map(lambda z: inscritos_reais.get(z, np.nan))).max()
            if not variacao <= LIMITE_PONTE_INSCRITOS:
                stats["pontes_recusadas"] += 1
                continue
            nova = zona.copy()
            nova[caminho] = p["zona"]
            if not PERMITE_PONTE_CORTAR_ZONA and any(
                pedacos_com_local(nova, z) > pedacos_com_local(zona, z) for z in doadoras
            ):
                stats["pontes_cortariam_zona"] += 1
                continue
            zona[caminho] = p["zona"]
            fixado[caminho] = True
            stats["pontes"] += 1
            mudou = True
        if not mudou:
            break
    else:
        logger.warning("Continuidade das zonas não estabilizou em %d iterações.", MAX_ITERACOES_CONTINUIDADE)
    return zona, stats


def _corrige_continuidade(
    setores: gpd.GeoDataFrame,
    setor_zona: pd.DataFrame,
    locais: gpd.GeoDataFrame,
    pesos: pd.DataFrame,
    col_setor: str,
    col_id_local: str,
    col_mun: str,
    col_zona: str,
) -> pd.DataFrame:
    """Roda `_torna_zonas_continuas` em cada município com várias zonas.

    Inscritos estimados por setor = Σ inscritos do local × peso(local, setor);
    soma real por zona = inscritos dos locais da zona (todos os não
    especiais, inclusive os posicionados no centróide do município).
    """
    inscritos_setor = (
        pesos[[col_id_local, col_setor, "peso"]]
        .merge(locais[[col_id_local, "qt_eleitores"]], on=col_id_local, how="inner")
        .assign(inscritos=lambda d: d["peso"] * d["qt_eleitores"].fillna(0))
        .groupby(col_setor)["inscritos"]
        .sum()
    )
    reais = locais.groupby([col_mun, col_zona])["qt_eleitores"].sum()
    com_coord = locais
    if "flag_sem_coord" in locais.columns:
        com_coord = locais.loc[~locais["flag_sem_coord"].astype(bool)]
    com_coord = com_coord.to_crs(CRS_METRICO_BR)

    base = (
        setores[[col_setor, "geometry"]]
        .merge(setor_zona[[col_setor, col_mun, col_zona]], on=col_setor, how="inner")
        .to_crs(CRS_METRICO_BR)
    )
    base["inscritos"] = base[col_setor].map(inscritos_setor).fillna(0.0)

    partes, total = [], {}
    for cd_mun, s in base.groupby(col_mun):
        zona, stats = _torna_zonas_continuas(
            s, com_coord.loc[com_coord[col_mun] == cd_mun], reais.loc[cd_mun], col_zona
        )
        partes.append(s[[col_setor, col_mun]].assign(**{col_zona: zona}))
        total = {k: total.get(k, 0) + v for k, v in stats.items()}
    logger.info(
        "Continuidade das zonas: %d ilhas absorvidas, %d pontes aplicadas, %d recusadas "
        "(> %.0f%% dos inscritos), %d recusadas por cortar outra zona.",
        total.get("ilhas_absorvidas", 0), total.get("pontes", 0), total.get("pontes_recusadas", 0),
        100 * LIMITE_PONTE_INSCRITOS, total.get("pontes_cortariam_zona", 0),
    )
    return pd.concat(partes, ignore_index=True)


def constroi_zona_mun(
    setores: gpd.GeoDataFrame,
    locais: gpd.GeoDataFrame,
    pesos: pd.DataFrame,
    municipios: gpd.GeoDataFrame,
    *,
    col_setor: str = "cd_setor",
    col_id_local: str = "id_local",
    col_mun: str = "cd_mun",
    col_zona: str = "nr_zona",
    local_setor_rede: pd.DataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Constrói a camada `id_zona_mun` (ver PLAN.md > Pesquisa > Zonas eleitorais).

    - Município com **uma só zona**: a geometria é o próprio polígono do IBGE,
      exata (`geom_estimada = False`).
    - Município com **várias zonas**: cada setor é atribuído à zona que recebe
      a maior parte dos seus adultos (via `pesos` + `locais`); setores sem
      adulto, à zona do local mais próximo; depois as zonas são tornadas
      contínuas (`_torna_zonas_continuas`), e os setores da mesma zona são
      fundidos (`geom_estimada = True`).

    Args:
        setores: polígonos dos setores censitários 2022, com `col_setor`.
        locais: locais não especiais (inclusive os posicionados no centróide
            do município), com `col_id_local`, `col_mun`, `col_zona`,
            `qt_eleitores`, `flag_sem_coord` e a geometria do ponto.
        pesos: matriz de pesos local -> setor (de `calcula_pesos_local_setor`).
        municipios: polígonos dos municípios (geobr), com `col_mun`.
        col_setor, col_id_local, col_mun, col_zona: nomes das colunas.
        local_setor_rede: `[col_setor, col_id_local]` com o local mais próximo
            por tempo na rede (`local_mais_proximo_por_tempo`). Quando dado,
            decide a zona dos setores que cobre, no lugar da regra dos adultos.

    Returns:
        GeoDataFrame com `id_zona_mun`, `col_mun`, `col_zona`, `n_zonas_mun`,
        `geom_estimada`.
    """
    # A malha nacional de 2022 tem 914 códigos de setor repetidos (setores
    # guardados em mais de um polígono: ilhas, margens de rio). Sem juntar os
    # pedaços, cada um virava um setor à parte na continuidade e, no merge final
    # por código, cada pedaço herdava a zona dos outros — sobravam ilhas.
    repetido = setores[col_setor].duplicated(keep=False)
    if repetido.any():
        setores = pd.concat(
            [setores.loc[~repetido], setores.loc[repetido].dissolve(by=col_setor, as_index=False)],
            ignore_index=True,
        )

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

    multi = n_zonas_mun[n_zonas_mun > 1].index

    # Com o tempo na rede, o local mais próximo por tempo decide a zona do
    # setor (inclusive dos que têm adultos): é o que respeita rios e morros, que
    # a distância em linha reta e a alocação do CNEFE ignoram. Onde a rede não
    # chega (NA), vale a regra dos adultos abaixo.
    if local_setor_rede is not None:
        zona_rede = (
            local_setor_rede[[col_setor, col_id_local]]
            .merge(locais_zona[[col_id_local, col_mun, col_zona]], on=col_id_local, how="inner")
            .loc[lambda d: d[col_mun].isin(multi), [col_setor, col_mun, col_zona]]
            .drop_duplicates(col_setor)
            .assign(adultos=0.0)
        )
        setor_zona = pd.concat(
            [zona_rede, setor_zona.loc[~setor_zona[col_setor].isin(zona_rede[col_setor])]],
            ignore_index=True,
        )
        logger.info("Zona pela rede em %d setores.", len(zona_rede))

    # Setores sem adultos alocados (parques, represas, áreas industriais ou
    # rurais sem domicílio no CNEFE) vão à zona do local mais próximo, no
    # mesmo município (regra do PLAN.md). Sem isso, a mediana de 24% da área
    # desses municípios (31,5% em São Paulo) ficava sem zona nenhuma — medido
    # em 29/09/2026 na malha de 2022.
    sem_zona = setores.loc[
        setores[col_mun].isin(multi) & ~setores[col_setor].isin(setor_zona[col_setor]),
        [col_setor, col_mun, "geometry"],
    ]
    if len(sem_zona):
        setor_zona = pd.concat(
            [setor_zona, _zona_do_local_mais_proximo(sem_zona, locais, col_setor, col_mun, col_zona)],
            ignore_index=True,
        )

    setor_zona = _corrige_continuidade(
        setores, setor_zona, locais, pesos, col_setor, col_id_local, col_mun, col_zona
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
    saida = saida.to_crs(CRS_SIRGAS2000)
    # Zona de cada setor (municípios com várias zonas), para comparar abordagens.
    saida.attrs["setor_zona"] = setor_zona[[col_setor, col_mun, col_zona]].copy()
    return saida


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
    `calcula_pesos_local_setor`) e o setor fantasma acaba aparecendo na
    matriz de pesos mesmo assim, sem geometria correspondente. Rodar isto
    por município antes de publicar, para saber onde o problema é grande.

    Returns:
        DataFrame com `cd_mun`, `n_dom`, `n_dom_setor_valido`,
        `pc_dom_setor_invalido`.
    """
    setores_validos = set(pop18_setor[col_setor])
    df = domicilios[["cd_mun", col_setor]].copy()
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
