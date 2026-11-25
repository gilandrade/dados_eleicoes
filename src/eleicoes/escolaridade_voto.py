"""Escolaridade, renda, urbanização e voto no PT por município.

Funções das etapas 11 a 13 (trabalho de Comportamento Eleitoral, FLP0484):

- leitura dos insumos por município: cadastro do eleitorado do TSE
  (`perfil_eleitorado_{ano}.zip`), renda dos microdados da amostra do Censo 2022
  (acesso controlado) e população, situação e área dos setores do Censo 2022
  (agregados por setor, tabela Básico);
- regressões com efeito fixo de UF (MQO com erro HC1, como nos textos da
  disciplina, sem peso), resíduos de Frisch-Waugh-Lovell e VIF.

Os dados são agregados por município: os coeficientes descrevem onde o PT vai
melhor, não como vota cada grupo de eleitores.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyogrio
import statsmodels.api as sm

from . import perfil
from .config import CODIGOS_NAO_CANDIDATO, NR_PT, PROCESSADO_DIR, RESULTADOS_DIR
from .fontes import _sql_read_csv

IDADE_MINIMA_VOTO = 16

# CD_SIT do Censo 2022 (dicionário `Dicionario_de_dados_malha_agregados.xlsx` do
# IBGE, conferido em 25/11/2026). Não existe o código 4 em 2022.
SITUACAO_SETOR = {
    1: "Área urbana de alta densidade de edificações de cidade ou vila",
    2: "Área urbana de baixa densidade de edificações de cidade ou vila",
    3: "Núcleo urbano",
    5: "Aglomerado rural - Povoado",
    6: "Aglomerado rural - Núcleo rural",
    7: "Aglomerado rural - Lugarejo",
    8: "Área rural (exclusive aglomerados)",
    9: "Massas de água",
}
# "Cidades e vilas" (Soares e Terron, 2008): só os setores urbanos de cidade ou vila.
SIT_CIDADES_E_VILAS = (1, 2)
# Situação "Urbana" oficial do IBGE (inclui o núcleo urbano, fora da cidade ou vila).
SIT_URBANA_IBGE = (1, 2, 3)
SIT_MASSA_DAGUA = 9


# ==== Insumos ================================================================


def _extrai(z: zipfile.ZipFile, nome: str, tmp_dir: Path) -> Path:
    """Extrai um membro do zip para `tmp_dir` (sem subpastas) e devolve o caminho."""
    destino = tmp_dir / Path(nome).name
    with z.open(nome) as origem, open(destino, "wb") as saida:
        shutil.copyfileobj(origem, saida, length=1 << 24)
    return destino


def ler_perfil_eleitorado(caminho_zip: Path, tmp_dir: Path) -> pd.DataFrame:
    """Lê `perfil_eleitorado_{ano}.zip` e soma por município × gênero × idade × escolaridade.

    O CSV é extraído para `tmp_dir`, lido no DuckDB e apagado. O formato muda entre
    os anos (conferido em 25/11/2026): em 2018 há um CSV só e a contagem é
    `QT_ELEITORES_PERFIL`; em 2026 há um CSV por UF mais `_BRASIL.csv` (o lido aqui)
    e a contagem é `QT_ELEITORES`.

    Args:
        caminho_zip: zip do TSE.
        tmp_dir: pasta temporária para o CSV.

    Returns:
        `cd_mun_tse` (5 dígitos), `sg_uf`, `cd_genero`, `cd_faixa_etaria`,
        `cd_grau_escolaridade` e `qt_eleitores`.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(caminho_zip) as z:
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        brasil = [n for n in csvs if n.upper().endswith("_BRASIL.CSV")]
        if brasil:
            csvs = brasil
        if len(csvs) != 1:
            raise ValueError(f"{caminho_zip.name}: esperava um CSV (ou um _BRASIL.csv), achei {csvs}")
        caminho_csv = _extrai(z, csvs[0], tmp_dir)
    try:
        con = duckdb.connect()
        sql_csv = _sql_read_csv(caminho_csv)
        colunas = {c.lower() for c in con.sql(f"select * from {sql_csv} limit 0").columns}
        col_qt = "qt_eleitores_perfil" if "qt_eleitores_perfil" in colunas else "qt_eleitores"
        return con.sql(f"""
            select
                lpad(cd_municipio, 5, '0') as cd_mun_tse,
                sg_uf as sg_uf,
                cast(cd_genero as integer) as cd_genero,
                cast(cd_faixa_etaria as integer) as cd_faixa_etaria,
                cast(cd_grau_escolaridade as integer) as cd_grau_escolaridade,
                sum(cast({col_qt} as bigint)) as qt_eleitores
            from {sql_csv}
            group by all
        """).df()
    finally:
        caminho_csv.unlink(missing_ok=True)


def perfil_por_municipio(perfil_tse: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Contagens e indicadores do cadastro por município IBGE.

    Usa a mesma codificação de `perfil.py` (escolaridade 1–8, 0 = não informado;
    gênero 2/4; `CD_FAIXA_ETARIA` com os dois limites da faixa, ex.: 4549 = 45 a 49
    anos) e o mesmo `perfil.calcula_indicadores_perfil`.

    Args:
        perfil_tse: saída de `ler_perfil_eleitorado`.
        crosswalk: `crosswalk_mun_tse_ibge.parquet` (`cd_mun_tse` → `cd_mun`).

    Returns:
        Uma linha por `cd_mun` com `qt_eleitores_perfil`, as contagens `qt_...` e
        `pc_esc_*`, `pc_feminino`, `idade_media`. Os municípios do cadastro sem
        código IBGE (exterior e emancipados depois da malha de 2022) ficam fora.
    """
    base = perfil_tse.merge(crosswalk[["cd_mun_tse", "cd_mun"]], on="cd_mun_tse", how="inner")

    codigo = base["cd_faixa_etaria"]
    idade_valida = codigo > 0
    idade_min = (codigo // 100).where(idade_valida)
    idade_max = (codigo % 100).where(codigo % 100 > 0, idade_min)
    faixa = pd.cut(idade_min, bins=[*perfil.FAIXAS_IDADE.values(), np.inf], right=False,
                   labels=list(perfil.FAIXAS_IDADE))

    qt = base["qt_eleitores"].astype("int64")
    partes = {
        "qt_eleitores_perfil": qt,
        "qt_genero_feminino": qt.where(base["cd_genero"] == perfil.CD_GENERO_FEMININO, 0),
        "qt_genero_masculino": qt.where(base["cd_genero"] == perfil.CD_GENERO_MASCULINO, 0),
        "qt_idade_valida": qt.where(idade_valida, 0),
        "qt_idade_soma": (qt * (idade_min + idade_max) / 2).fillna(0),
    }
    for cd in perfil.CODIGOS_ESCOLARIDADE:
        partes[f"qt_esc_{cd}"] = qt.where(base["cd_grau_escolaridade"] == cd, 0)
    for nome in perfil.FAIXAS_IDADE:
        partes[f"qt_idade_{nome}"] = qt.where(faixa == nome, 0)

    largo = pd.DataFrame(partes).groupby(base["cd_mun"]).sum().reset_index()
    return perfil.calcula_indicadores_perfil(largo)


def renda_censo_por_municipio(caminho_zip: Path, tmp_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Renda domiciliar per capita média por município, dos microdados da amostra.

    Mesma população e mesmas variáveis de `scripts/12_celulas_censo_amostra.py` da
    branch `analise-perfil-voto`: pessoas de 16 anos ou mais, não estrangeiras
    (`P0520 != 3`), com instrução e renda válidas; peso `P0111`; renda `D0360`
    (domicílio, ligado por `Controle`); município `P0080` (7 dígitos).

    Cada UF é extraída para `tmp_dir` e apagada logo depois de lida: o CSV dos
    microdados controlados não fica em disco. Só saem médias e totais por município.

    Returns:
        `(renda, controle)`: `renda` com `cd_mun`, `pop16_pond`, `n_amostra`,
        `renda_pc_media`; `controle` com os totais por UF para conferência.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    rendas, controles = [], []
    with zipfile.ZipFile(caminho_zip) as z:
        ufs = sorted({n.split("/")[0] for n in z.namelist() if "/Pessoas_" in n})
        for uf in ufs:
            extraidos = []
            try:
                for tabela in ("Pessoas", "Domicilios"):
                    extraidos.append(_extrai(z, f"{uf}/{tabela}_{uf}_controlado.csv", tmp_dir))
                pessoas, domicilios = (str(p).replace("\\", "/") for p in extraidos)
                csv = "delim=';', header=true, all_varchar=true, encoding='latin-1'"
                con.sql(f"""
                    create or replace temp table base as
                    select
                        p.P0080 as cd_mun,
                        try_cast(p.P0181 as int) as idade,
                        p.P0520 as nacionalidade,
                        try_cast(p.P0760 as int) as instrucao,
                        try_cast(replace(d.D0360, ',', '.') as double) as renda_pc,
                        try_cast(replace(p.P0111, ',', '.') as double) as peso
                    from read_csv('{pessoas}', {csv}) p
                    join read_csv('{domicilios}', {csv}) d on p.P0100 = d.D0100
                """)
            finally:
                for arquivo in extraidos:
                    arquivo.unlink(missing_ok=True)
            elegivel = f"idade >= {IDADE_MINIMA_VOTO} and nacionalidade <> '3'"
            controles.append(con.sql(f"""
                select '{uf}' as cd_uf, sum(peso) as pop_total,
                       sum(case when {elegivel} then peso else 0 end) as pop_elegivel,
                       sum(case when {elegivel} and renda_pc is null then peso else 0 end) as pop_sem_renda,
                       count(*) as n_amostra
                from base
            """).df())
            rendas.append(con.sql(f"""
                select cd_mun, sum(peso) as pop16_pond, count(*) as n_amostra,
                       sum(peso * renda_pc) / sum(peso) as renda_pc_media
                from base
                where {elegivel} and instrucao between 1 and 7 and renda_pc is not null
                group by cd_mun
            """).df())
    return pd.concat(rendas, ignore_index=True), pd.concat(controles, ignore_index=True)


def censo_setor_por_municipio(caminho_zip: Path) -> pd.DataFrame:
    """População por situação do setor e área, por município (Censo 2022, Básico).

    O CSV do IBGE vem em Windows-1252 (o DuckDB o recusa como latin-1), com `;` e
    vírgula decimal; só quatro colunas são lidas, direto do zip.

    Returns:
        Uma linha por `cd_mun`, com `pop_total`, `pop_sit_{1..9}`, `area_km2` (todos
        os setores) e `area_km2_terra` (sem os setores de massa d'água, `CD_SIT` 9).
    """
    setores = pd.read_csv(
        caminho_zip, sep=";", encoding="cp1252", decimal=",",
        usecols=["CD_SETOR", "CD_MUN", "CD_SIT", "AREA_KM2", "v0001"],
        dtype={"CD_SETOR": str, "CD_MUN": str}, na_values=["."],
    )
    if setores["CD_SETOR"].duplicated().any():
        raise ValueError("CD_SETOR repetido na tabela Básico")
    por_sit = (
        setores.assign(pop=setores["v0001"].fillna(0))
        .groupby(["CD_MUN", "CD_SIT"], dropna=False)
        .agg(pop=("pop", "sum"), area_km2=("AREA_KM2", "sum"), n_setores=("CD_SETOR", "size"))
        .reset_index()
        .rename(columns={"CD_MUN": "cd_mun", "CD_SIT": "cd_sit"})
    )
    pop = por_sit.pivot_table(index="cd_mun", columns="cd_sit", values="pop", aggfunc="sum", fill_value=0)
    pop.columns = [f"pop_sit_{int(c)}" for c in pop.columns]
    for cd in SITUACAO_SETOR:
        if f"pop_sit_{cd}" not in pop.columns:
            pop[f"pop_sit_{cd}"] = 0
    pop = pop[[f"pop_sit_{cd}" for cd in SITUACAO_SETOR]]
    saida = pop.assign(pop_total=pop.sum(axis=1))
    saida["area_km2"] = por_sit.groupby("cd_mun")["area_km2"].sum()
    saida["area_km2_terra"] = por_sit.loc[por_sit["cd_sit"] != SIT_MASSA_DAGUA].groupby("cd_mun")["area_km2"].sum()
    saida["n_setores"] = por_sit.groupby("cd_mun")["n_setores"].sum()
    return saida.reset_index()


def taxa_urbanizacao(censo_mun: pd.DataFrame, situacoes: tuple[int, ...]) -> pd.Series:
    """Proporção (0–1) da população que mora em setores com `CD_SIT` em `situacoes`."""
    urbana = censo_mun[[f"pop_sit_{cd}" for cd in situacoes]].sum(axis=1)
    return urbana / censo_mun["pop_total"]


def monta_base(ano: int, turno: int = 2) -> pd.DataFrame:
    """Uma linha por município: voto no PT, cadastro, renda e Censo (saídas das etapas 04 e 11).

    `pc_pt`, `superior`, `urbanizacao` e `urbanizacao_ibge` em % (0–100); renda,
    densidade e população em log. A ordem é a de `municipios.gpkg` (por `cd_mun`), a
    mesma da matriz de vizinhança da etapa 10.
    """
    aux = PROCESSADO_DIR / "aux"
    municipios = (
        pyogrio.read_dataframe(aux / "municipios.gpkg", read_geometry=False)
        .rename(columns={"abbrev_state": "sg_uf", "name_region": "regiao", "name_muni": "nm_mun"})
        [["cd_mun", "nm_mun", "sg_uf", "regiao"]]
        .sort_values("cd_mun").reset_index(drop=True)
    )
    res = pd.read_parquet(RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_municipio.parquet")
    candidatos = [
        c for c in res.columns
        if c.startswith("qt_votos_") and int(c.removeprefix("qt_votos_")) not in CODIGOS_NAO_CANDIDATO
    ]
    # float: os votos vêm em int32 e `100 * soma` estoura.
    res = res.assign(qt_validos=res[candidatos].astype(float).sum(axis=1))
    res["pc_pt"] = 100 * res[f"qt_votos_{NR_PT}"].astype(float) / res["qt_validos"]

    perfil_mun = pd.read_parquet(aux / f"perfil_municipio_{ano}.parquet")
    renda = pd.read_parquet(aux / "renda_censo2022_municipio.parquet")
    censo = pd.read_parquet(aux / "censo2022_municipio.parquet")
    df = (
        municipios
        .merge(res[["cd_mun", "pc_pt", "qt_validos", "qt_aptos"]], on="cd_mun", how="left", validate="1:1")
        .merge(perfil_mun[["cd_mun", "qt_eleitores_perfil", "pc_esc_superior", "idade_media", "pc_feminino"]],
               on="cd_mun", how="left", validate="1:1")
        .merge(renda[["cd_mun", "renda_pc_media"]], on="cd_mun", how="left", validate="1:1")
        .merge(censo[["cd_mun", "pop_total", "urbanizacao", "urbanizacao_ibge", "densidade"]],
               on="cd_mun", how="left", validate="1:1")
    )
    df["superior"] = 100 * df["pc_esc_superior"]
    df["urbanizacao"] = 100 * df["urbanizacao"]
    df["urbanizacao_ibge"] = 100 * df["urbanizacao_ibge"]
    df["log_renda"] = np.log(df["renda_pc_media"])
    df["log_densidade"] = np.log(df["densidade"])
    df["log_pop"] = np.log(df["pop_total"])
    return df


# ==== Regressões =============================================================


def padroniza(df: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    """z-score (média 0, desvio-padrão amostral 1), sem peso, como na branch."""
    return (df[colunas] - df[colunas].mean()) / df[colunas].std()


def matriz(df: pd.DataFrame, preditores: list[str], *, ef_uf: bool, col_uf: str = "sg_uf") -> pd.DataFrame:
    """Constante + preditores padronizados (+ dummies de UF, a primeira de fora)."""
    partes = [padroniza(df, preditores)]
    if ef_uf:
        partes.append(pd.get_dummies(df[col_uf], prefix="uf", drop_first=True, dtype=float))
    return sm.add_constant(pd.concat(partes, axis=1))


def ajusta(
    df: pd.DataFrame,
    y: str,
    preditores: list[str],
    *,
    ef_uf: bool,
    peso: str | None = None,
    grupos: str | None = None,
) -> sm.regression.linear_model.RegressionResultsWrapper:
    """MQO (ou WLS, com `peso`) com preditores padronizados.

    Erro-padrão HC1; com `grupos`, agrupado por essa coluna (ex.: `sg_uf`).
    O coeficiente de cada preditor é a variação de `y` (p.p.) para +1 desvio-padrão.
    """
    X = matriz(df, preditores, ef_uf=ef_uf)
    modelo = sm.WLS(df[y], X, weights=df[peso]) if peso else sm.OLS(df[y], X)
    if grupos:
        return modelo.fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(df[grupos])[0]})
    return modelo.fit(cov_type="HC1")


def coeficientes(ajuste, preditores: list[str], **rotulos) -> pd.DataFrame:
    """Uma linha por preditor: coeficiente, erro-padrão, p-valor, N, R² e se há EF de UF."""
    return pd.DataFrame({
        **rotulos,
        "preditor": preditores,
        "coef": ajuste.params[preditores].to_numpy(),
        "ep": ajuste.bse[preditores].to_numpy(),
        "p_valor": ajuste.pvalues[preditores].to_numpy(),
        "n": int(ajuste.nobs),
        "r2": ajuste.rsquared,
        "ef_uf": any(str(p).startswith("uf_") for p in ajuste.params.index),
    })


def residualiza(df: pd.DataFrame, y: str, controles: list[str], *, ef_uf: bool) -> pd.Series:
    """Resíduo MQO de `df[y]` nos `controles` padronizados (+ dummies de UF).

    Frisch-Waugh-Lovell: a inclinação do resíduo do voto no resíduo da escolaridade
    padronizada é igual ao coeficiente da escolaridade no modelo completo.
    """
    X = matriz(df, controles, ef_uf=ef_uf)
    return sm.OLS(df[y], X).fit().resid


def numero(valor: float, casas: int = 2) -> str:
    """Número com vírgula decimal e ponto de milhar."""
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def estrelas(p: float) -> str:
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""


def tabela_regressao(coefs: pd.DataFrame, colunas: list[str], rotulos: dict[str, str]) -> pd.DataFrame:
    """Tabela no formato de artigo: coeficiente com estrelas e, embaixo, o erro-padrão.

    Args:
        coefs: formato longo, com `coluna` (o rótulo da coluna da tabela),
            `preditor`, `coef`, `ep`, `p_valor`, `n`, `r2` e `ef_uf`.
        colunas: ordem das colunas.
        rotulos: preditor → nome da linha, na ordem das linhas.

    Returns:
        DataFrame de texto: duas linhas por preditor, mais "EF de UF", "N" e "R²".
    """
    linhas = {}
    for preditor, rotulo in rotulos.items():
        sub = coefs.loc[coefs["preditor"] == preditor].set_index("coluna")
        linhas[rotulo] = {c: numero(sub.at[c, "coef"]) + estrelas(sub.at[c, "p_valor"]) if c in sub.index else ""
                          for c in colunas}
        linhas[f"{rotulo} (EP)"] = {c: f"({numero(sub.at[c, 'ep'])})" if c in sub.index else "" for c in colunas}
    resumo = coefs.groupby("coluna")[["ef_uf", "n", "r2"]].first()
    linhas["EF de UF"] = {c: "sim" if resumo.at[c, "ef_uf"] else "não" for c in colunas}
    linhas["N"] = {c: numero(resumo.at[c, "n"], 0) for c in colunas}
    linhas["R²"] = {c: numero(resumo.at[c, "r2"], 3) for c in colunas}
    tab = pd.DataFrame(linhas).T[colunas]
    tab.index = [i if not i.endswith(" (EP)") else "" for i in tab.index]
    return tab


def para_markdown(tab: pd.DataFrame, primeira_coluna: str = "") -> str:
    """Tabela de texto em markdown (sem depender do `tabulate`)."""
    cab = [primeira_coluna, *map(str, tab.columns)]
    linhas = ["| " + " | ".join(cab) + " |", "|" + "|".join(["---"] + [":-:"] * len(tab.columns)) + "|"]
    for indice, valores in zip(tab.index, tab.itertuples(index=False)):
        linhas.append("| " + " | ".join([str(indice), *map(str, valores)]) + " |")
    return "\n".join(linhas)


def vif(df: pd.DataFrame, preditores: list[str], *, ef_uf: bool) -> pd.Series:
    """VIF de cada preditor: 1 / (1 − R²) da regressão dele nos demais (+ UF)."""
    z = padroniza(df, preditores)
    saida = {}
    for p in preditores:
        outros = [c for c in preditores if c != p]
        partes = [z[outros]]
        if ef_uf:
            partes.append(pd.get_dummies(df["sg_uf"], prefix="uf", drop_first=True, dtype=float))
        r2 = sm.OLS(z[p], sm.add_constant(pd.concat(partes, axis=1))).fit().rsquared
        saida[p] = 1 / (1 - r2)
    return pd.Series(saida, name="vif")
