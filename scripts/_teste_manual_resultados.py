"""Teste manual (dados sintéticos) de src/eleicoes/resultados.py.

Confere que a soma de votos é a mesma nas três agregações (local, zona x
município e setor), a conservação central do PLAN.md > Verificação.

Roda com: venv/Scripts/python scripts/_teste_manual_resultados.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from eleicoes import resultados

CD_MUN = "999999"

# 2 locais (mesma zona 1), 6 seções no total
secao_local = pd.DataFrame(
    {
        "id_secao": [f"{CD_MUN}00010001", f"{CD_MUN}00010002", f"{CD_MUN}00010003",
                     f"{CD_MUN}00020004", f"{CD_MUN}00020005", f"{CD_MUN}00020006"],
        "nr_turno": [1, 1, 1, 1, 1, 1],
        "id_local": ["L1", "L1", "L1", "L2", "L2", "L2"],
    }
)
locais = pd.DataFrame(
    {"id_local": ["L1", "L2"], "cd_mun": [CD_MUN, CD_MUN], "nr_zona": [1, 2]}
)

rng = np.random.default_rng(1)
linhas = []
for _, row in secao_local.iterrows():
    cd_mun_tse, nr_zona, nr_secao = row["id_secao"][:6], int(row["id_secao"][6:10]), int(row["id_secao"][10:14])
    aptos = int(rng.integers(200, 400))
    comparecimento = int(aptos * rng.uniform(0.7, 0.9))
    votos_13 = int(comparecimento * rng.uniform(0.3, 0.6))
    votos_22 = int(comparecimento * rng.uniform(0.2, 0.4))
    brancos = int(comparecimento * 0.02)
    nulos = comparecimento - votos_13 - votos_22 - brancos
    for nr, qt in [(13, votos_13), (22, votos_22), (95, brancos), (96, nulos)]:
        linhas.append(
            {
                "ano": 2022, "turno": 1, "sg_uf": "SP", "cd_mun_tse": cd_mun_tse,
                "nr_zona": nr_zona, "nr_secao": nr_secao, "cd_cargo": 1,
                "nr_votavel": nr, "nm_votavel": str(nr), "qt_votos": qt,
                "qt_aptos": aptos, "qt_comparecimento": comparecimento,
            }
        )
resultados_secao = pd.DataFrame(linhas)

# ---- 1. seção -> local -------------------------------------------------------

com_id = resultados.secao_para_id_local(resultados_secao, secao_local)
assert com_id["id_local"].notna().all()

res_local = resultados.agrega_por_local(com_id)
print("res_local:\n", res_local)

# ---- 2. seção -> zona x município -------------------------------------------

res_zona_mun = resultados.agrega_por_zona_mun(com_id, locais)
print("\nres_zona_mun:\n", res_zona_mun)

total_votos_local = res_local[["qt_votos_13", "qt_votos_22", "qt_votos_95", "qt_votos_96"]].sum().sum()
total_votos_zona = res_zona_mun[["qt_votos_13", "qt_votos_22", "qt_votos_95", "qt_votos_96"]].sum().sum()
assert total_votos_local == total_votos_zona, (total_votos_local, total_votos_zona)
print(f"\nConservação local == zona_mun: {total_votos_local} votos")

# ---- 3. local -> setor (via pesos) ------------------------------------------

pesos = pd.DataFrame(
    {
        "id_local": ["L1", "L1", "L2", "L2"],
        "cd_setor": ["S1", "S2", "S3", "S4"],
        "peso": [0.5, 0.5, 0.7, 0.3],
    }
)
res_setor = resultados.agrega_por_setor(res_local, pesos)
print("\nres_setor:\n", res_setor)

total_votos_setor = res_setor[["qt_votos_13", "qt_votos_22", "qt_votos_95", "qt_votos_96"]].sum().sum()
assert np.isclose(total_votos_setor, total_votos_local), (total_votos_setor, total_votos_local)
print(f"\nConservação local == setor: {total_votos_setor} votos")

# ---- 4. indicadores -----------------------------------------------------------

res_local_ind = resultados.calcula_indicadores(res_local, cols_votos_validos=["qt_votos_13", "qt_votos_22"])
print("\nindicadores por local:\n", res_local_ind[["id_local", "pc_comparecimento", "pc_validos_13", "pc_validos_22", "nr_vencedor", "margem"]])
assert res_local_ind["pc_validos_13"].add(res_local_ind["pc_validos_22"]).round(6).eq(1.0).all()
assert set(res_local_ind["nr_vencedor"]) <= {13, 22}

print("\nOK — todos os testes manuais de resultados.py passaram.")
