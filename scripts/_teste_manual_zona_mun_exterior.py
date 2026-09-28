"""Teste manual: agrega_por_zona_mun não pode perder o exterior (cd_mun nulo).

Bug real (29/09/2026, ver CLAUDE.md): `.astype(str)` numa coluna de texto
nula do pandas devolve NaN de verdade (não a string "nan"), e a concatenação
propaga isso — sem o `fillna(SENTINELA_EXTERIOR)`, `pivot_table()` descarta
esses grupos (dropna=True por padrão) e o exterior some do total, não só do
mapa. Rodando o Brasil inteiro de 2022 isso derrubou ~294 mil votos em
silêncio.

Roda com: venv/Scripts/python scripts/_teste_manual_zona_mun_exterior.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from eleicoes import resultados

CD_MUN = "999999"

locais = pd.DataFrame(
    {
        "id_local": ["L1", "L_EXT"],
        "cd_mun": [CD_MUN, None],  # None = exterior, sem crosswalk
        "nr_zona": [1, 1],
        "qt_eleitores": [300, 50],
    }
)
com_id = pd.DataFrame(
    {
        "ano": [2022] * 4, "turno": [1] * 4, "sg_uf": ["SP", "SP", "ZZ", "ZZ"],
        "cd_mun_tse": [CD_MUN, CD_MUN, "ZZ001", "ZZ001"],
        "nr_zona": [1, 1, 1, 1], "nr_secao": [1, 1, 1, 1],
        "cd_cargo": [1] * 4, "nr_votavel": [13, 95, 13, 95], "nm_votavel": ["x"] * 4,
        "qt_votos": [100, 5, 20, 2],
        "id_secao": [f"{CD_MUN}00010001"] * 2 + ["ZZ00010001"] * 2,
        "id_local": ["L1", "L1", "L_EXT", "L_EXT"],
        "qt_comparecimento": [105, 105, 22, 22],
        "qt_aptos": [pd.NA, pd.NA, pd.NA, pd.NA],  # sem detalhe_votacao_secao aqui, cai no fallback
    }
)

res_zm = resultados.agrega_por_zona_mun(com_id, locais)
print(res_zm)

total = res_zm[["qt_votos_13", "qt_votos_95"]].sum().sum()
esperado = 100 + 5 + 20 + 2
assert total == esperado, (total, esperado)
assert "EXTERIOR0001" in set(res_zm["id_zona_mun"])

print(f"\nOK — {total} votos conservados, exterior presente como zona própria.")
