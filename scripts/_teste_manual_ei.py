"""Teste manual de `ei.py`: o modelo recupera taxas por grupo que conhecemos?

Dados sintéticos, só para validar o código e ver o que o modelo consegue e não
consegue recuperar (não são dados reais): composição inventada de 4 escolaridades ×
3 rendas em 600 zonas de 6 UFs, taxas de voto por grupo conhecidas, ruído por zona.

Mostra:
  1. `limites_deterministicos`: o quanto os dados sozinhos restringem a resposta;
  2. a recuperação das taxas nacionais por grupo (erro e cobertura do intervalo de 90%);
  3. o mesmo com a hipótese central violada (taxa do grupo ligada à composição da zona).

Roda com: python scripts/_teste_manual_ei.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from eleicoes import ei

N_ESC, N_RENDA, N_OP, N_UF, N_ZONAS = 4, 3, 5, 6, 600
CONTEXTO_OBS = None
rng = np.random.default_rng(42)


def gera_composicao() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Composição `(Z, G)`, UF e contexto da zona (quanto mais alto, mais rica)."""
    uf = rng.integers(0, N_UF, N_ZONAS)
    contexto = rng.normal(size=N_ZONAS) + 0.3 * (uf - 2.5)
    pesos = []
    for z in range(N_ZONAS):
        e = np.array([0.45, 0.22, 0.25, 0.08]) * np.exp(np.array([-0.3, 0, 0.2, 0.7]) * contexto[z])
        e /= e.sum()
        r = np.array([0.5, 0.4, 0.1]) * np.exp(np.array([-0.5, 0, 0.9]) * contexto[z])
        r /= r.sum()
        conj = np.outer(e, r) * np.exp(rng.normal(0, 0.15, (N_ESC, N_RENDA)))
        pesos.append((conj / conj.sum()).ravel())
    n = rng.integers(2_000, 200_000, N_ZONAS).astype(float)
    return np.array(pesos), n, uf, contexto


def taxas_verdadeiras() -> np.ndarray:
    """Taxa de voto por grupo `(G, C)`: PT, PL, outros, branco/nulo, abstenção."""
    logit = np.zeros((N_ESC, N_RENDA, N_OP))
    e = np.array([0.5, 0.2, -0.2, -0.4])[:, None]  # escolaridade mais alta, menos PT
    r = np.array([0.6, 0.0, -0.5])[None, :]        # renda mais alta, menos PT
    logit[:, :, 0] = 0.3 + e + r
    logit[:, :, 1] = 0.0 - 0.5 * e - 0.5 * r
    logit[:, :, 2] = -2.5
    logit[:, :, 3] = -2.5
    logit[:, :, 4] = -1.4
    theta = np.exp(logit)
    return (theta / theta.sum(axis=-1, keepdims=True)).reshape(-1, N_OP)


def simula(viola: bool) -> tuple[np.ndarray, ...]:
    global CONTEXTO_OBS
    peso, n, uf, contexto = gera_composicao()
    theta = np.broadcast_to(taxas_verdadeiras(), (N_ZONAS, N_ESC * N_RENDA, N_OP)).copy()
    # heterogeneidade da zona: desloca o logit de todos os grupos
    ruido = rng.normal(0, 0.12, (N_ZONAS, 1, N_OP))
    if viola:  # a taxa de cada grupo depende do contexto da zona (viola a hipótese central)
        ruido[:, :, 0] += 0.5 * contexto[:, None]
        ruido[:, :, 1] -= 0.5 * contexto[:, None]
    logit = np.log(theta) + ruido
    theta = np.exp(logit)
    theta /= theta.sum(axis=-1, keepdims=True)
    p = np.einsum("zg,zgc->zc", peso, theta)
    votos = np.array([rng.multinomial(int(n[z]), p[z]) for z in range(N_ZONAS)]).astype(float)
    # contexto observado pelo analista: o verdadeiro, com erro de medida (ex.: renda média da área)
    CONTEXTO_OBS = (contexto + rng.normal(0, 0.5, N_ZONAS))[:, None]
    CONTEXTO_OBS = (CONTEXTO_OBS - CONTEXTO_OBS.mean()) / CONTEXTO_OBS.std()
    # taxa verdadeira nacional por grupo (média ponderada pelos eleitores do grupo)
    massa = peso * n[:, None]
    verdade = np.einsum("zg,zgc->gc", massa, theta) / massa.sum(axis=0)[:, None]
    return peso, votos, uf, verdade, massa


def avalia(viola: bool, usa_contexto: bool = False) -> None:
    rotulo = ("COM a hipótese violada" if viola else "hipótese respeitada") + (", COM covariável de contexto" if usa_contexto else "")
    print(f"\n=== Simulação ({rotulo}) ===")
    peso, votos, uf, verdade, massa = simula(viola)
    composicao = peso * votos.sum(axis=1)[:, None]

    inf, sup = ei.limites_deterministicos(composicao, votos)
    amplitude = (sup - inf)[:, 0]
    print(f"limites determinísticos da taxa de PT: amplitude mediana {100 * np.median(amplitude):.0f} p.p. "
          f"(mín {100 * amplitude.min():.0f}, máx {100 * amplitude.max():.0f})")
    assert np.all(inf <= verdade + 1e-9) and np.all(verdade <= sup + 1e-9), "taxa verdadeira fora dos limites"

    ajuste = ei.ajusta_svi(peso, votos, uf, N_ESC, N_RENDA, passos=2500, contexto=CONTEXTO_OBS if usa_contexto else None)
    perda = ajuste["perda"]
    print(f"ELBO: início {perda[:50].mean():.0f} -> fim {perda[-50:].mean():.0f}")
    amostras = ei.amostra_posterior(ajuste, n=200)
    taxas = ei.taxas_por_grupo(ajuste, amostras, {"pais": np.arange(N_ZONAS)})["pais"]
    r = ei.resume(taxas)

    erro = np.abs(r["media"] - verdade)
    dentro = (verdade >= r["inferior"]) & (verdade <= r["superior"])
    print(f"taxa de PT, 12 grupos — erro absoluto médio {100 * erro[:, 0].mean():.1f} p.p., "
          f"máximo {100 * erro[:, 0].max():.1f}; cobertura do intervalo de 90%: {100 * dentro[:, 0].mean():.0f}%")
    print(f"todas as opções — erro médio {100 * erro.mean():.1f} p.p.; cobertura {100 * dentro.mean():.0f}%")
    print("ordem dos grupos na taxa de PT (verdade vs estimada), correlação de Spearman: "
          f"{np.corrcoef(np.argsort(np.argsort(verdade[:, 0])), np.argsort(np.argsort(r['media'][:, 0])))[0, 1]:.2f}")
    soma = taxas.sum(axis=-1)
    assert np.allclose(soma, 1.0, atol=1e-4), "taxas de um grupo não somam 1"


if __name__ == "__main__":
    avalia(viola=False)
    avalia(viola=True)
    avalia(viola=True, usa_contexto=True)
    print("\nOK — código rodou; leia as coberturas acima antes de confiar no modelo.")
