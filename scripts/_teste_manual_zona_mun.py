"""Teste manual (dados sintéticos) de alocacao.constroi_zona_mun.

O `setores` sintético inclui `cd_mun` (como a malha real do IBGE tem, via
`CD_MUN` renomeado em `03_pesos_alocacao.carrega_setores`) — um teste anterior
não incluía essa coluna e por isso não pegou um bug real: `setores` e
`setor_zona` colidindo em `cd_mun` no merge (pandas sufixa `cd_mun_x`/`cd_mun_y`
e o `col_mun` puro some, quebrando o `dissolve()`).

Roda com: venv/Scripts/python scripts/_teste_manual_zona_mun.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, box

from eleicoes import alocacao
from eleicoes.config import CRS_METRICO_BR, CRS_SIRGAS2000

# Município A: uma zona só. Município B: duas zonas (2 setores cada).
setores = gpd.GeoDataFrame(
    {"cd_setor": ["A01", "A02", "B01", "B02", "B03", "B04"], "cd_mun": ["A", "A", "B", "B", "B", "B"]},
    geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(10, 0, 11, 1), box(11, 0, 12, 1), box(10, 1, 11, 2), box(11, 1, 12, 2)],
    crs=CRS_SIRGAS2000,
)
municipios = gpd.GeoDataFrame(
    {"cd_mun": ["A", "B"]}, geometry=[box(0, 0, 2, 1), box(10, 0, 12, 2)], crs=CRS_SIRGAS2000
)
locais = gpd.GeoDataFrame(
    {
        "id_local": ["LA1", "LB1", "LB2"], "cd_mun": ["A", "B", "B"], "nr_zona": [1, 1, 2],
        "qt_eleitores": [200, 200, 200], "flag_sem_coord": [False, False, False],
    },
    geometry=[Point(0.5, 0.5), Point(10.5, 0.5), Point(11.5, 1.5)],
    crs=CRS_SIRGAS2000,
)
pesos = pd.DataFrame(
    {
        "id_local": ["LA1", "LA1", "LB1", "LB1", "LB2", "LB2"],
        "cd_setor": ["A01", "A02", "B01", "B02", "B03", "B04"],
        "adultos": [100, 100, 100, 100, 100, 100],
        "peso": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
    }
)

zm = alocacao.constroi_zona_mun(setores, locais, pesos, municipios)
print(zm[["id_zona_mun", "cd_mun", "nr_zona", "n_zonas_mun", "geom_estimada"]])

assert set(zm["id_zona_mun"]) == {"A0001", "B0001", "B0002"}
assert zm.loc[zm.cd_mun == "A", "geom_estimada"].iloc[0] == False  # noqa: E712
assert zm.loc[zm.cd_mun == "B", "geom_estimada"].all()

# Setor sem adulto alocado (B05, ex.: um parque) em município de várias zonas:
# vai à zona do local mais próximo (LB2, zona 2), sem deixar buraco na malha.
setores_b5 = pd.concat(
    [setores, gpd.GeoDataFrame({"cd_setor": ["B05"], "cd_mun": ["B"]}, geometry=[box(12, 1, 13, 2)], crs=CRS_SIRGAS2000)],
    ignore_index=True,
)
municipios_b5 = gpd.GeoDataFrame(
    {"cd_mun": ["A", "B"]},
    geometry=[box(0, 0, 2, 1), box(10, 0, 12, 2).union(box(12, 1, 13, 2))],
    crs=CRS_SIRGAS2000,
)
zm_b5 = alocacao.constroi_zona_mun(setores_b5, locais, pesos, municipios_b5)
zona2 = zm_b5.loc[zm_b5.id_zona_mun == "B0002"].geometry.iloc[0]
assert zona2.contains(Point(12.5, 1.5)), "B05 deveria ir para a zona 2 (local LB2, o mais próximo)"
uniao_b = zm_b5.loc[zm_b5.cd_mun == "B"].geometry.union_all()
assert abs(uniao_b.area - municipios_b5.geometry.iloc[1].area) < 1e-9, "a malha de B deveria cobrir o município todo"
print("Setor sem adulto alocado vai à zona do local mais próximo, sem buraco: OK")

# ---- Continuidade das zonas (_torna_zonas_continuas) -------------------------
# Malha 4 × 3 de setores de 1 km (coluna, linha), zona inicial de cada um:
#
#   linha 2:  2* 1  1  2        2* = parte da zona 2 com 1 local da zona 2
#   linha 1:  1  2  1  2        2 no meio = ilha da zona 2 sem local
#   linha 0:  1  1  1  2        coluna 3 = corpo da zona 2 (2 locais)
#
# mais um setor solto, longe de tudo (ilha no mar), da zona 2 e sem local.
X0, Y0 = 5_000_000, 7_000_000
zonas_grade = {(0, 2): 2, (1, 2): 1, (2, 2): 1, (3, 2): 2,
               (0, 1): 1, (1, 1): 2, (2, 1): 1, (3, 1): 2,
               (0, 0): 1, (1, 0): 1, (2, 0): 1, (3, 0): 2}
celulas = list(zonas_grade)
grade = gpd.GeoDataFrame(
    {"nr_zona": [zonas_grade[c] for c in celulas] + [2], "inscritos": [10.0] * (len(celulas) + 1)},
    geometry=[box(X0 + 1000 * c, Y0 + 1000 * r, X0 + 1000 * (c + 1), Y0 + 1000 * (r + 1)) for c, r in celulas]
    + [box(X0 + 9000, Y0 + 9000, X0 + 10000, Y0 + 10000)],
    crs=CRS_METRICO_BR,
)
centro = lambda c, r: Point(X0 + 1000 * c + 500, Y0 + 1000 * r + 500)  # noqa: E731
locais_grade = gpd.GeoDataFrame(
    {"nr_zona": [2, 2, 2, 1, 1], "qt_eleitores": [300, 300, 100, 300, 300]},
    geometry=[centro(3, 0), centro(3, 1), centro(0, 2), centro(0, 0), centro(2, 1)],
    crs=CRS_METRICO_BR,
)
posicao = {c: i for i, c in enumerate(celulas)}
isolado = len(celulas)

zona, stats = alocacao._torna_zonas_continuas(
    grade, locais_grade, pd.Series({1: 10_000, 2: 10_000}), "nr_zona"
)
print("\ncontinuidade:", stats)
assert zona[posicao[(1, 1)]] == 1, "ilha sem local da zona 2 deveria virar zona 1"
assert zona[posicao[(0, 2)]] == 2, "a parte com 1 local continua na zona 2"
assert zona[posicao[(1, 2)]] == 2 and zona[posicao[(2, 2)]] == 2, "a ponte (1,2)-(2,2) deveria passar à zona 2"
assert zona[isolado] == 2, "setor sem nenhuma zona vizinha (ilha no mar) não muda"
print("Ilha sem local absorvida, parte com 1 local ligada por ponte, ilha no mar mantida: OK")

# A mesma ponte é recusada quando muda a soma de inscritos da zona em mais de 5%.
zona_r, stats_r = alocacao._torna_zonas_continuas(
    grade, locais_grade, pd.Series({1: 100, 2: 100}), "nr_zona"
)
assert zona_r[posicao[(1, 2)]] == 1 and zona_r[posicao[(0, 2)]] == 2, "ponte de 20% dos inscritos deveria ser recusada"
assert stats_r["pontes_recusadas"] >= 1
print("Ponte recusada acima do limite de inscritos: OK")

# Ponte que partiria outra zona é recusada. Malha 4 × 3:
#
#   linha 2:  1  1  1  1        a zona 1 só é contínua pelo setor (2,1):
#   linha 1:  2  2  1  2*       qualquer ponte de 2* até o corpo da zona 2
#   linha 0:  1  1  1  1        deixaria um pedaço da zona 1 com local isolado
zonas_corte = {(c, 2): 1 for c in range(4)} | {(c, 0): 1 for c in range(4)} | {
    (0, 1): 2, (1, 1): 2, (2, 1): 1, (3, 1): 2}
cel_corte = list(zonas_corte)
grade_corte = gpd.GeoDataFrame(
    {"nr_zona": [zonas_corte[c] for c in cel_corte], "inscritos": [10.0] * len(cel_corte)},
    geometry=[box(X0 + 1000 * c, Y0 + 1000 * r, X0 + 1000 * (c + 1), Y0 + 1000 * (r + 1)) for c, r in cel_corte],
    crs=CRS_METRICO_BR,
)
locais_corte = gpd.GeoDataFrame(
    {"nr_zona": [1, 1, 2, 2], "qt_eleitores": [300, 300, 300, 100]}, geometry=[centro(0, 0), centro(0, 2), centro(0, 1), centro(3, 1)], crs=CRS_METRICO_BR
)
zona_c, stats_c = alocacao._torna_zonas_continuas(
    grade_corte, locais_corte, pd.Series({1: 10_000, 2: 10_000}), "nr_zona"
)
pos_c = {c: i for i, c in enumerate(cel_corte)}
assert zona_c[pos_c[(2, 1)]] == 1 and stats_c["pontes_cortariam_zona"] >= 1, stats_c
print("Ponte que cortaria outra zona recusada:", stats_c)

print("\nOK — constroi_zona_mun com setores contendo cd_mun (esquema real).")
