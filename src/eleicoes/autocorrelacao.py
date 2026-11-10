"""Autocorrelação espacial (Moran global e LISA) por município.

Replica o método de Soares e Terron (2008), "Dois Lulas: a geografia eleitoral
da reeleição" (Opinião Pública 14(2)): matriz de contiguidade entre municípios,
I de Moran com 9.999 permutações, Moran Local em cinco categorias e a
"dinâmica das bases geoeleitorais" entre duas eleições. Usado por
`10_autocorrelacao_espacial.qmd`.
"""

from __future__ import annotations

import logging

import esda
import geopandas as gpd
import numpy as np
import pandas as pd
from libpysal import weights

from .config import CRS_METRICO_BR

logger = logging.getLogger(__name__)

# ==== Parâmetros do método ===================================================

PERMUTACOES = 9999  # como no artigo (nota 6)
ALFA = 0.05  # significância do Moran Local (padrão do GeoDa)
SEMENTE = 2008

ALTO_ALTO = "Alto-Alto"
BAIXO_BAIXO = "Baixo-Baixo"
BAIXO_ALTO = "Baixo-Alto"
ALTO_BAIXO = "Alto-Baixo"
NAO_SIGNIFICATIVO = "Não significativo"
# Ordem das tabelas do artigo; o índice é a classe de cor no visualizador.
CLASSES_LISA = [ALTO_ALTO, BAIXO_BAIXO, BAIXO_ALTO, ALTO_BAIXO, NAO_SIGNIFICATIVO]
# Quadrante do esda (`Moran_Local.q`): 1 HH, 2 LH, 3 LL, 4 HL.
_CLASSE_POR_Q = {1: ALTO_ALTO, 2: BAIXO_ALTO, 3: BAIXO_BAIXO, 4: ALTO_BAIXO}
COESAS = (ALTO_ALTO, BAIXO_BAIXO)

DINAMICAS = [
    "Coesão regional e manutenção",
    "Coesão regional e alteração",
    "Dissolução da coesão regional",
    "Construção da coesão regional",
    "Manutenção da vulnerabilidade regional",
]


# ==== Matriz de vizinhança ===================================================


def pesos_contiguidade(gdf: gpd.GeoDataFrame) -> weights.W:
    """Contiguidade Queen, padronizada por linha, na ordem das linhas de `gdf`.

    Municípios sem vizinho (ilhas: Fernando de Noronha, Ilhabela) ficam ligados
    ao município de centróide mais próximo, para entrarem no cálculo.
    """
    w = weights.Queen.from_dataframe(gdf, use_index=False, silence_warnings=True)
    if w.islands:
        centroides = gdf.geometry.to_crs(CRS_METRICO_BR).centroid
        knn = weights.KNN.from_array(np.column_stack([centroides.x, centroides.y]), k=1)
        logger.info("%d municípios sem vizinho ligados ao mais próximo: %s", len(w.islands), w.islands)
        w = weights.attach_islands(w, knn)
    w.transform = "r"
    return w


# ==== Moran global e local ===================================================


def moran_global(y: np.ndarray, w: weights.W) -> dict:
    """I de Moran com significância por permutação (média e dp do I simulado)."""
    np.random.seed(SEMENTE)  # o esda.Moran sorteia com o gerador global
    m = esda.Moran(np.asarray(y, dtype=float), w, permutations=PERMUTACOES)
    return {
        "I": m.I,
        "media_perm": m.EI_sim,
        "dp_perm": m.seI_sim,
        "z_sim": m.z_sim,
        "p_sim": m.p_sim,
        "n": m.n,
    }


def lisa(y: np.ndarray, w: weights.W) -> pd.DataFrame:
    """Moran Local: `lag` (média dos vizinhos), `Ii`, `q`, `p_sim` e `classe_lisa`."""
    y = np.asarray(y, dtype=float)
    ml = esda.Moran_Local(y, w, permutations=PERMUTACOES, seed=SEMENTE)
    classe = pd.Series(ml.q).map(_CLASSE_POR_Q)
    classe = classe.where(ml.p_sim < ALFA, NAO_SIGNIFICATIVO)
    return pd.DataFrame(
        {
            "lag": weights.lag_spatial(w, y),
            "Ii": ml.Is,
            "q": ml.q,
            "p_sim": ml.p_sim,
            "classe_lisa": pd.Categorical(classe, categories=CLASSES_LISA),
        }
    )


# ==== Tabelas do artigo ======================================================


def resumo_por_classe(classe: pd.Series, validos: pd.Series, votos_pt: pd.Series) -> pd.DataFrame:
    """Tabelas 2a/2b: municípios, % municípios, % dos válidos e % dos votos do PT por classe."""
    # float: os votos vêm em int32, e `100 * soma` estoura (28 milhões × 100 > 2^31).
    df = pd.DataFrame(
        {"classe": classe, "validos": np.asarray(validos, dtype=float), "votos_pt": np.asarray(votos_pt, dtype=float)}
    )
    tab = df.groupby("classe", observed=False).agg(
        municipios=("classe", "size"), validos=("validos", "sum"), votos_pt=("votos_pt", "sum")
    )
    tab = tab.reindex(CLASSES_LISA)
    return pd.DataFrame(
        {
            "municipios": tab["municipios"],
            "pc_municipios": 100 * tab["municipios"] / tab["municipios"].sum(),
            "pc_validos": 100 * tab["validos"] / tab["validos"].sum(),
            "pc_votos_pt": 100 * tab["votos_pt"] / tab["votos_pt"].sum(),
        }
    )


def dinamica(classe_antes: pd.Series, classe_depois: pd.Series) -> pd.Series:
    """Tabela 3: dinâmica das bases geoeleitorais entre duas eleições.

    "Coesa" = Alto-Alto ou Baixo-Baixo. Não significativo, Alto-Baixo e
    Baixo-Alto contam como zona de vulnerabilidade (sem coesão regional).
    """
    antes = np.asarray(classe_antes, dtype=object)
    depois = np.asarray(classe_depois, dtype=object)
    coesa_antes = np.isin(antes, COESAS)
    coesa_depois = np.isin(depois, COESAS)
    rotulo = np.select(
        [
            coesa_antes & coesa_depois & (antes == depois),
            coesa_antes & coesa_depois,
            coesa_antes,
            coesa_depois,
        ],
        DINAMICAS[:4],
        default=DINAMICAS[4],
    )
    return pd.Series(pd.Categorical(rotulo, categories=DINAMICAS), index=getattr(classe_antes, "index", None))


def resumo_dinamica(rotulo: pd.Series, populacao: pd.Series) -> pd.DataFrame:
    """Tabela 3: municípios e população (eleitorado apto) por dinâmica, com %."""
    df = pd.DataFrame({"dinamica": rotulo, "populacao": np.asarray(populacao, dtype=float)})
    tab = df.groupby("dinamica", observed=False).agg(
        municipios=("dinamica", "size"), populacao=("populacao", "sum")
    ).reindex(DINAMICAS)
    tab["pc_municipios"] = 100 * tab["municipios"] / tab["municipios"].sum()
    tab["pc_populacao"] = 100 * tab["populacao"] / tab["populacao"].sum()
    return tab[["municipios", "pc_municipios", "populacao", "pc_populacao"]]
