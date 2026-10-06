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

from .config import NOME_PT_POR_ANO

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
    """Lula/Haddad forte -> Lula/Haddad fraco, empate, Bolsonaro fraco -> Bolsonaro forte."""
    return [*reversed(RAMPA_PT), COR_EMPATE, *RAMPA_BOLSONARO]


# Mapa de mudança no 1º turno: zonas em que Lula/Haddad e o bolsonarista caíram
# vão para o 3º lugar. Amarelo/ocre (matiz OKLCH 95, L 0,84 e 0,66): a violeta
# ficaria entre o vermelho e o azul e leria como mistura dos dois. Distância
# OKLab mínima para as outras cores do mapa conferida com simulação de
# deuteranopia/protanopia/tritanopia (06/10/2026); não passou pelo
# `validate_palette.js`.
RAMPA_TERCEIRO = ["#e7c952", "#ab9017"]
QUEBRA_TERCEIRO = 0.05


def cores_mudanca() -> list[str]:
    """`cores_divergentes()` + as duas classes do 3º lugar."""
    return [*cores_divergentes(), *RAMPA_TERCEIRO]


def classifica_crescimento(lado: pd.Series, magnitude: pd.Series) -> np.ndarray:
    """Índice em `cores_mudanca()`. `lado` é quem cresceu mais (`pt`, `bolsonaro`,
    `terceiro`, `empate`; outro valor = `SEM_DADO`) e `magnitude`, o quanto
    (fração). Crescimento <= 0 de Lula/Haddad ou Bolsonaro cai na classe mais clara."""
    v = pd.to_numeric(magnitude, errors="coerce").fillna(0).to_numpy(dtype=float)
    n = len(QUEBRAS_MUDANCA) + 1
    passo = np.digitize(np.clip(v, 0, None), QUEBRAS_MUDANCA)
    lado = lado.to_numpy(dtype=object)
    return np.select(
        [lado == "pt", lado == "bolsonaro", lado == "empate", lado == "terceiro"],
        [n - 1 - passo, n + 1 + passo, n, 2 * n + 1 + (v > QUEBRA_TERCEIRO)],
        default=SEM_DADO,
    ).astype(int)


# Mapa "de onde veio a mudança" (sobre o eleitorado apto): cor = quem teve o
# saldo, tom = mecanismo. Mesmo valor de `tipo_mudanca` em `resultados.py`.
# Escuro: um sobe e o outro cai; médio: os dois sobem (não-voto diminuiu);
# claro: os dois caem (não-voto aumentou).
TIPOS_MUDANCA = [
# `{pt}` vira o nome do candidato (ver `nome_pt_par`).
    ("pt_troca", "{pt} cresceu, Bolsonaro caiu", RAMPA_PT[4]),
    ("pt_ambos_cresceram", "Abst./brancos/nulos diminuíram; {pt} e Bolsonaro aumentaram, {pt} mais", RAMPA_PT[2]),
    ("pt_ambos_cairam", "Abst./brancos/nulos aumentaram; {pt} e Bolsonaro diminuíram, {pt} menos", RAMPA_PT[0]),
    ("bolsonaro_ambos_cairam", "Abst./brancos/nulos aumentaram; {pt} e Bolsonaro diminuíram, Bolsonaro menos", RAMPA_BOLSONARO[0]),
    ("bolsonaro_ambos_cresceram", "Abst./brancos/nulos diminuíram; {pt} e Bolsonaro aumentaram, Bolsonaro mais", RAMPA_BOLSONARO[2]),
    ("bolsonaro_troca", "Bolsonaro cresceu, {pt} caiu", RAMPA_BOLSONARO[4]),
]


def nome_pt_par(ano_antes: int, ano_depois: int) -> str:
    """Candidato do PT nos dois anos: "Lula", ou "Haddad/Lula" se mudou."""
    nomes = dict.fromkeys(NOME_PT_POR_ANO[a] for a in (ano_antes, ano_depois))
    return "/".join(nomes)


def cores_tipo_mudanca() -> list[str]:
    return [cor for _, _, cor in TIPOS_MUDANCA]


def classifica_tipo_mudanca(tipos: pd.Series) -> np.ndarray:
    """Índice em `TIPOS_MUDANCA`, NaN/desconhecido = `SEM_DADO`."""
    indice = {chave: i for i, (chave, _, _) in enumerate(TIPOS_MUDANCA)}
    return tipos.map(indice).fillna(SEM_DADO).astype(int).to_numpy()


def classifica_divergente(valores: pd.Series, quebras: tuple[float, ...]) -> np.ndarray:
    """Índice em `cores_divergentes()`. Positivo = Lula/Haddad, negativo = Bolsonaro,
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


def legenda_mudanca(ano_antes: int, ano_depois: int, turno: int) -> dict:
    faixas = _faixas(QUEBRAS_MUDANCA, " p.p.")
    nome_pt = nome_pt_par(ano_antes, ano_depois)
    rotulos = [
        *(f"{nome_pt} ganhou {f}" for f in reversed(faixas)),
        "Cresceram igual",
        *(f"Bolsonaro ganhou {f}" for f in faixas),
    ]
    nota = "A cor é de quem mais cresceu em p.p. dos votos válidos. Contorno escuro: a zona trocou de vencedor."
    # No 2º turno não há 3º lugar: um sobe exatamente o que o outro cai.
    if turno == 1:
        corte = f"{100 * QUEBRA_TERCEIRO:g}".replace(".", ",")
        rotulos += [f"O 3º lugar ganhou até {corte} p.p.", f"O 3º lugar ganhou mais de {corte} p.p."]
        nota += (
            f" 3º lugar: zonas em que {nome_pt} e Bolsonaro caíram; compara o mais votado "
            "fora os dois em cada eleição, mesmo que não seja o mesmo candidato."
        )
    return _legenda(
        f"Quem cresceu mais nos votos válidos, {ano_antes} → {ano_depois} ({turno}º turno)",
        cores_mudanca(),
        rotulos,
        nota=nota,
    )


def legenda_tipo_mudanca(ano_antes: int, ano_depois: int, turno: int) -> dict:
    return _legenda(
        f"De onde veio a mudança, {ano_antes} → {ano_depois}, {turno}º turno (% do eleitorado)",
        cores_tipo_mudanca(),
        [rotulo.format(pt=nome_pt_par(ano_antes, ano_depois)) for _, rotulo, _ in TIPOS_MUDANCA],
        nota=(
            "Saldo da zona em votos sobre os inscritos. Abst./brancos/nulos incluem, no "
            "1º turno, os demais candidatos. Nas trocas diretas (um sobe e o outro cai) "
            "eles podem ter subido ou descido. Não indica eleitores que trocaram de lado."
        ),
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
