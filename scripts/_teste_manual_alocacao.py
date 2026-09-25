"""Teste manual (dados sintéticos) da lógica de src/eleicoes/alocacao.py.

Não é um pytest formal (o repo não tem suíte de testes, ver CLAUDE.md) — é um
script para rodar uma vez e conferir, com um município fictício, que:
  1. cada domicílio vai para o local mais próximo;
  2. a matriz de pesos conserva o total (soma de peso = 1 por local);
  3. pop18_por_setor faz a conta certa;
  4. o diagnóstico de razão de eleitores calcula um valor plausível.

Roda com: venv/Scripts/python scripts/_teste_manual_alocacao.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from eleicoes import alocacao
from eleicoes.config import CRS_SIRGAS2000

# ---- Município fictício: grade 10x10 km, 2 locais, 4 setores em quadrantes --

CD_MUN = "999999"

# 2 locais: um a oeste, um a leste
locais = gpd.GeoDataFrame(
    {
        "id_local": ["L1", "L2"],
        "cd_mun": [CD_MUN, CD_MUN],
        "qt_eleitores": [3000, 1000],
    },
    geometry=[Point(-46.70, -23.55), Point(-46.60, -23.55)],
    crs=CRS_SIRGAS2000,
)

# 400 domicílios numa grade 20x20, metade oeste metade leste, em 4 setores
# (quadrantes NW/NE/SW/SE)
rng = np.random.default_rng(0)
xs = rng.uniform(-46.75, -46.55, 400)
ys = rng.uniform(-23.60, -23.50, 400)


def setor_do_ponto(x, y):
    ns = "N" if y > -23.55 else "S"
    lo = "W" if x < -46.65 else "E"
    return f"{CD_MUN}0{ns}{lo}"


setores_dom = [setor_do_ponto(x, y) for x, y in zip(xs, ys)]

domicilios = gpd.GeoDataFrame(
    {"cd_mun": [CD_MUN] * 400, "cd_setor": setores_dom},
    geometry=[Point(x, y) for x, y in zip(xs, ys)],
    crs=CRS_SIRGAS2000,
)

# ---- 1. alocação domicílio -> local mais próximo --------------------------

id_local_dom = alocacao.aloca_domicilios_a_locais(domicilios, locais)
domicilios["id_local"] = id_local_dom

assert id_local_dom.notna().all(), "todo domicílio deveria ter um local (mesmo município)"
contagem = id_local_dom.value_counts()
print("Domicílios por local (esperado: ~mais a oeste em L1, leste em L2):")
print(contagem)
# L1 está a oeste (-46.70); domicílios com x < -46.65 devem ir majoritariamente a L1
oeste = domicilios.loc[domicilios.geometry.x < -46.68, "id_local"]
assert (oeste == "L1").mean() > 0.95, "domicílios claramente a oeste deveriam ir a L1"

# ---- 2. pop18_por_setor -----------------------------------------------------

setores_unicos = sorted(set(setores_dom))
cn22_pop01 = pd.DataFrame(
    {"cd_setor": setores_unicos, "cn22_pop01_res_tot_0": [1000] * len(setores_unicos)}
)
cn22_pop03 = pd.DataFrame(
    {
        "cd_setor": setores_unicos,
        "cn22_pop03_0a4_tot_0": [50] * len(setores_unicos),
        "cn22_pop03_5a9_tot_0": [50] * len(setores_unicos),
        "cn22_pop03_10a14_tot_0": [50] * len(setores_unicos),
        "cn22_pop03_15a19_tot_0": [50] * len(setores_unicos),
    }
)
pop18 = alocacao.pop18_por_setor(cn22_pop01, cn22_pop03)
esperado = 1000 - 50 - 50 - 50 - 0.6 * 50
assert np.allclose(pop18["pop18"], esperado), f"esperado {esperado}, obtido {pop18}"
print(f"\npop18_por_setor ok: {esperado} por setor")

# ---- 3. matriz de pesos local -> setor -------------------------------------

pesos = alocacao.constroi_pesos_local_setor(domicilios, pop18)
print("\nMatriz de pesos:")
print(pesos)

soma_por_local = pesos.groupby("id_local")["peso"].sum()
assert np.allclose(soma_por_local, 1.0), f"pesos não somam 1: {soma_por_local}"
print(f"\nConservação ok: soma de peso por local = {soma_por_local.to_dict()}")

# ---- 4. diagnóstico de razão de eleitores ----------------------------------

diag = alocacao.diagnostico_razao_eleitores(locais, pesos)
print("\nDiagnóstico razao_eleitores:")
print(diag)
assert diag["razao_eleitores"].notna().all()

print("\nOK — todos os testes manuais passaram.")
