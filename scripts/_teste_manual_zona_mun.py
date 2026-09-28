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
from shapely.geometry import box

from eleicoes import alocacao
from eleicoes.config import CRS_SIRGAS2000

# Município A: uma zona só. Município B: duas zonas (2 setores cada).
setores = gpd.GeoDataFrame(
    {"cd_setor": ["A01", "A02", "B01", "B02", "B03", "B04"], "cd_mun": ["A", "A", "B", "B", "B", "B"]},
    geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(10, 0, 11, 1), box(11, 0, 12, 1), box(10, 1, 11, 2), box(11, 1, 12, 2)],
    crs=CRS_SIRGAS2000,
)
municipios = gpd.GeoDataFrame(
    {"cd_mun": ["A", "B"]}, geometry=[box(0, 0, 2, 1), box(10, 0, 12, 2)], crs=CRS_SIRGAS2000
)
locais = pd.DataFrame({"id_local": ["LA1", "LB1", "LB2"], "cd_mun": ["A", "B", "B"], "nr_zona": [1, 1, 2]})
pesos = pd.DataFrame(
    {
        "id_local": ["LA1", "LA1", "LB1", "LB1", "LB2", "LB2"],
        "cd_setor": ["A01", "A02", "B01", "B02", "B03", "B04"],
        "adultos": [100, 100, 100, 100, 100, 100],
    }
)

zm = alocacao.constroi_zona_mun(setores, locais, pesos, municipios)
print(zm[["id_zona_mun", "cd_mun", "nr_zona", "n_zonas_mun", "geom_estimada"]])

assert set(zm["id_zona_mun"]) == {"A0001", "B0001", "B0002"}
assert zm.loc[zm.cd_mun == "A", "geom_estimada"].iloc[0] == False  # noqa: E712
assert zm.loc[zm.cd_mun == "B", "geom_estimada"].all()

print("\nOK — constroi_zona_mun com setores contendo cd_mun (esquema real).")
