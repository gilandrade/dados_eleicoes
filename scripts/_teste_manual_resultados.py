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

from eleicoes import cores, resultados

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
    {
        "id_local": ["L1", "L2"],
        "cd_mun": [CD_MUN, CD_MUN],
        "nr_zona": [1, 2],
        "qt_eleitores": [3 * 300, 3 * 300],  # ~3 seções de ~300 eleitores cada
    }
)

# qt_aptos não entra mais no esquema de fontes.py — vem de locais.qt_eleitores
# (ver resultados.py). qt_comparecimento é calculado como soma de qt_votos por
# seção, então nem entra aqui: fica implícito nos próprios votos.
rng = np.random.default_rng(1)
linhas = []
for _, row in secao_local.iterrows():
    cd_mun_tse, nr_zona, nr_secao = row["id_secao"][:6], int(row["id_secao"][6:10]), int(row["id_secao"][10:14])
    comparecimento = int(rng.integers(150, 350))
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
            }
        )
resultados_secao = pd.DataFrame(linhas)
resultados_secao["qt_comparecimento"] = resultados_secao.groupby(
    ["cd_mun_tse", "nr_zona", "nr_secao"]
)["qt_votos"].transform("sum")
# qt_aptos por seção fica NA (sem detalhe_votacao_secao neste teste) — cai no
# fallback de locais.qt_eleitores, como fontes.ler_votacao_secao() sem
# caminho_detalhe também devolve.
resultados_secao["qt_aptos"] = pd.NA

# ---- 1. seção -> local -------------------------------------------------------

com_id = resultados.secao_para_id_local(resultados_secao, secao_local)
assert com_id["id_local"].notna().all()

res_local = resultados.agrega_por_local(com_id, locais)
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
assert res_local_ind["pc_brancos_nulos"].round(6).eq(
    res_local_ind["pc_brancos"].add(res_local_ind["pc_nulos"]).round(6)
).all()

# ---- 5. município (soma de zona_mun por cd_mun, com exterior) ---------------

res_zona_mun_ind = resultados.calcula_indicadores(
    res_zona_mun, cols_votos_validos=["qt_votos_13", "qt_votos_22"]
)
res_municipio = resultados.agrega_zona_mun_por_municipio(res_zona_mun_ind)
total_votos_municipio = res_municipio[
    ["qt_votos_13", "qt_votos_22", "qt_votos_95", "qt_votos_96"]
].sum().sum()
assert total_votos_municipio == total_votos_zona, (total_votos_municipio, total_votos_zona)
print(f"\nConservação zona_mun == município: {total_votos_municipio} votos")

# Simula um segundo local no exterior (sem cd_mun no crosswalk) e confere que
# o total dele não some (bug real corrigido em 29/09/2026 — ver CLAUDE.md).
zona_mun_com_exterior = pd.concat(
    [
        res_zona_mun,
        pd.DataFrame(
            [{"id_zona_mun": f"{resultados.SENTINELA_EXTERIOR}0001", "qt_votos_13": 10,
              "qt_votos_22": 5, "qt_votos_95": 1, "qt_votos_96": 1,
              "qt_comparecimento": 17, "qt_aptos": 20}]
        ),
    ],
    ignore_index=True,
)
res_municipio_ext = resultados.agrega_zona_mun_por_municipio(zona_mun_com_exterior)
assert resultados.SENTINELA_EXTERIOR in set(res_municipio_ext["cd_mun"])
linha_exterior = res_municipio_ext[res_municipio_ext["cd_mun"] == resultados.SENTINELA_EXTERIOR]
assert linha_exterior["qt_votos_13"].iloc[0] == 10
print("Exterior preservado em agrega_zona_mun_por_municipio: OK")

# ---- 6. mudança de votação (2018 -> 2022, 2º turno) -------------------------

res_2018 = pd.DataFrame(
    {
        "cd_mun": ["1000000", "1000001", "1000002", "1000003", resultados.SENTINELA_EXTERIOR],
        "pc_validos_13": [0.60, 0.30, 0.55, 0.40, 0.90],
        "nr_vencedor": [13, 17, 13, 17, 13],
    }
)
res_2022 = pd.DataFrame(
    {
        "cd_mun": ["1000000", "1000001", "1000002", "1000003", resultados.SENTINELA_EXTERIOR],
        "pc_validos_13": [0.65, 0.45, 0.40, 0.55, 0.80],
        "nr_vencedor": [13, 22, 22, 13, 13],
    }
)
mudanca = resultados.mudanca_votacao(res_2018, res_2022, ano_antes=2018, ano_depois=2022, nr_pt=13)
print("\nmudança 2018->2022:\n", mudanca)
assert resultados.SENTINELA_EXTERIOR not in set(mudanca["cd_mun"])
sentidos = mudanca.set_index("cd_mun")["sentido_virada"]
assert sentidos["1000000"] == "manteve_pt"
assert sentidos["1000001"] == "manteve_bolsonaro"
assert sentidos["1000002"] == "virou_bolsonaro"
assert sentidos["1000003"] == "virou_pt"
# Bolsonaro venceu nos dois anos com números diferentes (17 -> 22): não é virada.
assert not mudanca.set_index("cd_mun").loc["1000001", "mudou_vencedor"]
assert mudanca["mudou_vencedor"].sum() == 2
assert np.isclose(mudanca.set_index("cd_mun").loc["1000000", "var_pc_validos_pt"], 0.05)

# ---- 6b. mudança por zona x município (alinha_zona_mun) ---------------------
# 1000000: zona única que trocou de número (0010 -> 0020) — tem de casar.
# 2000000: duas zonas, mesmos números nos dois anos — id mantido.

zm_2018 = pd.DataFrame(
    {
        "id_zona_mun": ["10000000010", "20000000001", "20000000002", f"{resultados.SENTINELA_EXTERIOR}0001"],
        "pc_validos_13": [0.40, 0.60, 0.30, 0.50],
        "nr_vencedor": [17, 13, 17, 13],
    }
)
zm_2022 = pd.DataFrame(
    {
        "id_zona_mun": ["10000000020", "20000000001", "20000000002", f"{resultados.SENTINELA_EXTERIOR}0001"],
        "pc_validos_13": [0.55, 0.62, 0.28, 0.60],
        "nr_vencedor": [13, 13, 22, 13],
    }
)
zm_2018_alinhado = resultados.alinha_zona_mun(zm_2018, zm_2022)
assert "10000000020" in set(zm_2018_alinhado["id_zona_mun"])
assert {"20000000001", "20000000002"} <= set(zm_2018_alinhado["id_zona_mun"])
mudanca_zm = resultados.mudanca_votacao(
    zm_2018_alinhado, zm_2022, ano_antes=2018, ano_depois=2022, nr_pt=13, chave="id_zona_mun"
)
print("\nmudança por zona x município:\n", mudanca_zm)
assert len(mudanca_zm) == 3  # exterior fora, os três territórios casados
assert mudanca_zm.set_index("id_zona_mun").loc["10000000020", "sentido_virada"] == "virou_pt"
assert not mudanca_zm.set_index("id_zona_mun").loc["20000000002", "mudou_vencedor"]

# ---- 6c. mudança sobre o eleitorado apto -------------------------------------
# Zona A: o exemplo 30 x 20 -> 50 x 50 (100 aptos nos dois anos). O swing nos
# válidos cai 10 p.p., mas Lula/Haddad ganha 20 p.p. do eleitorado (+66,7% de votos).
# Zona B: Lula/Haddad sobe e Bolsonaro cai (troca). Zona C: os dois caem (não-voto cresce).

def _res_aptos(votos_pt, votos_bo, aptos, nr_bo):
    df = pd.DataFrame({
        "id_zona_mun": ["10000000001", "20000000001", "30000000001"],
        "qt_votos_13": votos_pt, f"qt_votos_{nr_bo}": votos_bo, "qt_aptos": aptos,
    })
    validos = df["qt_votos_13"] + df[f"qt_votos_{nr_bo}"]
    df["pc_validos_13"] = df["qt_votos_13"] / validos
    df["nr_vencedor"] = np.where(df["qt_votos_13"] > df[f"qt_votos_{nr_bo}"], 13, nr_bo)
    return df

ap_2018 = _res_aptos([30, 40, 40], [20, 40, 40], [100, 100, 100], 17)
ap_2022 = _res_aptos([50, 55, 30], [50, 30, 30], [100, 100, 100], 22)
m_ap = resultados.mudanca_votacao(
    ap_2018, ap_2022, ano_antes=2018, ano_depois=2022, nr_pt=13, chave="id_zona_mun",
    nr_bolsonaro_antes=17, nr_bolsonaro_depois=22,
).set_index("id_zona_mun")
print("\nmudança sobre aptos:\n", m_ap.T)
a = m_ap.loc["10000000001"]
assert np.isclose(a["var_pc_validos_pt"], -0.10)
assert np.isclose(a["var_pc_aptos_pt"], 0.20)
assert np.isclose(a["var_rel_votos_pt"], 2 / 3)
assert a["tipo_mudanca"] == "bolsonaro_ambos_cresceram"
assert (a["qt_validos_2018"], a["qt_validos_2022"]) == (50, 100)  # só candidatos, sem branco/nulo
assert m_ap.loc["20000000001", "tipo_mudanca"] == "pt_troca"
assert m_ap.loc["30000000001", "tipo_mudanca"] == "pt_ambos_cairam"
soma = m_ap[["var_pc_aptos_pt", "var_pc_aptos_bolsonaro", "var_pc_aptos_nao_voto"]].sum(axis=1)
assert np.allclose(soma, 0), soma
# Sem os números do Bolsonaro, as colunas novas não aparecem (compatibilidade).
assert "tipo_mudanca" not in resultados.mudanca_votacao(
    ap_2018, ap_2022, ano_antes=2018, ano_depois=2022, nr_pt=13, chave="id_zona_mun"
).columns
print("Mudança sobre aptos: OK")

# ---- 6d. quem cresceu mais nos votos válidos ----------------------------------
# Com dois candidatos (acima) é o espelho: a magnitude é |Δ de Lula/Haddad| e não há 3º.
assert list(m_ap["cresceu_mais_validos"]) == ["bolsonaro", "pt", "empate"]
assert np.isclose(a["var_pc_validos_maior"], 0.10)
assert m_ap["nr_terceiro_2022"].isna().all()

# 1º turno, 100 válidos por zona. Zona A: Lula +1, bolsonarista +3 (outros encolhem)
# -> bolsonarista. Zona B: os dois caem e o 3º troca de candidato (15 com 10% ->
# 55 com 18%) -> terceiro, +8 p.p. Zona C: Lula +2, bolsonarista -5 -> Lula.
def _res_t1(votos: dict[int, list[int]]) -> pd.DataFrame:
    df = pd.DataFrame({"id_zona_mun": ["10000000001", "20000000001", "30000000001"], "qt_aptos": 200})
    for nr, v in votos.items():
        df[f"qt_votos_{nr}"] = v
    df["qt_votos_95"] = 5  # branco: fora dos válidos e do 3º lugar
    df["pc_validos_13"] = df["qt_votos_13"] / 100
    df["nr_vencedor"] = 13
    return df

t1_2022 = _res_t1({13: [40, 45, 40], 22: [45, 40, 40], 15: [10, 10, 12], 12: [5, 5, 8]})
t1_2026 = _res_t1({13: [41, 42, 43], 22: [48, 37, 35], 55: [6, 18, 13], 70: [5, 3, 9]})
m_t1 = resultados.mudanca_votacao(
    t1_2022, t1_2026, ano_antes=2022, ano_depois=2026, nr_pt=13, chave="id_zona_mun",
    nr_bolsonaro_antes=22, nr_bolsonaro_depois=22,
).set_index("id_zona_mun")
print("\nquem cresceu mais (1º turno):\n", m_t1[["cresceu_mais_validos", "var_pc_validos_maior", "nr_terceiro_2022", "nr_terceiro_2026"]])
assert list(m_t1["cresceu_mais_validos"]) == ["bolsonaro", "terceiro", "pt"]
assert np.allclose(m_t1["var_pc_validos_maior"], [0.03, 0.08, 0.03])
assert (m_t1.loc["20000000001", "nr_terceiro_2022"], m_t1.loc["20000000001", "nr_terceiro_2026"]) == (15, 55)
classes = cores.classifica_crescimento(m_t1["cresceu_mais_validos"], m_t1["var_pc_validos_maior"])
assert list(classes) == [7, 12, 3], classes  # Bolsonaro 2–5 p.p., 3º > 5 p.p., Lula 2–5 p.p.
print("Quem cresceu mais: OK")

# ---- 7. município direto da seção (agrega_secao_por_municipio) -------------
# Simula um ano sem locais processados (o caso de 2018): agrega direto de
# resultados_secao via crosswalk, sem passar por secao_local/locais.

crosswalk = pd.DataFrame({"cd_mun_tse": [CD_MUN], "cd_mun": ["3500000"]})
res_municipio_direto = resultados.agrega_secao_por_municipio(resultados_secao, crosswalk)
total_votos_direto = res_municipio_direto[
    ["qt_votos_13", "qt_votos_22", "qt_votos_95", "qt_votos_96"]
].sum().sum()
assert total_votos_direto == total_votos_local, (total_votos_direto, total_votos_local)
print(f"\nConservação seção == município (direto): {total_votos_direto} votos")

# Seção de município sem crosswalk vira SENTINELA_EXTERIOR, sem perder votos.
resultados_secao_exterior = pd.concat(
    [
        resultados_secao,
        pd.DataFrame(
            [{"ano": 2022, "turno": 1, "sg_uf": "ZZ", "cd_mun_tse": "888888", "nr_zona": 1,
              "nr_secao": 1, "cd_cargo": 1, "nr_votavel": 13, "nm_votavel": "13",
              "qt_votos": 7, "qt_comparecimento": 7, "qt_aptos": pd.NA}]
        ),
    ],
    ignore_index=True,
)
res_com_exterior = resultados.agrega_secao_por_municipio(resultados_secao_exterior, crosswalk)
assert resultados.SENTINELA_EXTERIOR in set(res_com_exterior["cd_mun"])
print("Exterior preservado em agrega_secao_por_municipio: OK")

print("\nOK — todos os testes manuais de resultados.py passaram.")
