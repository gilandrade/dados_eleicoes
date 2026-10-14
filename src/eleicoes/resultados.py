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

from .config import (
    CODIGO_ANULADO_APURADO_SEPARADO,
    CODIGO_BRANCO,
    CODIGO_NULO,
    CODIGOS_NAO_CANDIDATO,
)

# Prefixo de id_zona_mun para locais sem cd_mun (o exterior, sem crosswalk —
# ver 00_insumos.qmd). 8 caracteres, não colide com nenhum cd_mun real
# (7 dígitos).
SENTINELA_EXTERIOR = "EXTERIOR"


def secao_para_id_local(
    resultados_secao: pd.DataFrame,
    secao_local: pd.DataFrame,
    *,
    col_id_secao: str = "id_secao",
) -> pd.DataFrame:
    """Liga cada linha de resultado por seção ao seu `id_local`.

    Args:
        resultados_secao: esquema longo de `fontes.py`, com `cd_mun_tse`,
            `nr_zona`, `nr_secao`, `turno`.
        secao_local: saída da etapa 01 (`secao_local_{ano}.parquet`), com
            `id_secao`, `nr_turno` e `id_local` (seções agregadas já
            resolvidas para o local da seção principal — **do mesmo turno**:
            o local de uma seção pode mudar de um turno para o outro; 0,28%
            mudou em 2022, ver `01_locais_votacao.qmd`). O join é por
            `(id_secao, turno)`, não só por `id_secao`.

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
    return df.merge(
        secao_local[["id_secao", "nr_turno", "id_local"]],
        left_on=["id_secao", "turno"],
        right_on=["id_secao", "nr_turno"],
        how="left",
    ).drop(columns="nr_turno")


def agrega_por_local(
    resultados_secao_com_id: pd.DataFrame,
    locais: pd.DataFrame,
    *,
    col_id_local: str = "id_local",
    col_qt_eleitores: str = "qt_eleitores",
) -> pd.DataFrame:
    """Soma `qt_votos` por `id_local` × `nr_votavel`, e `qt_comparecimento`/
    `qt_aptos` por `id_local` (contando cada seção uma única vez).

    `qt_aptos`: se `resultados_secao_com_id` já traz um `qt_aptos` por seção
    (de `fontes.ler_votacao_secao(..., caminho_detalhe=...)`, o real do TSE),
    soma esse valor por local. Onde ele faltar — `fontes.ler_bweb()`, ou
    `ler_votacao_secao()` sem o arquivo de detalhe — cai em
    `locais.qt_eleitores` (soma de `QT_ELEITOR_SECAO` da etapa 01,
    aproximação a menos de 0,002% no teste com 2022).

    Args:
        resultados_secao_com_id: saída de `secao_para_id_local`.
        locais: tabela de locais (etapa 01), com `col_id_local` e
            `col_qt_eleitores`.

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

    # qt_comparecimento/qt_aptos são por seção, não por (seção, votável) —
    # contar uma vez por (id_secao) para não somar em duplicidade. sum(min_count=1)
    # devolve NA (não 0) quando todas as seções do local não têm qt_aptos, para
    # o fillna por locais.qt_eleitores funcionar certo em seguida.
    por_secao = base.drop_duplicates("id_secao").groupby("id_local").agg(
        qt_comparecimento=("qt_comparecimento", "sum"),
        qt_aptos=("qt_aptos", lambda s: s.sum(min_count=1)),
    ).reset_index()

    aptos_fallback = locais[[col_id_local, col_qt_eleitores]].rename(
        columns={col_id_local: "id_local", col_qt_eleitores: "qt_aptos_fallback"}
    )

    saida = votos_largo.merge(por_secao, on="id_local", how="left").merge(
        aptos_fallback, on="id_local", how="left"
    )
    saida["qt_aptos"] = saida["qt_aptos"].fillna(saida["qt_aptos_fallback"])
    return saida.drop(columns="qt_aptos_fallback")


def agrega_por_zona_mun(
    resultados_secao_com_id: pd.DataFrame,
    locais: pd.DataFrame,
    *,
    col_id_local: str = "id_local",
    col_cd_mun: str = "cd_mun",
    col_qt_eleitores: str = "qt_eleitores",
) -> pd.DataFrame:
    """Agrega direto da seção, por `id_zona_mun` (inclui os locais especiais).

    Esta é a agregação "observada": deve bater linha a linha com
    `votacao_candidato_munzona` do TSE (ver PLAN.md > Verificação). Diferente
    de `agrega_por_local`, aqui não se filtra `flag_especial`.

    `qt_aptos` vem de `locais.qt_eleitores` somado por `id_zona_mun` (mesma
    lógica de `agrega_por_local` — ver o módulo).

    Args:
        resultados_secao_com_id: saída de `secao_para_id_local`. Já traz
            `nr_zona` (a zona da própria seção) — só falta o `cd_mun` (código
            IBGE), que vem de `locais` via `id_local`.
        locais: tabela de locais (etapa 01), com `id_local`, `cd_mun`,
            `qt_eleitores`.
    """
    base = resultados_secao_com_id.dropna(subset=["id_local"]).merge(
        locais[[col_id_local, col_cd_mun]], on=col_id_local, how="left"
    )
    # cd_mun nulo = exterior (locais sem crosswalk, ver 00_insumos.qmd).
    # `.astype(str)` numa coluna de texto nula do pandas devolve NaN de
    # verdade (não a string "nan"), e a concatenação propaga isso — sem o
    # fillna, pivot_table() descarta esses grupos por padrão (dropna=True) e
    # o exterior some do total, não só do mapa. Achado rodando de verdade:
    # ~294 mil votos perdidos silenciosamente em 2022.
    cd_mun_ou_exterior = base[col_cd_mun].fillna(SENTINELA_EXTERIOR)
    base["id_zona_mun"] = (
        cd_mun_ou_exterior.astype(str) + base["nr_zona"].astype(int).astype(str).str.zfill(4)
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

    # qt_aptos: prefere o valor real por seção (de detalhe_votacao_secao, via
    # fontes.py), somado uma vez por seção. Onde faltar (NA em todas as
    # seções da zona), cai no fallback de locais.qt_eleitores — mesma lógica
    # de agrega_por_local, ver lá.
    por_secao = base.drop_duplicates("id_secao").groupby("id_zona_mun").agg(
        qt_comparecimento=("qt_comparecimento", "sum"),
        qt_aptos=("qt_aptos", lambda s: s.sum(min_count=1)),
    ).reset_index()

    # id_zona_mun de cada local: mesma conta de `base`, mas a partir de
    # `locais` (nr_zona do próprio local, não da seção — coincide, mas evita
    # depender do merge de resultados para montar a chave).
    aptos_por_local = locais[[col_id_local, col_cd_mun, "nr_zona", col_qt_eleitores]].copy()
    aptos_por_local["id_zona_mun"] = (
        aptos_por_local[col_cd_mun].fillna(SENTINELA_EXTERIOR).astype(str)
        + aptos_por_local["nr_zona"].astype(int).astype(str).str.zfill(4)
    )
    aptos_fallback = (
        aptos_por_local.groupby("id_zona_mun")[col_qt_eleitores]
        .sum()
        .rename("qt_aptos_fallback")
        .reset_index()
    )

    saida = votos_largo.merge(por_secao, on="id_zona_mun", how="left").merge(
        aptos_fallback, on="id_zona_mun", how="left"
    )
    saida["qt_aptos"] = saida["qt_aptos"].fillna(saida["qt_aptos_fallback"])
    return saida.drop(columns="qt_aptos_fallback")


def agrega_zona_mun_por_municipio(res_zona_mun: pd.DataFrame) -> pd.DataFrame:
    """Soma `res_..._zona_mun` por `cd_mun` (os 7 primeiros dígitos de
    `id_zona_mun`) — a camada **observada** (bate exato com o TSE), usada
    tanto para o arquivo `_municipio` quanto para o mapa nacional.

    O exterior (`id_zona_mun` prefixado por `SENTINELA_EXTERIOR`) vira uma
    linha `cd_mun = SENTINELA_EXTERIOR`, mantida para conservar o total — não
    truncar para 7 caracteres, senão vira `"EXTERIO"` e o total do exterior
    se espalha por várias linhas falsas (uma por combinação truncada).
    """
    df = res_zona_mun.copy()
    eh_exterior = df["id_zona_mun"].str.startswith(SENTINELA_EXTERIOR)
    df["cd_mun"] = np.where(eh_exterior, SENTINELA_EXTERIOR, df["id_zona_mun"].str[:7])

    colunas_qt = [c for c in df.columns if c.startswith("qt_")]
    return df.groupby("cd_mun")[colunas_qt].sum().reset_index()


def agrega_secao_por_municipio(
    resultados_secao: pd.DataFrame, crosswalk: pd.DataFrame
) -> pd.DataFrame:
    """Agrega direto da seção por `cd_mun`, via crosswalk `cd_mun_tse` -> `cd_mun`.

    Serve para anos sem `id_local`/locais processados (ex.: 2018) — não
    depende de `secao_local`, só do crosswalk de município (etapa 00).
    Município do TSE sem crosswalk (o exterior) vira `SENTINELA_EXTERIOR`,
    mesma convenção de `agrega_por_zona_mun`/`agrega_zona_mun_por_municipio`.

    `qt_aptos` fica NA quando `resultados_secao` não tiver vindo de
    `fontes.ler_votacao_secao(..., caminho_detalhe=...)` — sem
    `locais.qt_eleitores` (que depende da etapa 01, não rodada para este
    ano), não há fallback. `qt_comparecimento` não depende disso (soma de
    `qt_votos`, ver fontes.py).

    Args:
        resultados_secao: esquema longo de `fontes.py`, com `cd_mun_tse`.
        crosswalk: `crosswalk_mun_tse_ibge.parquet` (etapa 00), com
            `cd_mun_tse` (5 dígitos) e `cd_mun` (IBGE).

    Returns:
        DataFrame largo por `cd_mun`: `qt_votos_{nr_votavel}`, `qt_aptos`,
        `qt_comparecimento`.
    """
    base = resultados_secao.merge(
        crosswalk[["cd_mun_tse", "cd_mun"]], on="cd_mun_tse", how="left"
    )
    base["cd_mun"] = base["cd_mun"].fillna(SENTINELA_EXTERIOR)
    base["id_secao"] = (
        base["cd_mun_tse"].astype(str)
        + base["nr_zona"].astype(int).astype(str).str.zfill(4)
        + base["nr_secao"].astype(int).astype(str).str.zfill(4)
    )

    votos_largo = (
        base.pivot_table(
            index="cd_mun",
            columns="nr_votavel",
            values="qt_votos",
            aggfunc="sum",
            fill_value=0,
        )
        .rename(columns=lambda nr: f"qt_votos_{int(nr)}")
        .reset_index()
    )

    por_secao = base.drop_duplicates("id_secao").groupby("cd_mun").agg(
        qt_comparecimento=("qt_comparecimento", "sum"),
        qt_aptos=("qt_aptos", lambda s: s.sum(min_count=1)),
    ).reset_index()

    return votos_largo.merge(por_secao, on="cd_mun", how="left")


def alinha_zona_mun(res_antes: pd.DataFrame, res_depois: pd.DataFrame) -> pd.DataFrame:
    """Troca o `id_zona_mun` de `res_antes` pelo de `res_depois` nos municípios
    com uma só zona nos dois anos.

    Nesses municípios a unidade é o próprio município, mas o número da zona
    pode mudar de uma eleição para outra (36 casos entre 2018 e 2022), e o id
    deixaria de casar. Nos municípios com várias zonas o id é mantido: entre
    2018 e 2022 o conjunto de zonas de cada um é idêntico (medido em
    29/09/2026, ver PLAN.md).

    Returns:
        Cópia de `res_antes` com `id_zona_mun` alinhado a `res_depois`.
    """
    def zona_unica(df: pd.DataFrame) -> pd.Series:
        base = df.loc[~df["id_zona_mun"].str.startswith(SENTINELA_EXTERIOR), "id_zona_mun"]
        por_mun = base.groupby(base.str[:7])
        return por_mun.first()[por_mun.size() == 1]

    unica_antes = zona_unica(res_antes)
    unica_depois = zona_unica(res_depois)
    comuns = unica_antes.index.intersection(unica_depois.index)
    troca = dict(zip(unica_antes.loc[comuns], unica_depois.loc[comuns]))

    saida = res_antes.copy()
    saida["id_zona_mun"] = saida["id_zona_mun"].replace(troca)
    return saida


def mudanca_votacao(
    res_antes: pd.DataFrame,
    res_depois: pd.DataFrame,
    *,
    ano_antes: int,
    ano_depois: int,
    nr_pt: int,
    chave: str = "cd_mun",
    nr_bolsonaro_antes: int | None = None,
    nr_bolsonaro_depois: int | None = None,
) -> pd.DataFrame:
    """Compara dois resultados (já com `calcula_indicadores`) pela coluna `chave`.

    Pensado para o 2º turno, com só dois candidatos — o Δ de Lula/Haddad é o espelho
    exato do Δ de Bolsonaro nesse caso. `nr_vencedor` (já calculado por
    `calcula_indicadores`) é o que decide a virada, então o número do
    Bolsonaro (que muda de partido a cada eleição) não entra nessa parte.

    Duas bases de comparação:
      - **votos válidos** (`var_pc_validos_pt`): a variação de Lula/Haddad. Só é
        o swing de Butler com dois candidatos; no 1º turno os dois podem subir
        juntos. Não vê mudança no tamanho do bolo (abstenção, brancos e nulos).
      - **eleitorado apto** (só se `nr_bolsonaro_*` forem passados): Lula/Haddad,
        Bolsonaro e não-voto somam 100% dos aptos, então as três variações
        somam zero e mostram de onde veio o movimento.

    Com `nr_bolsonaro_*` vêm também `cresceu_mais_validos` (`pt`, `bolsonaro`,
    `terceiro` se os dois caíram, ou `empate`) e `var_pc_validos_maior` (a
    variação de quem cresceu mais) — é o que pinta o mapa de mudança.

    Com `chave="id_zona_mun"`, passar `res_antes` por `alinha_zona_mun`
    antes. O exterior (`SENTINELA_EXTERIOR`) é descartado: não é território,
    e é o mesmo tratamento dado aos mapas.

    Returns:
        Uma linha por `chave` presente nos dois anos, com
        `pc_validos_pt_{ano_antes}`, `pc_validos_pt_{ano_depois}`,
        `var_pc_validos_pt` (fração, depois - antes), `nr_vencedor_{ano_antes}`,
        `nr_vencedor_{ano_depois}`, `mudou_vencedor` (bool) e `sentido_virada`.
    """
    col_pt = f"pc_validos_{nr_pt}"

    def prepara(df: pd.DataFrame, ano: int) -> pd.DataFrame:
        base = df[~df[chave].str.startswith(SENTINELA_EXTERIOR)]
        return base[[chave, col_pt, "nr_vencedor"]].rename(
            columns={col_pt: f"pc_validos_pt_{ano}", "nr_vencedor": f"nr_vencedor_{ano}"}
        )

    df = prepara(res_antes, ano_antes).merge(prepara(res_depois, ano_depois), on=chave, how="inner")
    df["var_pc_validos_pt"] = df[f"pc_validos_pt_{ano_depois}"] - df[f"pc_validos_pt_{ano_antes}"]

    # A virada é decidida por "Lula/Haddad venceu?" nos dois anos, nunca comparando
    # os números dos vencedores: o do Bolsonaro muda (17 -> 22), e a
    # comparação direta marcaria como virada todo lugar que ele venceu duas vezes.
    pt_antes = df[f"nr_vencedor_{ano_antes}"] == nr_pt
    pt_depois = df[f"nr_vencedor_{ano_depois}"] == nr_pt
    df["sentido_virada"] = np.select(
        [pt_antes & pt_depois, ~pt_antes & ~pt_depois, pt_depois],
        ["manteve_pt", "manteve_bolsonaro", "virou_pt"],
        default="virou_bolsonaro",
    )
    df["mudou_vencedor"] = pt_antes != pt_depois

    if nr_bolsonaro_antes is not None and nr_bolsonaro_depois is not None:
        df = _acrescenta_base_aptos(
            df, res_antes, res_depois, chave=chave, nr_pt=nr_pt,
            nr_bolsonaro={ano_antes: nr_bolsonaro_antes, ano_depois: nr_bolsonaro_depois},
            ano_antes=ano_antes, ano_depois=ano_depois,
        )
    return df


def _acrescenta_base_aptos(
    df: pd.DataFrame,
    res_antes: pd.DataFrame,
    res_depois: pd.DataFrame,
    *,
    chave: str,
    nr_pt: int,
    nr_bolsonaro: dict[int, int],
    ano_antes: int,
    ano_depois: int,
) -> pd.DataFrame:
    """Colunas da mudança sobre o eleitorado apto (ver `mudanca_votacao`).

    `qt_aptos == 0` vira NA (mesma regra de `calcula_indicadores`), e a linha
    fica sem `tipo_mudanca`.
    """
    def prepara(res: pd.DataFrame, ano: int) -> pd.DataFrame:
        base = res[~res[chave].str.startswith(SENTINELA_EXTERIOR)]
        aptos = pd.to_numeric(base["qt_aptos"], errors="coerce").replace(0, np.nan)
        cols_candidatos = [
            c for c in base.columns
            if c.startswith("qt_votos_") and c[len("qt_votos_"):].isdigit()
            and int(c[len("qt_votos_"):]) not in CODIGOS_NAO_CANDIDATO
        ]
        # 3º lugar: o mais votado fora Lula/Haddad e o bolsonarista, em cada ano
        # (não precisa ser a mesma pessoa nos dois). Sem outros candidatos (2º
        # turno) ou sem voto neles, fica NA.
        cols_outros = [
            c for c in cols_candidatos
            if c not in (f"qt_votos_{nr_pt}", f"qt_votos_{nr_bolsonaro[ano]}")
        ]
        if cols_outros:
            votos_outros = base[cols_outros].fillna(0)
            qt_terceiro = votos_outros.max(axis=1).where(lambda v: v > 0)
            nr_terceiro = pd.to_numeric(
                votos_outros.idxmax(axis=1).str[len("qt_votos_"):]
            ).where(qt_terceiro.notna())
        else:
            qt_terceiro = nr_terceiro = pd.Series(np.nan, index=base.index)
        return pd.DataFrame({
            chave: base[chave],
            f"qt_votos_pt_{ano}": base[f"qt_votos_{nr_pt}"],
            f"qt_votos_bolsonaro_{ano}": base[f"qt_votos_{nr_bolsonaro[ano]}"],
            f"qt_validos_{ano}": base[cols_candidatos].sum(axis=1),
            f"qt_aptos_{ano}": aptos,
            f"nr_terceiro_{ano}": nr_terceiro.astype("Int64"),
            f"qt_votos_terceiro_{ano}": qt_terceiro,
        })

    df = df.merge(prepara(res_antes, ano_antes), on=chave, how="left").merge(
        prepara(res_depois, ano_depois), on=chave, how="left"
    )
    for ano in (ano_antes, ano_depois):
        aptos = df[f"qt_aptos_{ano}"]
        df[f"pc_aptos_pt_{ano}"] = df[f"qt_votos_pt_{ano}"] / aptos
        df[f"pc_aptos_bolsonaro_{ano}"] = df[f"qt_votos_bolsonaro_{ano}"] / aptos
        df[f"pc_aptos_nao_voto_{ano}"] = (
            1 - df[f"pc_aptos_pt_{ano}"] - df[f"pc_aptos_bolsonaro_{ano}"]
        )

    for parte in ("pt", "bolsonaro", "nao_voto"):
        df[f"var_pc_aptos_{parte}"] = (
            df[f"pc_aptos_{parte}_{ano_depois}"] - df[f"pc_aptos_{parte}_{ano_antes}"]
        )
    df["var_rel_votos_pt"] = df[f"qt_votos_pt_{ano_depois}"] / df[f"qt_votos_pt_{ano_antes}"].replace(0, np.nan) - 1
    df["var_rel_votos_bolsonaro"] = (
        df[f"qt_votos_bolsonaro_{ano_depois}"] / df[f"qt_votos_bolsonaro_{ano_antes}"].replace(0, np.nan) - 1
    )
    df["var_rel_aptos"] = df[f"qt_aptos_{ano_depois}"] / df[f"qt_aptos_{ano_antes}"] - 1

    # Saldo (quem ganhou mais eleitorado) × mecanismo (um sobe e o outro cai,
    # os dois sobem — o não-voto encolheu —, ou os dois caem). Descreve o saldo
    # agregado da zona, não eleitores que mudaram de lado.
    d_pt, d_bo = df["var_pc_aptos_pt"], df["var_pc_aptos_bolsonaro"]
    saldo = np.where(d_pt - d_bo >= 0, "pt", "bolsonaro")
    mecanismo = np.select(
        [(d_pt > 0) & (d_bo < 0) | (d_pt < 0) & (d_bo > 0), (d_pt >= 0) & (d_bo >= 0)],
        ["troca", "ambos_cresceram"],
        default="ambos_cairam",
    )
    df["tipo_mudanca"] = np.where(d_pt.isna() | d_bo.isna(), None, pd.Series(saldo).str.cat(pd.Series(mecanismo), sep="_"))

    # Quem cresceu mais nos votos válidos. No 1º turno a variação de Lula/Haddad
    # não é o espelho da do bolsonarista: os dois podem subir (ou cair) juntos.
    # Se os dois caíram, quem cresceu foi o resto — entra o 3º lugar.
    for ano in (ano_antes, ano_depois):
        validos = df[f"qt_validos_{ano}"].replace(0, np.nan)
        df[f"pc_validos_bolsonaro_{ano}"] = df[f"qt_votos_bolsonaro_{ano}"] / validos
        df[f"pc_validos_terceiro_{ano}"] = df[f"qt_votos_terceiro_{ano}"] / validos
    for parte in ("bolsonaro", "terceiro"):
        df[f"var_pc_validos_{parte}"] = (
            df[f"pc_validos_{parte}_{ano_depois}"] - df[f"pc_validos_{parte}_{ano_antes}"]
        )
    v_pt, v_bo, v_3 = df["var_pc_validos_pt"], df["var_pc_validos_bolsonaro"], df["var_pc_validos_terceiro"]
    condicoes = [v_pt.isna() | v_bo.isna(), (v_pt < 0) & (v_bo < 0) & v_3.notna(), v_pt > v_bo, v_bo > v_pt]
    df["cresceu_mais_validos"] = np.select(condicoes, [None, "terceiro", "pt", "bolsonaro"], default="empate")
    df["var_pc_validos_maior"] = np.select(condicoes, [np.nan, v_3, v_pt, v_bo], default=v_pt)
    return df


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

    # qt_aptos = 0 acontece no fallback por locais.qt_eleitores: seções de voto
    # em trânsito têm QT_ELEITOR_SECAO = 0 no cadastro, mas recebem votos.
    # Sem o NA, o comparecimento vira infinito e a abstenção, -infinito.
    df["pc_comparecimento"] = df["qt_comparecimento"] / df["qt_aptos"].replace(0, np.nan)
    df["pc_abstencao"] = 1 - df["pc_comparecimento"]

    qt_validos = df[cols_votos_validos].sum(axis=1)
    df["pc_brancos"] = df.get(col_brancos, 0) / df["qt_comparecimento"]
    df["pc_nulos"] = df.get(col_nulos, 0) / df["qt_comparecimento"]
    col_anulados = f"qt_votos_{CODIGO_ANULADO_APURADO_SEPARADO}"
    df["pc_brancos_nulos"] = (
        df.get(col_brancos, 0) + df.get(col_nulos, 0) + df.get(col_anulados, 0)
    ) / df["qt_comparecimento"]

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
