"""Agregação de resultados: seção → local → zona×município e local → setor.

Recebe o esquema longo de `fontes.py` (uma linha por seção × votável) e produz
as três tabelas de resultado do modelo de dados (PLAN.md > Modelo de dados):

    res_..._local     -- observado, por id_local
    res_..._zona_mun  -- observado, por id_zona_mun (mesma agregação do TSE)
    res_..._setor     -- estimado, por cd_setor (via matriz de pesos)

Os indicadores (percentuais, vencedor, margem) só fazem sentido depois de
agregar, então `calcula_indicadores` é chamado em cada nível separadamente,
nunca antes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CODIGO_BRANCO, CODIGO_NULO


def secao_para_id_local(
    resultados_secao: pd.DataFrame,
    secao_local: pd.DataFrame,
    *,
    col_id_secao: str = "id_secao",
) -> pd.DataFrame:
    """Liga cada linha de resultado por seção ao seu `id_local`.

    Args:
        resultados_secao: esquema longo de `fontes.py`, com `cd_mun_tse`,
            `nr_zona`, `nr_secao`.
        secao_local: saída da etapa 01 (`secao_local_{ano}.parquet`), com
            `id_secao` e `id_local` (seções agregadas já resolvidas para o
            local da seção principal).

    Returns:
        `resultados_secao` com as colunas `id_secao` e `id_local` adicionadas.
        Seções sem `id_local` (deveria ser só o exterior) ficam com NA e devem
        ser contadas/logadas por quem chama — ver PLAN.md > Verificação.
    """
    df = resultados_secao.copy()
    df["id_secao"] = (
        df["cd_mun_tse"].astype(str)
        + df["nr_zona"].astype(int).astype(str).str.zfill(4)
        + df["nr_secao"].astype(int).astype(str).str.zfill(4)
    )
    return df.merge(secao_local[["id_secao", "id_local"]], on="id_secao", how="left")


def agrega_por_local(resultados_secao_com_id: pd.DataFrame) -> pd.DataFrame:
    """Soma `qt_votos` por `id_local` × `nr_votavel`, e `qt_aptos`/`qt_comparecimento`
    por `id_local` (contando cada seção uma única vez).

    Returns:
        DataFrame largo: uma linha por `id_local`, uma coluna por
        `qt_votos_{nr_votavel}` + `qt_aptos` + `qt_comparecimento`.
    """
    base = resultados_secao_com_id.dropna(subset=["id_local"])

    votos_largo = (
        base.pivot_table(
            index="id_local",
            columns="nr_votavel",
            values="qt_votos",
            aggfunc="sum",
            fill_value=0,
        )
        .rename(columns=lambda nr: f"qt_votos_{int(nr)}")
        .reset_index()
    )

    # qt_aptos/qt_comparecimento são por seção, não por (seção, votável) —
    # contar uma vez por (id_secao) para não somar em duplicidade.
    aptos = (
        base.drop_duplicates("id_secao")
        .groupby("id_local")[["qt_aptos", "qt_comparecimento"]]
        .sum()
        .reset_index()
    )

    return votos_largo.merge(aptos, on="id_local", how="left")


def agrega_por_zona_mun(
    resultados_secao_com_id: pd.DataFrame,
    locais: pd.DataFrame,
    *,
    col_id_local: str = "id_local",
    col_cd_mun: str = "cd_mun",
) -> pd.DataFrame:
    """Agrega direto da seção, por `id_zona_mun` (inclui os locais especiais).

    Esta é a agregação "observada": deve bater linha a linha com
    `votacao_candidato_munzona` do TSE (ver PLAN.md > Verificação). Diferente
    de `agrega_por_local`, aqui não se filtra `flag_especial`.

    Args:
        resultados_secao_com_id: saída de `secao_para_id_local`. Já traz
            `nr_zona` (a zona da própria seção) — só falta o `cd_mun` (código
            IBGE), que vem de `locais` via `id_local`.
        locais: tabela de locais (etapa 01), com `id_local`, `cd_mun`.
    """
    base = resultados_secao_com_id.dropna(subset=["id_local"]).merge(
        locais[[col_id_local, col_cd_mun]], on=col_id_local, how="left"
    )
    base["id_zona_mun"] = (
        base[col_cd_mun].astype(str) + base["nr_zona"].astype(int).astype(str).str.zfill(4)
    )

    votos_largo = (
        base.pivot_table(
            index="id_zona_mun",
            columns="nr_votavel",
            values="qt_votos",
            aggfunc="sum",
            fill_value=0,
        )
        .rename(columns=lambda nr: f"qt_votos_{int(nr)}")
        .reset_index()
    )

    aptos = (
        base.drop_duplicates("id_secao")
        .groupby("id_zona_mun")[["qt_aptos", "qt_comparecimento"]]
        .sum()
        .reset_index()
    )

    return votos_largo.merge(aptos, on="id_zona_mun", how="left")


def agrega_por_setor(
    resultados_local: pd.DataFrame,
    pesos_local_setor: pd.DataFrame,
    *,
    col_id_local: str = "id_local",
    col_setor: str = "cd_setor",
    col_peso: str = "peso",
) -> pd.DataFrame:
    """Distribui os resultados por local para os setores, via `pesos_local_setor`.

    `votos_setor(s, nr) = Σ_L votos_local(L, nr) * peso(L, s)` — a etapa
    "`W ×` votos" do PLAN.md. As colunas numéricas de `resultados_local` (tudo
    que começa com `qt_`) são distribuídas da mesma forma.

    Locais sem entrada em `pesos_local_setor` (ex.: fora do MVP de alocação,
    ou locais especiais que não entram na distribuição por design — ver
    PLAN.md > Método) são simplesmente ignorados aqui, e por isso o total por
    setor não inclui o voto em trânsito nem as seções especiais.
    """
    colunas_qt = [c for c in resultados_local.columns if c.startswith("qt_")]

    base = resultados_local.merge(
        pesos_local_setor[[col_id_local, col_setor, col_peso]],
        on=col_id_local,
        how="inner",
    )
    for col in colunas_qt:
        base[col] = base[col] * base[col_peso]

    return base.groupby(col_setor)[colunas_qt].sum().reset_index()


def calcula_indicadores(
    resultados_largo: pd.DataFrame,
    *,
    cols_votos_validos: list[str],
) -> pd.DataFrame:
    """Calcula os indicadores derivados (percentuais 0–1, vencedor, margem).

    Args:
        resultados_largo: uma linha por unidade territorial, com `qt_aptos`,
            `qt_comparecimento`, `qt_votos_{CODIGO_BRANCO}`, `qt_votos_{CODIGO_NULO}`
            e `qt_votos_{nr}` para cada candidato.
        cols_votos_validos: nomes das colunas `qt_votos_{nr}` dos candidatos
            (excluindo branco e nulo).

    Returns:
        `resultados_largo` com `pc_comparecimento`, `pc_abstencao`,
        `pc_brancos`, `pc_nulos`, `pc_validos_{nr}`, `nr_vencedor`, `margem`.
    """
    df = resultados_largo.copy()
    col_brancos = f"qt_votos_{CODIGO_BRANCO}"
    col_nulos = f"qt_votos_{CODIGO_NULO}"

    df["pc_comparecimento"] = df["qt_comparecimento"] / df["qt_aptos"]
    df["pc_abstencao"] = 1 - df["pc_comparecimento"]

    qt_validos = df[cols_votos_validos].sum(axis=1)
    df["pc_brancos"] = df.get(col_brancos, 0) / df["qt_comparecimento"]
    df["pc_nulos"] = df.get(col_nulos, 0) / df["qt_comparecimento"]

    for col in cols_votos_validos:
        df[col.replace("qt_votos_", "pc_validos_")] = df[col] / qt_validos.replace(
            0, pd.NA
        )

    ranking = df[cols_votos_validos].rank(axis=1, method="first", ascending=False)
    df["nr_vencedor"] = (
        ranking.eq(1)
        .idxmax(axis=1)
        .str.replace("qt_votos_", "", regex=False)
        .astype(int)
    )

    # Ordena os votos de cada linha em ordem decrescente (numpy puro, sem
    # depender do índice do apply) para achar 1º e 2º colocados.
    votos_ordenados = -np.sort(-df[cols_votos_validos].to_numpy(), axis=1)
    primeiro, segundo = votos_ordenados[:, 0], votos_ordenados[:, 1]
    df["margem"] = np.where(
        qt_validos > 0, (primeiro - segundo) / qt_validos.replace(0, pd.NA), pd.NA
    )

    return df
