"""Classes de cor e legendas dos mapas (visualizador do site e Folium local).

Classes **discretas**, com cortes fixos iguais em todos os anos e turnos (dá
para comparar 2018 com 2022 pela cor). A classificação é feita aqui, em
Python; o visualizador (`assets/mapas.js`) só pinta pelo índice da classe.

Rampas: a azul sai de `dataviz/references/palette.md` (passos 250→700); a
vermelha e a violeta foram geradas com a mesma luminosidade OKLab de cada
passo azul. O croma mínimo é 0,12 em todas: com o croma original da paleta
(~0,10) a classe mais clara lia como cinza, e margens de ~3 p.p. pareciam
empate. As três passam em `validate_palette.js --ordinal --mode light`, e o
par de polos mais claros (vermelho × azul) passa no piso de croma e de
separação CVD.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SEM_DADO = -1
COR_SEM_DADO = "#c3c2b7"
COR_EMPATE = "#f0efec"

# Claro -> escuro.
RAMPA_PT = ["#f5938c", "#dd716a", "#c74845", "#9e3432", "#681114"]
RAMPA_BOLSONARO = ["#7eb5fe", "#5997e8", "#2a78d6", "#1a5cab", "#01366f"]
# Violeta: não usar azul nas métricas sem polo, para não ler como "Bolsonaro".
RAMPA_SEQUENCIAL = ["#a9a8fc", "#8c88e6", "#6f67d4", "#564ea9", "#322972"]
# Cinza neutro, em passos iguais de luminosidade OKLab (0,81 -> 0,27). A faixa é
# mais larga que a das rampas acima: sem matiz, só a luminosidade separa as classes.
# Usada na GeoReDUS para brancos e nulos (o visualizador do site segue na violeta).
RAMPA_CINZA = ["#c1c1c1", "#9b9b9b", "#777777", "#525252", "#262626"]

# Cortes em fração (0,05 = 5 p.p. ou 5%). Quatro cortes -> cinco classes.
# Brancos/nulos e abstenção: quintis arredondados da distribuição conjunta dos
# locais em 2018 e 2022, dois turnos (29/09/2026: brancos/nulos 3,2/4,6/6,3/9,0%;
# abstenção 15,1/18,4/21,6/25,7%). Em 2018 brancos/nulos foi ~o dobro de 2022,
# e cortes por ano esconderiam isso.
QUEBRAS_MARGEM = (0.05, 0.15, 0.30, 0.50)
QUEBRAS_MUDANCA = (0.02, 0.05, 0.10, 0.15)
QUEBRAS_BRANCO_NULO = (0.03, 0.05, 0.07, 0.10)
QUEBRAS_ABSTENCAO = (0.15, 0.18, 0.21, 0.25)


def cores_divergentes() -> list[str]:
    """PT forte -> PT fraco, empate, Bolsonaro fraco -> Bolsonaro forte."""
    return [*reversed(RAMPA_PT), COR_EMPATE, *RAMPA_BOLSONARO]


def classifica_divergente(valores: pd.Series, quebras: tuple[float, ...]) -> np.ndarray:
    """Índice em `cores_divergentes()`. Positivo = PT, negativo = Bolsonaro,
    exatamente 0 = empate, NaN = `SEM_DADO`."""
    v = pd.to_numeric(valores, errors="coerce").to_numpy(dtype=float)
    n = len(quebras) + 1
    passo = np.digitize(np.abs(np.nan_to_num(v)), quebras)
    idx = np.where(v > 0, n - 1 - passo, np.where(v < 0, n + 1 + passo, n))
    return np.where(np.isnan(v), SEM_DADO, idx).astype(int)


def classifica_sequencial(valores: pd.Series, quebras: tuple[float, ...]) -> np.ndarray:
    """Índice em `RAMPA_SEQUENCIAL` (0 = menor), NaN = `SEM_DADO`."""
    v = pd.to_numeric(valores, errors="coerce").to_numpy(dtype=float)
    idx = np.digitize(np.nan_to_num(v), quebras)
    return np.where(np.isnan(v), SEM_DADO, idx).astype(int)


def _faixas(quebras: tuple[float, ...], unidade: str) -> list[str]:
    """Textos das faixas, da mais fraca para a mais forte:
    "até 5 p.p.", "5 a 15 p.p.", ..., "50 p.p. ou mais"."""
    q = [f"{100 * x:g}".replace(".", ",") for x in quebras]
    return [
        f"até {q[0]}{unidade}",
        *(f"{a} a {b}{unidade}" for a, b in zip(q, q[1:])),
        f"{q[-1]}{unidade} ou mais",
    ]


def legenda_margem(nome_pt: str) -> dict:
    faixas = _faixas(QUEBRAS_MARGEM, " p.p.")
    rotulos = [
        *(f"{nome_pt} por {f}" for f in reversed(faixas)),
        "Empate",
        *(f"Bolsonaro por {f}" for f in faixas),
    ]
    return _legenda(f"Vantagem nos votos válidos ({nome_pt} × Bolsonaro)", cores_divergentes(), rotulos)


def legenda_mudanca() -> dict:
    faixas = _faixas(QUEBRAS_MUDANCA, " p.p.")
    rotulos = [
        *(f"PT ganhou {f}" for f in reversed(faixas)),
        "Sem variação",
        *(f"PT perdeu {f}" for f in faixas),
    ]
    return _legenda(
        "Variação do PT nos votos válidos, 2018 → 2022",
        cores_divergentes(),
        rotulos,
        nota="Contorno escuro: a zona trocou de vencedor.",
    )


def legenda_sequencial(
    titulo: str, quebras: tuple[float, ...], rampa: list[str] = RAMPA_SEQUENCIAL
) -> dict:
    return _legenda(titulo, rampa, _faixas(quebras, "%"))


def _legenda(titulo: str, cores: list[str], rotulos: list[str], nota: str | None = None) -> dict:
    legenda = {
        "titulo": titulo,
        "itens": [{"cor": c, "rotulo": r} for c, r in zip(cores, rotulos)],
        "sem_dado": COR_SEM_DADO,
    }
    if nota:
        legenda["nota"] = nota
    return legenda
