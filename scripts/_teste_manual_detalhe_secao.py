"""Teste manual: fontes.ler_votacao_secao com detalhe_votacao_secao.

Confere que, quando o arquivo de detalhe está disponível, qt_aptos vem dele
(não de locais.qt_eleitores) e qt_comparecimento usa o valor do TSE quando
presente, caindo na soma de qt_votos só para a seção sem match no detalhe.

Roda com: venv/Scripts/python scripts/_teste_manual_detalhe_secao.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from eleicoes import fontes

tmp = Path(tempfile.mkdtemp())
CD_MUN, ANO, TURNO = "99999", 2022, 1

# 2 seções: uma com match no detalhe (qt_aptos real diferente do que a soma
# de votos sozinha "adivinharia"), outra sem match (cai no fallback de soma).
votacao_secao_csv = tmp / "votacao_secao.csv"
with open(votacao_secao_csv, "w", encoding="latin-1", newline="") as f:
    f.write("SG_UF;CD_MUNICIPIO;NR_ZONA;NR_SECAO;NR_TURNO;CD_CARGO;NR_VOTAVEL;NM_VOTAVEL;QT_VOTOS\n")
    linhas = [
        ("SP", CD_MUN, 1, 1, TURNO, 1, 13, "CAND13", 100),
        ("SP", CD_MUN, 1, 1, TURNO, 1, 96, "NULO", 5),
        ("SP", CD_MUN, 1, 2, TURNO, 1, 13, "CAND13", 50),
        ("SP", CD_MUN, 1, 2, TURNO, 1, 96, "NULO", 2),
    ]
    for l in linhas:
        f.write(";".join(str(x) for x in l) + "\n")

detalhe_csv = tmp / "detalhe.csv"
with open(detalhe_csv, "w", encoding="latin-1", newline="") as f:
    f.write("CD_MUNICIPIO;NR_ZONA;NR_SECAO;NR_TURNO;QT_APTOS;QT_COMPARECIMENTO\n")
    # só a seção 1 tem match no detalhe; QT_APTOS=200 é o valor real do TSE,
    # bem diferente do que qt_eleitores (fallback) daria.
    f.write(f"{CD_MUN};1;1;{TURNO};200;105\n")

resultado = fontes.ler_votacao_secao(votacao_secao_csv, ANO, TURNO, caminho_detalhe=detalhe_csv)
print(resultado[["nr_secao", "nr_votavel", "qt_votos", "qt_comparecimento", "qt_aptos"]])

secao1 = resultado[resultado["nr_secao"] == 1]
secao2 = resultado[resultado["nr_secao"] == 2]

# seção 1: veio do detalhe (200), não da soma de votos (105, que por acaso é
# igual ao qt_comparecimento real aqui, mas qt_aptos NUNCA viria disso).
assert (secao1["qt_aptos"] == 200).all(), secao1["qt_aptos"].tolist()
assert (secao1["qt_comparecimento"] == 105).all()

# seção 2: sem match no detalhe -> qt_aptos fica NA, qt_comparecimento cai no
# fallback (soma de qt_votos = 50+2 = 52).
assert secao2["qt_aptos"].isna().all(), secao2["qt_aptos"].tolist()
assert (secao2["qt_comparecimento"] == 52).all()

print("\nOK — detalhe usado quando disponível, fallback por soma quando não.")
