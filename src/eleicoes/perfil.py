"""Perfil do eleitorado por local de votação, a partir do cadastro do TSE.

Recebe a saída de `fontes.ler_perfil_eleitor_secao` (seção × gênero × faixa
etária × escolaridade) e devolve uma linha por `id_local`, com contagens
`qt_...`. O prefixo `qt_` é o que faz `resultados.agrega_por_setor` distribuir
essas colunas para os setores pela mesma matriz de pesos dos votos.

A escolaridade do cadastro é a declarada no alistamento e quase nunca é
atualizada: subestima a escolaridade atual, sobretudo de quem se alistou jovem.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .resultados import secao_para_id_local

# CD_GRAU_ESCOLARIDADE do TSE: 1 analfabeto, 2 lê e escreve, 3 fundamental
# incompleto, 4 fundamental completo, 5 médio incompleto, 6 médio completo,
# 7 superior incompleto, 8 superior completo (0 = não informado, fora da conta).
CODIGOS_ESCOLARIDADE = tuple(range(1, 9))
GRUPOS_ESCOLARIDADE = {
    "ate_fund_incompleto": (1, 2, 3),
    "fund_completo": (4, 5),
    "medio_completo": (6, 7),
    "superior": (8,),
}

CD_GENERO_MASCULINO = 2
CD_GENERO_FEMININO = 4

# Idade inicial de cada faixa; a última é aberta.
FAIXAS_IDADE = {"16a24": 16, "25a34": 25, "35a44": 35, "45a59": 45, "60mais": 60}


def agrega_perfil_por_local(
    perfil_secao: pd.DataFrame, secao_local: pd.DataFrame, *, turno: int = 1
) -> pd.DataFrame:
    """Soma o cadastro por `id_local`.

    A seção é ligada ao local por `secao_local` (etapa 01), e não pelo
    `NR_LOCAL_VOTACAO` do próprio arquivo de perfil: as seções agregadas votam
    no local da seção principal, e é lá que os votos delas aparecem.

    Args:
        perfil_secao: saída de `fontes.ler_perfil_eleitor_secao`.
        secao_local: `secao_local_{ano}.parquet` (etapa 01).
        turno: turno usado para achar o local da seção (o local pode mudar
            entre os turnos; o cadastro é um só).

    Returns:
        Uma linha por `id_local`, com `qt_eleitores_perfil`, `qt_esc_{1..8}`,
        `qt_genero_feminino`/`_masculino`, `qt_idade_{faixa}`, `qt_idade_valida`
        e `qt_idade_soma` (soma das idades, para a média).
    """
    com_id = secao_para_id_local(perfil_secao.assign(turno=turno), secao_local)
    base = com_id.dropna(subset=["id_local"])

    # CD_FAIXA_ETARIA junta os dois limites da faixa: 4549 = 45 a 49 anos,
    # 1900 = 19 anos. Negativo = idade inválida.
    codigo = base["cd_faixa_etaria"]
    idade_valida = codigo > 0
    idade_min = (codigo // 100).where(idade_valida)
    idade_max = (codigo % 100).where(codigo % 100 > 0, idade_min)
    faixa = pd.cut(
        idade_min,
        bins=[*FAIXAS_IDADE.values(), np.inf],
        right=False,
        labels=list(FAIXAS_IDADE),
    )

    qt = base["qt_eleitores"]
    partes = {
        "qt_eleitores_perfil": qt,
        "qt_genero_feminino": qt.where(base["cd_genero"] == CD_GENERO_FEMININO, 0),
        "qt_genero_masculino": qt.where(base["cd_genero"] == CD_GENERO_MASCULINO, 0),
        "qt_idade_valida": qt.where(idade_valida, 0),
        "qt_idade_soma": (qt * (idade_min + idade_max) / 2).fillna(0),
    }
    for cd in CODIGOS_ESCOLARIDADE:
        partes[f"qt_esc_{cd}"] = qt.where(base["cd_grau_escolaridade"] == cd, 0)
    for nome in FAIXAS_IDADE:
        partes[f"qt_idade_{nome}"] = qt.where(faixa == nome, 0)

    return pd.DataFrame(partes).groupby(base["id_local"]).sum().reset_index()


def calcula_indicadores_perfil(perfil_largo: pd.DataFrame) -> pd.DataFrame:
    """Calcula as proporções (0–1) e a idade média a partir das contagens.

    Como em `resultados.calcula_indicadores`, só faz sentido depois de
    agregar: chamar uma vez por nível (local, setor).

    Returns:
        `perfil_largo` com `pc_esc_{grupo}` (ver `GRUPOS_ESCOLARIDADE`),
        `pc_feminino`, `idade_media` e `pc_idade_{faixa}`.
    """
    df = perfil_largo.copy()

    total_esc = df[[f"qt_esc_{cd}" for cd in CODIGOS_ESCOLARIDADE]].sum(axis=1).replace(0, np.nan)
    for nome, codigos in GRUPOS_ESCOLARIDADE.items():
        df[f"pc_esc_{nome}"] = df[[f"qt_esc_{cd}" for cd in codigos]].sum(axis=1) / total_esc

    total_genero = (df["qt_genero_feminino"] + df["qt_genero_masculino"]).replace(0, np.nan)
    df["pc_feminino"] = df["qt_genero_feminino"] / total_genero

    total_idade = df["qt_idade_valida"].replace(0, np.nan)
    df["idade_media"] = df["qt_idade_soma"] / total_idade
    for nome in FAIXAS_IDADE:
        df[f"pc_idade_{nome}"] = df[f"qt_idade_{nome}"] / total_idade

    return df
