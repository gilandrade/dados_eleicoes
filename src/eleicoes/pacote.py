"""Pacote de dados para distribuição: eleições gerais por local de votação e por município.

Funções chamadas por `14_pacote_dados.qmd`. Os arquivos seguem o padrão de nome do
Download de Dados do CEM (`EL{ano}_{LV|MU}_BRA_{cargo}{turno}`, com os códigos de
cargo do CEM: PS, GO, SE, DF, DE): um GeoPackage e um `.xlsx` por ano, cargo e
turno, com os votos de cada candidato (ou partido, nos deputados) em colunas
separadas.

Também gera a versão 2 do `EL2022_MU_BRA_CEM` (base de municípios do CEM), com
população, domicílios, área e densidade do Censo 2022 no lugar do Censo 2010.
"""

from __future__ import annotations

import functools
import shutil
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio

from .config import (
    BRUTO_DIR,
    CARGOS,
    CARGOS_COM_2_TURNO,
    CARGOS_GERAIS,
    CODIGO_ANULADO_APURADO_SEPARADO,
    CODIGO_ANULADO_INAPTO,
    CODIGO_BRANCO,
    CODIGO_NULO,
    CODIGOS_NAO_CANDIDATO,
    CRS_SIRGAS2000,
    DADOS_DIR,
    GEOMETRIAS_DIR,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
    SETORES_2022_GPKG,
    UFS_27,
)
from .fontes import le_consulta_cand, votos_nao_validos
from .resultados import SENTINELA_EXTERIOR
from .visualizador import nomes_candidatos

# ==== Constantes ===============================================================

PACOTE_DIR = DADOS_DIR / "04_pacote"
PACOTE_CEM_DIR = PACOTE_DIR / "cem"

# Agregados por setor do Censo 2022, tabela Básico (IBGE, versão de 20/05/2026).
# V0001 = total de pessoas; V0007 = domicílios particulares ocupados (DPPO + DPIO),
# conferido no dicionário `dicionario_de_dados_agregados_por_setores_censitarios_20261113.xlsx`.
CENSO_BASICO_SETOR_ZIP = BRUTO_DIR / "aux" / "censo2022" / "Agregados_por_setores_basico_BR_20260520.zip"
CENSO_VERSAO = "Agregados por Setores Censitários, tabela Básico (versão de 20/05/2026)"
POP_CENSO_2022_BRASIL = 203_080_756

# Base de municípios do CEM (2022) a atualizar.
CEM_MU_2022_ZIP = BRUTO_DIR / "cem" / "EL2022_MU_BRA_CEM_0.zip"
CEM_MU_2022_NOME = "EL2022_MU_BRA_CEM"


def turnos(cargo: str) -> tuple[int, ...]:
    """(1, 2) para os cargos com 2º turno; (1,) para senador e deputados."""
    return (1, 2) if cargo in CARGOS_COM_2_TURNO else (1,)


def nome_arquivo(ano: int, cargo: str, turno: int, unidade: str) -> str:
    """`EL2022_LV_BRA_PS1`, `EL2022_MU_BRA_GO2`, `EL2022_MU_BRA_SE` etc.

    `unidade` = `LV` ou `MU`. O turno só entra no nome dos cargos com 2º turno.
    """
    sufixo = str(turno) if cargo in CARGOS_COM_2_TURNO else ""
    return f"EL{ano}_{unidade}_BRA_{CARGOS[cargo]['sigla']}{sufixo}"


def le_resultado(cargo: str, ano: int, turno: int, nivel: str) -> pd.DataFrame:
    """`res_{cargo}_{ano}_t{turno}_{nivel}.parquet` da etapa 04."""
    caminho = RESULTADOS_DIR / f"res_{cargo}_{ano}_t{turno}_{nivel}.parquet"
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho.name} não existe: rode 04_resultados_turno.qmd -P ano:{ano} -P turno:{turno} -P cargo:{cargo}"
        )
    return pd.read_parquet(caminho)


def sem_acento(texto: pd.Series) -> pd.Series:
    """Maiúsculas sem acento, como o `MUN_NOME` do CEM (`SAO PAULO`)."""
    return texto.map(
        lambda s: None if s is None or (isinstance(s, float) and np.isnan(s))
        else "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).upper()
    )


# ==== Colunas de resultado =====================================================


def _nrs_candidatos(res: pd.DataFrame) -> list[int]:
    return sorted(
        int(c.removeprefix("qt_votos_"))
        for c in res.columns
        if c.startswith("qt_votos_") and int(c.removeprefix("qt_votos_")) not in CODIGOS_NAO_CANDIDATO
    )


def _inteiro(res: pd.DataFrame, coluna: str) -> pd.Series:
    """Coluna como int64 (0 se ausente). Os votos vêm em int32: somar ou
    multiplicar por 100 estoura (CLAUDE.md)."""
    if coluna not in res.columns:
        return pd.Series(0, index=res.index, dtype="int64")
    return pd.to_numeric(res[coluna], errors="coerce").fillna(0).round().astype("int64")


def _pc(num: pd.Series, den: pd.Series) -> pd.Series:
    """Percentual de 0 a 100; sem denominador, nulo."""
    return (100 * num / den.where(den > 0)).round(4)


def colunas_resultado(res: pd.DataFrame, ano: int, turno: int) -> pd.DataFrame:
    """Colunas de resultado do pacote a partir de um `res_{cargo}_*` (formato largo).

    Mantém o índice de `res`. Percentuais de 0 a 100.
    """
    nrs = _nrs_candidatos(res)
    votos = {nr: _inteiro(res, f"qt_votos_{nr}") for nr in nrs}
    aptos = _inteiro(res, "qt_aptos")
    comparecimento = _inteiro(res, "qt_comparecimento")
    brancos = _inteiro(res, f"qt_votos_{CODIGO_BRANCO}")
    nulos = _inteiro(res, f"qt_votos_{CODIGO_NULO}")
    anulados = _inteiro(res, f"qt_votos_{CODIGO_ANULADO_APURADO_SEPARADO}")
    inaptos = _inteiro(res, f"qt_votos_{CODIGO_ANULADO_INAPTO}")
    validos = sum(votos.values()) if votos else pd.Series(0, index=res.index, dtype="int64")

    out = pd.DataFrame(index=res.index)
    out["ano"] = ano
    out["turno"] = turno
    out["qt_aptos"] = aptos
    out["qt_comparecimento"] = comparecimento
    out["qt_abstencoes"] = aptos - comparecimento
    out["qt_validos"] = validos
    out["qt_brancos"] = brancos
    out["qt_nulos"] = nulos
    out["qt_anulados"] = inaptos
    out["qt_anulados_apu_sep"] = anulados
    out["qt_brancos_nulos"] = brancos + nulos
    out["pc_comparecimento"] = _pc(comparecimento, aptos)
    out["pc_abstencao"] = _pc(aptos - comparecimento, aptos)
    out["pc_brancos"] = _pc(brancos, comparecimento)
    out["pc_nulos"] = _pc(nulos, comparecimento)
    out["pc_brancos_nulos"] = _pc(brancos + nulos, comparecimento)
    for nr in nrs:
        out[f"votos_{nr}"] = votos[nr]
    for nr in nrs:
        out[f"pc_validos_{nr}"] = _pc(votos[nr], validos)

    # Mais votado e margem (1º − 2º, em p.p. dos válidos); sem válidos, nulo.
    matriz = np.column_stack([votos[nr].to_numpy() for nr in nrs])
    ordem = np.argsort(-matriz, axis=1, kind="stable")
    primeiro = matriz[np.arange(len(matriz)), ordem[:, 0]]
    segundo = matriz[np.arange(len(matriz)), ordem[:, 1]] if len(nrs) > 1 else 0
    tem_validos = validos.to_numpy() > 0
    out["nr_mais_votado"] = pd.Series(np.array(nrs)[ordem[:, 0]], index=res.index, dtype="Int64").where(
        tem_validos, pd.NA
    )
    out["margem_validos"] = _pc(pd.Series(primeiro - segundo, index=res.index), validos)
    return out


# ==== Candidatos ===============================================================


def _consulta_cand(ano: int) -> pd.DataFrame:
    """Candidatos de todos os cargos do `consulta_cand_{ano}.zip` (eleição ordinária)."""
    return le_consulta_cand(ano)


def _codigos_cargo(cargo: str) -> tuple[int, ...]:
    cd = CARGOS[cargo]["cd_cargo"]
    return (cd,) if isinstance(cd, int) else tuple(cd)


def _escolhe_registro(opcoes: pd.DataFrame, nome_votacao: str) -> pd.Series | None:
    """Um registro do `consulta_cand` para o número. Quando há mais de um
    (candidatura trocada, como Lula → Haddad em 2018), vale o nome que aparece no
    `votacao_secao`; depois, a candidatura apta."""
    escolha = opcoes[opcoes["nm_candidato"].str.upper() == (nome_votacao or "").upper()]
    if escolha.empty:
        escolha = opcoes[opcoes["ds_situacao_candidatura"].str.upper() == "APTO"]
    if escolha.empty:
        escolha = opcoes
    return None if escolha.empty else escolha.iloc[0]


def candidatos_anulados(ano: int, turno: int, cargo: str) -> pd.DataFrame:
    """Candidatos com votos anulados na totalização (ver `config.CODIGO_ANULADO_INAPTO`):
    UF, número, nome no `votacao_secao`, votos e destino (anulado ou sub judice).

    Os votos deles estão em `qt_anulados`/`qt_anulados_apu_sep` dos arquivos, não
    numa coluna `votos_{nr}`; a tabela `candidatos` os lista para não perder quem
    eram (ex.: Daniel Silveira, Senado/RJ 2022).
    """
    import duckdb

    codigos = ", ".join(str(c) for c in _codigos_cargo(cargo))
    nao_candidato = ", ".join(str(c) for c in CODIGOS_NAO_CANDIDATO)
    lista = ", ".join(
        "'" + (BRUTO_DIR / str(ano) / f"votacao_secao_{ano}_{uf}.parquet").as_posix() + "'" for uf in UFS_27
    )
    votos = duckdb.sql(f"""
        select sg_uf as sg_uf, cast(nr_votavel as integer) as nr_candidato, upper(nm_votavel) as nm_votavel,
            sum(cast(qt_votos as bigint)) as qt_votos
        from read_parquet([{lista}])
        where cast(cd_cargo as integer) in ({codigos}) and cast(nr_turno as integer) = {turno}
            and cast(cd_tipo_eleicao as integer) = 2 and cast(nr_votavel as integer) not in ({nao_candidato})
        group by all
    """).df()
    destino = votos_nao_validos(ano, turno, CARGOS[cargo]["cd_cargo"])
    m = votos.merge(
        destino, left_on=["sg_uf", "nr_candidato", "nm_votavel"], right_on=["i_sg_uf", "i_nr", "i_nm"], how="left"
    )
    anulado = m["i_nr"].isna() | m["i_cod"].notna()
    m = m[anulado].copy()
    m["destino"] = np.where(
        m["i_cod"].astype("Float64").fillna(CODIGO_ANULADO_INAPTO) == CODIGO_ANULADO_APURADO_SEPARADO,
        "anulado sub judice", "anulado",
    )
    return m[["sg_uf", "nr_candidato", "nm_votavel", "qt_votos", "destino"]].reset_index(drop=True)


def candidatos(ano: int, turno: int, cargo: str, res: pd.DataFrame) -> pd.DataFrame:
    """Tabela `candidatos` do arquivo: número, nome, nome de urna, partido e votos.

    - Presidente: um número por candidato, votos no Brasil (`res` é o resultado
      por município, com a linha do exterior).
    - Governador e Senador: uma linha por UF e número (o número muda de dono de
      UF para UF), votos e % dos válidos na UF. Inclui os candidatos com votos
      anulados (`destino`), sem percentual: os votos deles estão em `qt_anulados`.
    - Deputados: uma linha por partido (ver `partidos`).
    """
    if CARGOS[cargo]["por_partido"]:
        return partidos(ano, cargo, res)
    cand = _consulta_cand(ano)
    cand = cand[cand["cd_cargo"].isin(_codigos_cargo(cargo)) & (cand["nr_turno"] == turno)]

    if cargo == "presidente":
        nomes_votacao = {int(nr): nome for nr, nome in nomes_candidatos(ano, turno).items()}
        base = pd.DataFrame({"nr_candidato": _nrs_candidatos(res)})
        base["nm_votavel"] = base["nr_candidato"].map(nomes_votacao)
        votos = pd.Series({nr: _inteiro(res, f"qt_votos_{nr}").sum() for nr in base["nr_candidato"]})
        base["qt_votos"] = base["nr_candidato"].map(votos)
        base["sg_uf"] = None
    else:
        # Nome e votos por UF, do arquivo que a 04 grava junto do resultado.
        nomes = pd.read_parquet(RESULTADOS_DIR / f"candidatos_{cargo}_{ano}_t{turno}.parquet")
        base = (
            nomes.groupby(["sg_uf", "nr_votavel", "nm_votavel"], as_index=False)["qt_votos"].sum()
            .rename(columns={"nr_votavel": "nr_candidato"})
        )
        if base.duplicated(["sg_uf", "nr_candidato"]).any():
            raise ValueError(f"mais de um nome para o mesmo número na UF ({cargo}, {ano}, {turno})")
        base["destino"] = "válido"
        base = pd.concat([base, candidatos_anulados(ano, turno, cargo)], ignore_index=True)

    linhas = []
    for linha in base.itertuples(index=False):
        opcoes = cand[cand["nr_candidato"] == int(linha.nr_candidato)]
        if linha.sg_uf is not None:
            opcoes = opcoes[opcoes["sg_uf"] == linha.sg_uf]
        registro = _escolhe_registro(opcoes, linha.nm_votavel)
        linhas.append({
            **({} if linha.sg_uf is None else {"sg_uf": linha.sg_uf}),
            "nr_candidato": int(linha.nr_candidato),
            "nm_candidato": linha.nm_votavel if registro is None else registro["nm_candidato"],
            "nm_urna": None if registro is None else registro["nm_urna_candidato"],
            "sg_partido": None if registro is None else registro["sg_partido"],
            "nm_partido": None if registro is None else registro["nm_partido"],
            "qt_votos": int(linha.qt_votos),
            **({} if linha.sg_uf is None else {"destino": linha.destino}),
        })
    tabela = pd.DataFrame(linhas)
    tabela["qt_votos"] = tabela["qt_votos"].astype("int64")
    if "sg_uf" in tabela:
        validos = tabela["qt_votos"].where(tabela["destino"] == "válido")
        total = validos.groupby(tabela["sg_uf"]).transform("sum")
        tabela["pc_validos"] = (100 * validos / total).round(4)
        tabela = tabela.sort_values(["sg_uf", "destino", "qt_votos"], ascending=[True, False, False])
    else:
        tabela["pc_validos"] = (100 * tabela["qt_votos"] / tabela["qt_votos"].sum()).round(4)
    return tabela.reset_index(drop=True)


def partidos(ano: int, cargo: str, res: pd.DataFrame) -> pd.DataFrame:
    """Tabela `candidatos` dos deputados: uma linha por partido, votos no Brasil.

    O número do partido são os 2 primeiros dígitos do número do candidato; sigla
    e nome vêm do `consulta_cand` do ano (o número muda de dono entre anos).
    """
    cand = _consulta_cand(ano)
    cand = cand[cand["cd_cargo"].isin(_codigos_cargo(cargo))].copy()
    cand["nr_partido"] = cand["nr_candidato"].astype(str).str[:2].astype(int)
    siglas = (
        cand.groupby(["nr_partido", "sg_partido", "nm_partido"]).size().rename("n").reset_index()
        .sort_values("n", ascending=False).drop_duplicates("nr_partido").set_index("nr_partido")
    )
    nrs = _nrs_candidatos(res)
    tabela = pd.DataFrame({"nr_partido": nrs})
    tabela["sg_partido"] = tabela["nr_partido"].map(siglas["sg_partido"])
    tabela["nm_partido"] = tabela["nr_partido"].map(siglas["nm_partido"])
    tabela["qt_votos"] = [int(_inteiro(res, f"qt_votos_{nr}").sum()) for nr in nrs]
    tabela["qt_votos"] = tabela["qt_votos"].astype("int64")
    tabela["pc_validos"] = (100 * tabela["qt_votos"] / tabela["qt_votos"].sum()).round(4)
    return tabela


# ==== Contagens e atributos ====================================================


def _municipios_ibge(ano: int | None = None) -> pd.DataFrame:
    mun = pd.DataFrame(
        pyogrio.read_dataframe(
            malha_municipios(ano),
            columns=["cd_mun", "name_muni", "abbrev_state", "name_state", "name_region"],
            read_geometry=False,
        )
    )
    return mun.rename(columns={
        "name_muni": "nm_mun", "abbrev_state": "sg_uf", "name_state": "nm_uf", "name_region": "nm_regiao",
    })


def contagens_secao(ano: int, turno: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`n_secoes` por local e (`n_zonas`, `n_locais`, `n_secoes`) por município.

    Seções do cadastro do TSE (`secao_local_{ano}`), no turno; inclui as agregadas.
    O exterior aparece como `cd_mun = EXTERIOR`.
    """
    secoes = pd.read_parquet(PROCESSADO_DIR / f"secao_local_{ano}.parquet")
    secoes = secoes[secoes["nr_turno"].astype(int) == turno]
    por_local = secoes.groupby("id_local").size().rename("n_secoes").reset_index()

    locais = pd.read_parquet(PROCESSADO_DIR / f"locais_{ano}.parquet", columns=["id_local", "cd_mun", "nr_zona"])
    base = por_local.merge(locais, on="id_local", how="left")
    base["cd_mun"] = _chave_municipio(base["cd_mun"], base["id_local"].str[:5])
    por_mun = base.groupby("cd_mun").agg(
        n_zonas=("nr_zona", "nunique"), n_locais=("id_local", "nunique"), n_secoes=("n_secoes", "sum")
    )
    return por_local, por_mun.reset_index()


def censo_municipio(ano: int | None = None) -> pd.DataFrame:
    """População (V0001), domicílios particulares ocupados (V0007) e área por município.

    Soma dos setores do Censo 2022 (tabela Básico). `hab_km2` = população / área.
    Com a malha de 2024 (`ano` >= ANO_MALHA_2024), os setores dos municípios que
    perderam território para um município novo vão para o município da malha de
    2024 que contém o ponto interno do setor.
    """
    if ano is not None and ano >= ANO_MALHA_2024:
        return _censo_municipio_2024().copy()
    return _censo_municipio().copy()


@functools.lru_cache(maxsize=1)
def _censo_municipio_2024() -> pd.DataFrame:
    origens = sorted({o for info in MUNICIPIOS_SEM_IBGE.values() for o in info["desmembrado_de"]})
    lista = ", ".join(f"'{c}'" for c in origens)
    setores = gpd.read_file(SETORES_2022_GPKG, where=f"CD_MUN IN ({lista})", columns=["CD_SETOR", "CD_MUN"])
    malha = gpd.read_file(malha_municipios(ANO_MALHA_2024), columns=["cd_mun"]).to_crs(setores.crs)
    pontos = setores.assign(geometry=setores.representative_point())
    destino = gpd.sjoin(pontos, malha, how="left", predicate="within")
    destino = destino.drop_duplicates("CD_SETOR").set_index("CD_SETOR")["cd_mun"]
    if destino.isna().any():
        raise ValueError(f"Setores fora da malha de 2024: {list(destino[destino.isna()].index)}")
    setores = _setores_censo().copy()
    movidos = destino[destino != destino.index.str[:7]]
    novo = setores["CD_SETOR"].map(movidos)
    setores["CD_MUN"] = novo.fillna(setores["CD_MUN"])
    return _agrega_censo(setores)


@functools.lru_cache(maxsize=1)
def _setores_censo() -> pd.DataFrame:
    # O duckdb recusa o arquivo como latin-1 (bytes de controle); pandas lê só as
    # colunas usadas, direto do zip.
    setores = pd.read_csv(
        CENSO_BASICO_SETOR_ZIP, sep=";", encoding="latin-1", dtype=str,
        usecols=["CD_SETOR", "CD_MUN", "AREA_KM2", "v0001", "v0007"],
    )
    setores["pop"] = pd.to_numeric(setores["v0001"], errors="coerce")
    setores["dom"] = pd.to_numeric(setores["v0007"], errors="coerce")
    setores["area"] = pd.to_numeric(setores["AREA_KM2"].str.replace(",", "."), errors="coerce")
    return setores


@functools.lru_cache(maxsize=1)
def _censo_municipio() -> pd.DataFrame:
    return _agrega_censo(_setores_censo())


def _agrega_censo(setores: pd.DataFrame) -> pd.DataFrame:
    df = setores.groupby("CD_MUN").agg(
        pop_2022=("pop", "sum"), dom_2022=("dom", "sum"), area_km2=("area", "sum"),
        setores_sem_pop=("pop", lambda s: int(s.isna().sum())),
    ).reset_index().rename(columns={"CD_MUN": "cd_mun"})
    df["pop_2022"] = df["pop_2022"].astype("int64")
    df["dom_2022"] = df["dom_2022"].astype("int64")
    df["hab_km2"] = (df["pop_2022"] / df["area_km2"]).round(4)
    df["area_km2"] = df["area_km2"].round(3)
    return df


# ==== Montagem =================================================================

# Municípios que existem no TSE mas não na malha/crosswalk do IBGE de 2022 (criados
# depois). Sem esta tabela, seus locais e votos caem no exterior (CLAUDE.md).
# A partir de ANO_MALHA_2024 o pacote usa a malha municipal de 2024, que já os tem
# (`cd_mun_2024`); os setores do Censo 2022 dos municípios de origem
# (`desmembrado_de`) são realocados pelo ponto interno (`censo_municipio`).
MUNICIPIOS_SEM_IBGE = {
    "73709": {
        "nm_mun": "Boa Esperança do Norte", "sg_uf": "MT", "nm_uf": "Mato Grosso", "nm_regiao": "Centro-Oeste",
        "cd_mun_2024": "5101837", "desmembrado_de": ("5107925", "5106240"),  # Sorriso, Nova Ubiratã
    },
}
ANO_MALHA_2024 = 2026
MALHA_MUNICIPIOS = PROCESSADO_DIR / "aux" / "municipios.gpkg"
MALHA_MUNICIPIOS_2024 = PROCESSADO_DIR / "aux" / "municipios_2024.gpkg"  # 00_insumos.qmd


def malha_municipios(ano: int | None = None) -> Path:
    """Malha municipal do IBGE do pacote: 2022, ou 2024 a partir de ANO_MALHA_2024."""
    return MALHA_MUNICIPIOS_2024 if ano is not None and ano >= ANO_MALHA_2024 else MALHA_MUNICIPIOS


def _cd_mun_novo(ano: int) -> dict[str, str]:
    """Código TSE -> código IBGE dos MUNICIPIOS_SEM_IBGE que a malha do ano já tem."""
    if ano < ANO_MALHA_2024:
        return {}
    return {tse: info["cd_mun_2024"] for tse, info in MUNICIPIOS_SEM_IBGE.items()}


def _chave_municipio(cd_mun: pd.Series, cd_mun_tse: pd.Series) -> pd.Series:
    """`cd_mun`; sem ele, `TSE{código}` para os MUNICIPIOS_SEM_IBGE ou EXTERIOR."""
    sem_ibge = cd_mun_tse.where(cd_mun_tse.isin(list(MUNICIPIOS_SEM_IBGE)))
    return cd_mun.fillna(("TSE" + sem_ibge).fillna(SENTINELA_EXTERIOR))


def _separa_sem_ibge_do_exterior(res: pd.DataFrame, ano: int, turno: int, cargo: str) -> pd.DataFrame:
    """Tira da linha EXTERIOR do resultado por município os votos dos
    MUNICIPIOS_SEM_IBGE (somados dos locais) e cria uma linha para cada um.

    Acrescenta a coluna `chave` (`cd_mun`, `TSE{código}` ou EXTERIOR); essas linhas
    ficam com `cd_mun` nulo. Fora de Presidente não há exterior: a linha EXTERIOR,
    se só tinha os municípios sem IBGE, fica zerada e sai.
    """
    res = res.copy()
    qt = [c for c in res.columns if c.startswith("qt_")]
    res[qt] = res[qt].astype("float64")
    res["chave"] = res["cd_mun"]
    res_local = le_resultado(cargo, ano, turno, "local")
    cd_tse = res_local["id_local"].str[:5]
    novas = []
    for codigo in MUNICIPIOS_SEM_IBGE:
        grupo = res_local[cd_tse == codigo]
        if grupo.empty:
            continue
        soma = grupo.reindex(columns=qt).astype("float64").fillna(0).sum()
        exterior = res["cd_mun"] == SENTINELA_EXTERIOR
        res.loc[exterior, qt] = res.loc[exterior, qt].to_numpy() - soma.to_numpy()
        if (res.loc[exterior, qt] < 0).to_numpy().any():
            raise ValueError(f"Exterior ficou negativo ao separar {codigo} ({ano}, {turno})")
        novas.append({"cd_mun": None, "chave": f"TSE{codigo}", **soma.to_dict()})
    if novas:
        res = pd.concat([res, pd.DataFrame(novas)], ignore_index=True)
    vazio = (res["chave"] == SENTINELA_EXTERIOR) & (res[qt].fillna(0) == 0).all(axis=1)
    if cargo == "presidente" and vazio.any():
        raise ValueError(f"Linha do exterior zerada em Presidente ({ano}, {turno})")
    return res[~vazio].reset_index(drop=True)


def monta_locais(ano: int, turno: int, cargo: str = "presidente") -> gpd.GeoDataFrame:
    """Uma linha por local de votação com resultado do cargo no turno, com o ponto."""
    res = le_resultado(cargo, ano, turno, "local")
    locais = pd.read_parquet(PROCESSADO_DIR / f"locais_{ano}.parquet")
    geom = gpd.read_file(
        GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}.gpkg",
        columns=["id_local", "fonte_coord", "flag_sem_coord"],
    ).to_crs(CRS_SIRGAS2000)
    por_local, _ = contagens_secao(ano, turno)
    mun = _municipios_ibge(ano)[["cd_mun", "nm_mun"]]

    base = res[["id_local"]].merge(locais, on="id_local", how="left", validate="1:1")
    # Municípios novos que a malha do ano já tem (2026: Boa Esperança do Norte).
    novos = base["id_local"].str[:5].map(_cd_mun_novo(ano))
    novo = base["cd_mun"].isna() & novos.notna()
    base["cd_mun"] = base["cd_mun"].fillna(novos)
    base = base.merge(mun, on="cd_mun", how="left")
    base = base.merge(geom, on="id_local", how="left", validate="1:1")
    base = base.merge(por_local, on="id_local", how="left")

    # Sem código IBGE: o exterior e os municípios criados depois da malha de 2022
    # (MUNICIPIOS_SEM_IBGE). Estes ganham nome, UF e a coordenada do TSE.
    cd_tse = base["id_local"].str[:5]
    sem_ibge = base["cd_mun"].isna() & cd_tse.isin(list(MUNICIPIOS_SEM_IBGE))
    exterior = base["cd_mun"].isna() & ~sem_ibge
    nome = base["nm_mun"].where(~exterior, "Exterior")
    nome = nome.where(~sem_ibge, cd_tse.map(lambda c: MUNICIPIOS_SEM_IBGE.get(c, {}).get("nm_mun")))
    uf = base["sg_uf"].where(~exterior, "ZZ")
    # O cadastro de locais não traz a UF de quem não tem código IBGE de 2022.
    uf = uf.where(~(sem_ibge | novo), cd_tse.map(lambda c: MUNICIPIOS_SEM_IBGE.get(c, {}).get("sg_uf")))
    if sem_ibge.any():
        pontos_tse = gpd.points_from_xy(base["longitude"], base["latitude"], crs=CRS_SIRGAS2000)
        base["geometry"] = base["geometry"].where(~sem_ibge, pd.Series(list(pontos_tse), index=base.index))
        base["fonte_coord"] = base["fonte_coord"].where(~sem_ibge, "tse_nao_validada")
    if novo.any():
        # A etapa 02 não tinha o polígono: coordenada do TSE, validada aqui contra a
        # malha de 2024 (dentro: tse; fora: centroide do município novo).
        malha = gpd.read_file(malha_municipios(ano), columns=["cd_mun"]).to_crs(CRS_SIRGAS2000).set_index("cd_mun")
        pontos_tse = gpd.GeoSeries(
            gpd.points_from_xy(base["longitude"], base["latitude"]), index=base.index, crs=CRS_SIRGAS2000
        )
        poligono = gpd.GeoSeries(malha.geometry.reindex(base["cd_mun"]).to_numpy(), index=base.index, crs=CRS_SIRGAS2000)
        dentro = pontos_tse.within(poligono)
        centro = poligono.representative_point()
        base.loc[novo, "geometry"] = pontos_tse.where(dentro, centro)[novo]
        base.loc[novo, "fonte_coord"] = np.where(dentro[novo], "tse", "centroide_municipio")
        base.loc[novo, "flag_sem_coord"] = ~dentro[novo]
    atributos = pd.DataFrame({
        "id_local": base["id_local"],
        "nr_local": base["id_local"].str[-4:],
        "nr_zona": base["nr_zona"].astype("int32"),
        "id_zona_mun": _chave_municipio(base["cd_mun"], cd_tse)
        + base["nr_zona"].astype(int).astype(str).str.zfill(4),
        "cd_mun": base["cd_mun"],
        "cd_mun_tse": cd_tse,
        "nm_mun": nome,
        "sg_uf": uf,
        "nm_local_votacao": base["nm_local_votacao"].str.strip(),
        "ds_endereco": base["ds_endereco"].str.strip(),
        "nm_bairro": base["nm_bairro"].str.strip(),
        "nr_cep": base["nr_cep"],
        "ds_tipo_local": base["ds_tipo_local"],
        "local_especial": base["flag_especial"].fillna(False).astype(bool),
        "fonte_coord": base["fonte_coord"],
        "coord_aproximada": base["flag_sem_coord"].fillna(False).astype(bool),
        "latitude": np.nan,
        "longitude": np.nan,
        "n_secoes": base["n_secoes"].fillna(0).astype("int32"),
    })
    pontos = gpd.GeoSeries(base["geometry"].to_numpy(), crs=CRS_SIRGAS2000)
    atributos["latitude"] = pontos.y.round(6).to_numpy()
    atributos["longitude"] = pontos.x.round(6).to_numpy()

    resultado = colunas_resultado(res, ano, turno)
    gdf = gpd.GeoDataFrame(
        pd.concat([atributos.reset_index(drop=True), resultado.reset_index(drop=True)], axis=1),
        geometry=pontos.to_numpy(),
        crs=CRS_SIRGAS2000,
    )
    return gdf.sort_values("id_local").reset_index(drop=True)


def monta_municipios(ano: int, turno: int, cargo: str = "presidente") -> gpd.GeoDataFrame:
    """Uma linha por município com resultado do cargo no turno (+ exterior sem
    geometria, em Presidente), com Censo 2022."""
    res = le_resultado(cargo, ano, turno, "municipio")
    poligonos = gpd.read_file(malha_municipios(ano), columns=["cd_mun"]).to_crs(CRS_SIRGAS2000)
    mun = _municipios_ibge(ano)
    crosswalk = pd.read_parquet(PROCESSADO_DIR / "aux" / "crosswalk_mun_tse_ibge.parquet", columns=["cd_mun", "cd_mun_tse"])
    _, por_mun = contagens_secao(ano, turno)
    censo = censo_municipio(ano)[["cd_mun", "pop_2022", "dom_2022", "area_km2", "hab_km2"]]

    res = _separa_sem_ibge_do_exterior(res, ano, turno, cargo)
    # Municípios novos que a malha do ano já tem: ganham código IBGE, polígono e Censo.
    for cd_tse, cd_ibge in _cd_mun_novo(ano).items():
        res.loc[res["chave"] == f"TSE{cd_tse}", "cd_mun"] = cd_ibge
    base = res[["cd_mun"]].merge(mun, on="cd_mun", how="left", validate="1:1")
    base = base.merge(crosswalk, on="cd_mun", how="left", validate="1:1")
    base = base.merge(por_mun.rename(columns={"cd_mun": "chave"}), left_on=res["chave"].to_numpy(), right_on="chave", how="left")
    base = base.merge(censo, on="cd_mun", how="left")
    exterior = base["chave"] == SENTINELA_EXTERIOR
    base.loc[exterior, "nm_mun"] = "Exterior"
    base.loc[exterior, "sg_uf"] = "ZZ"
    for cd_tse, info in MUNICIPIOS_SEM_IBGE.items():
        linha = base["chave"] == f"TSE{cd_tse}"
        base.loc[linha, "cd_mun_tse"] = cd_tse
        for coluna in ("nm_mun", "sg_uf", "nm_uf", "nm_regiao"):
            base.loc[linha, coluna] = info[coluna]
    for col in ("n_locais", "n_secoes", "n_zonas"):
        base[col] = base[col].fillna(0).astype("int32")
    for col in ("pop_2022", "dom_2022"):
        base[col] = base[col].astype("Int64")
    base["nm_mun_sem_acento"] = sem_acento(base["nm_mun"])

    atributos = base[[
        "cd_mun", "cd_mun_tse", "nm_mun", "nm_mun_sem_acento", "sg_uf", "nm_uf", "nm_regiao", "n_zonas", "n_locais",
        "n_secoes", "pop_2022", "dom_2022", "area_km2", "hab_km2",
    ]]
    resultado = colunas_resultado(res, ano, turno)
    geometria = res[["cd_mun"]].merge(poligonos, on="cd_mun", how="left")["geometry"]
    gdf = gpd.GeoDataFrame(
        pd.concat([atributos.reset_index(drop=True), resultado.reset_index(drop=True)], axis=1),
        geometry=geometria.to_numpy(),
        crs=CRS_SIRGAS2000,
    )
    return gdf


def anula_fora_da_uf(gdf: gpd.GeoDataFrame, cand: pd.DataFrame) -> gpd.GeoDataFrame:
    """Governador e Senador: `votos_{nr}` e `pc_validos_{nr}` nulos nas linhas de UF
    em que não houve candidato válido com aquele número (em vez de 0).

    `cand` é a tabela `candidatos` do arquivo (com `sg_uf` e `destino`). Sem
    `sg_uf` (Presidente, deputados), devolve `gdf` como está.
    """
    if "sg_uf" not in cand:
        return gdf
    gdf = gdf.copy()
    validos = cand[cand["destino"] == "válido"]
    for coluna in [c for c in gdf.columns if c.startswith("votos_")]:
        nr = int(coluna.removeprefix("votos_"))
        ufs = set(validos.loc[validos["nr_candidato"] == nr, "sg_uf"])
        fora = ~gdf["sg_uf"].isin(ufs)
        if (gdf.loc[fora, coluna].fillna(0) != 0).any():
            raise ValueError(f"{coluna} tem votos fora das UFs do candidato")
        gdf[coluna] = gdf[coluna].astype("Int64").mask(fora)
        gdf[f"pc_validos_{nr}"] = gdf[f"pc_validos_{nr}"].mask(fora)
    return gdf


# ==== Escrita ==================================================================


def escreve_gpkg(gdf: gpd.GeoDataFrame, cand: pd.DataFrame, caminho: Path, camada: str) -> Path:
    """GeoPackage com a camada principal e a tabela `candidatos` (sem geometria)."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.unlink(missing_ok=True)
    if gdf.crs is None or gdf.crs.to_epsg() != CRS_SIRGAS2000:
        raise ValueError(f"GeoDataFrame fora de EPSG:{CRS_SIRGAS2000} antes de escrever {caminho}")
    gdf.to_file(caminho, layer=camada, driver="GPKG")
    pyogrio.write_dataframe(cand, caminho, layer="candidatos", driver="GPKG", append=True)
    return caminho


def escreve_xlsx(gdf: gpd.GeoDataFrame, cand: pd.DataFrame, caminho: Path) -> Path:
    """`.xlsx` com as abas `dados` (sem geometria) e `candidatos`."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dados = pd.DataFrame(gdf.drop(columns="geometry"))
    with pd.ExcelWriter(caminho, engine="openpyxl") as escritor:
        dados.to_excel(escritor, sheet_name="dados", index=False)
        cand.to_excel(escritor, sheet_name="candidatos", index=False)
    return caminho


def compacta(arquivos: list[Path], caminho_zip: Path) -> Path:
    """Zip com os arquivos na raiz (sem pastas)."""
    caminho_zip.unlink(missing_ok=True)
    with zipfile.ZipFile(caminho_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arquivo in arquivos:
            zf.write(arquivo, arcname=Path(arquivo).name)
    return caminho_zip


# ==== Atualização do EL2022_MU_BRA_CEM (Censo 2022) ============================

COLUNAS_CENSO_CEM = {
    # coluna CEM: (coluna do censo_municipio, descrição nova)
    "BA_001": (
        "dom_2022",
        "Domicílios particulares ocupados (permanentes e improvisados) do município, "
        "segundo o Censo Demográfico 2022 do IBGE (variável V0007 dos Agregados por "
        "Setores Censitários, tabela Básico)",
    ),
    "BA_002": (
        "pop_2022",
        "Total de pessoas residentes no município, segundo o Censo Demográfico 2022 do "
        "IBGE (variável V0001 dos Agregados por Setores Censitários, tabela Básico)",
    ),
    "AREA_KM2": (
        "area_km2",
        "Área do município em quilômetros quadrados, soma das áreas dos setores "
        "censitários do Censo 2022 (IBGE)",
    ),
    "HAB_KM2": (
        "hab_km2",
        "Habitantes por quilômetro quadrado (BA_002 / AREA_KM2), Censo 2022",
    ),
}


def le_cem_mu_2022(pasta: Path) -> gpd.GeoDataFrame:
    """Extrai o zip original do CEM em `pasta` e lê o shapefile (ISO-8859-1)."""
    with zipfile.ZipFile(CEM_MU_2022_ZIP) as zf:
        zf.extractall(pasta)
    return gpd.read_file(pasta / f"{CEM_MU_2022_NOME}.shp", encoding="ISO-8859-1")


def atualiza_censo_cem(cem: gpd.GeoDataFrame, censo: pd.DataFrame) -> gpd.GeoDataFrame:
    """Troca BA_001, BA_002, AREA_KM2 e HAB_KM2 pelos valores do Censo 2022.

    Polígonos sem código de município (lagoas e baía) ficam como estão.
    """
    novo = cem.copy()
    cd = novo["CD_MUN_I"].astype("Int64").astype(str)
    por_mun = censo.set_index("cd_mun")
    tem_codigo = novo["CD_MUN_I"].notna()
    for coluna, (origem, _) in COLUNAS_CENSO_CEM.items():
        valores = cd.map(por_mun[origem])
        novo.loc[tem_codigo, coluna] = valores[tem_codigo].astype(float).to_numpy()
    return novo


def escreve_shapefile_cem(gdf: gpd.GeoDataFrame, pasta: Path) -> list[Path]:
    """Shapefile com o mesmo nome e codificação do original (+ `.cpg`)."""
    caminho = pasta / f"{CEM_MU_2022_NOME}.shp"
    for ext in (".shp", ".shx", ".dbf", ".DBF", ".prj", ".cpg"):
        caminho.with_suffix(ext).unlink(missing_ok=True)
    gdf.to_file(caminho, driver="ESRI Shapefile", encoding="ISO-8859-1")
    return sorted(p for p in pasta.iterdir() if p.stem == CEM_MU_2022_NOME and p.suffix.lower() in (
        ".shp", ".shx", ".dbf", ".prj", ".cpg"
    ))


def atualiza_xlsx_cem(original: Path, destino: Path, censo: pd.DataFrame, nota: list[str]) -> Path:
    """Copia o `.xlsx` do CEM trocando as quatro colunas do Censo e suas descrições.

    Mantém a formatação (edita célula a célula com openpyxl) e acrescenta a aba
    `Atualização`.
    """
    import openpyxl

    shutil.copyfile(original, destino)
    wb = openpyxl.load_workbook(destino)
    por_mun = censo.set_index("cd_mun")

    dados = wb.worksheets[0]
    cabecalho = [c.value for c in dados[1]]
    col_cd = cabecalho.index("CD_MUN_I") + 1
    indices = {nome: cabecalho.index(nome) + 1 for nome in COLUNAS_CENSO_CEM}
    for linha in range(2, dados.max_row + 1):
        cd = dados.cell(row=linha, column=col_cd).value
        if cd is None:
            continue
        cd = str(int(cd))
        if cd not in por_mun.index:
            continue
        for nome, (origem, _) in COLUNAS_CENSO_CEM.items():
            valor = por_mun.at[cd, origem]
            dados.cell(row=linha, column=indices[nome]).value = (
                None if pd.isna(valor) else (int(valor) if origem in ("pop_2022", "dom_2022") else float(valor))
            )

    dicionario = wb.worksheets[1]
    for linha in dicionario.iter_rows(min_row=2):
        if linha[0].value in COLUNAS_CENSO_CEM:
            linha[1].value = COLUNAS_CENSO_CEM[linha[0].value][1]

    aba = wb.create_sheet("Atualização")
    for i, texto in enumerate(nota, start=1):
        aba.cell(row=i, column=1).value = texto
    wb.save(destino)
    return destino


def nota_atualizacao(cem: gpd.GeoDataFrame, novo: gpd.GeoDataFrame) -> list[str]:
    """Texto da seção "Atualização" (PDF e aba do `.xlsx`), com totais antes e depois."""
    sp_antes = cem.loc[cem["CD_MUN_I"] == 3550308].iloc[0]
    sp_depois = novo.loc[novo["CD_MUN_I"] == 3550308].iloc[0]

    def _fmt(valor: float, casas: int = 0) -> str:
        texto = f"{valor:,.{casas}f}"
        return texto.replace(",", "X").replace(".", ",").replace("X", ".")

    return [
        "Versão 2 (novembro de 2026): as colunas de população, domicílios, área e densidade "
        "passam a usar o Censo Demográfico 2022 do IBGE, no lugar do Censo 2010.",
        "Fonte: IBGE, Censo Demográfico 2022, " + CENSO_VERSAO + ". Os valores são a soma dos "
        "setores censitários de cada município.",
        "BA_001: antes, domicílios particulares permanentes (Censo 2010); agora, domicílios "
        "particulares ocupados, permanentes e improvisados (V0007, Censo 2022).",
        "BA_002: antes, moradores em domicílios particulares permanentes (Censo 2010); agora, "
        "total de pessoas residentes (V0001, Censo 2022). A tabela Básico de 2022 não traz os "
        "moradores só dos domicílios particulares: o total inclui quem mora em domicílio "
        "coletivo, e por isso não é diretamente comparável ao valor de 2010.",
        "AREA_KM2: antes, área municipal de 2021; agora, soma das áreas dos setores do Censo "
        "2022. HAB_KM2 é recalculada como BA_002 / AREA_KM2.",
        "Não mudam: os polígonos, a coluna AREA e todas as colunas de resultado eleitoral, "
        "eleitorado apto e abstenção. Os três polígonos sem município (lagoas dos Patos e "
        "Mirim e baía de Oiapoque) ficam como estavam.",
        f"Brasil: BA_002 passou de {_fmt(cem['BA_002'].sum())} para {_fmt(novo['BA_002'].sum())}; "
        f"BA_001, de {_fmt(cem['BA_001'].sum())} para {_fmt(novo['BA_001'].sum())}.",
        f"São Paulo (3550308): BA_002 de {_fmt(sp_antes['BA_002'])} para {_fmt(sp_depois['BA_002'])}; "
        f"BA_001 de {_fmt(sp_antes['BA_001'])} para {_fmt(sp_depois['BA_001'])}; AREA_KM2 de "
        f"{_fmt(sp_antes['AREA_KM2'], 3)} para {_fmt(sp_depois['AREA_KM2'], 3)}.",
    ]


# Texto da ficha do PDF original (DIC_EL2022_MU_BRA_CEM.pdf, p. 2), sem mudança.
DESCRICAO_CEM_MU_2022 = [
    "Base cartográfica georreferenciada em formato shape contendo os resultados eleitorais "
    "dos 5568 municípios brasileiros mais o Distrito Federal e o Distrito Estadual de "
    "Fernando de Noronha.",
    "O arquivo disponibilizado reúne os resultados eleitorais dos municípios brasileiros. O "
    "arquivo cartográfico totaliza 5573 polígonos, dos quais 5568 municípios, 2 recortes "
    "territoriais similares a município (Brasília e Fernando de Noronha), 2 extensões "
    "lacustres (lagoas dos Patos e Mirim - parte brasileira) e uma extensão marinha (baía de "
    "Oiapoque – parte brasileira). Estão incluídas as ilhas marítimas a partir de 0,1 km², "
    "agrupadas ao respectivo município, exceto as oceânicas Trindade e Martim Vaz.",
    "Foi incluído na tabela de atributos os dados de eleitorado apto a votar e dados de "
    "abstenção por município, lembrando que os valores de eleitorado apto a votar são "
    "diferentes para presidente e os demais tipos de candidatura devido ao voto em trânsito, "
    "que quando realizado em outro estado, só é permitido o voto para presidente.",
    "Acompanha o arquivo cartográfico a tabela com os votos por partido dos candidatos a "
    "presidente, governador, senador e os partidos com candidatos a deputado federal e "
    "estadual em 2022, tanto agregado ao arquivo cartográfico quanto separadamente em formato "
    "Excel, contidos no arquivo zipado aqui disponibilizado.",
]


def _tabela_md(df: pd.DataFrame) -> str:
    """Tabela markdown (pipe), sem quebras de linha nem `|` dentro das células."""
    def _limpa(valor: object) -> str:
        if valor is None or (isinstance(valor, float) and np.isnan(valor)):
            return ""
        return str(valor).replace("\n", " ").replace("|", "/").strip()

    cabecalho = "| " + " | ".join(_limpa(c) for c in df.columns) + " |"
    linhas = ["| " + " | ".join(_limpa(v) for v in linha) + " |" for linha in df.itertuples(index=False)]
    # Largura relativa das colunas (pandoc lê do número de traços quando a linha é
    # longa): proporcional ao maior texto da coluna, com o cabeçalho pesando pouco.
    # A palavra mais longa da coluna (cabeçalho incluído) é o mínimo, para nada
    # transbordar sem hifenização.
    def _palavra(texto: str) -> int:
        return max((len(p) for p in texto.split()), default=0)

    larguras = []
    for c in df.columns:
        textos = [_limpa(v) for v in df[c]]
        minimo = int(1.4 * max([_palavra(t) for t in textos] + [_palavra(_limpa(c)), 3])) + 2
        larguras.append(max(minimo, min(30, max(len(t) for t in textos))))
    separador = "|" + "|".join("-" * n for n in larguras) + "|"
    if len(separador) <= 72:
        separador = "|" + "|".join("-" * (n * 3) for n in larguras) + "|"
    return "\n".join([cabecalho, separador, *linhas])


def _n_paginas_pdf(caminho: Path) -> int:
    """Número de páginas de um PDF (conta os objetos `/Type /Page`)."""
    import re

    return len(re.findall(rb"/Type\s*/Page(?!s)", Path(caminho).read_bytes()))


def escreve_dicionario_cem(xlsx: Path, pdf_original: Path, destino_pdf: Path, nota: list[str]) -> Path:
    """Refaz o PDF de dicionário do CEM (Quarto + Typst) com a seção de atualização.

    Capa e créditos são as páginas do PDF original, como imagem; a ficha, a tabela
    de atributos e as tabelas de candidatos vêm do `.xlsx` já atualizado.
    """
    import subprocess

    abas = pd.read_excel(xlsx, sheet_name=None, dtype=str)
    nomes = list(abas)
    titulos_candidatos = {
        nomes[2]: "Candidatos a presidente do Brasil - Eleição 2022",
        nomes[3]: "Candidatos a governador por estado - Eleição 2022",
        nomes[4]: "Candidatos a senador - Eleição 2022",
        nomes[5]: "Partidos com candidatos a deputado federal e estadual - Eleição 2022",
    }
    quebra = ["```{=typst}", "#pagebreak()", "```", ""]
    partes = [
        "---",
        "lang: pt",
        "format:",
        "  typst:",
        "    papersize: a4",
        "    fontsize: 10pt",
        # Capa e créditos do original entram como fundo da primeira e da última
        # página. A regra vai no preâmbulo: um `#set page` (ou `#page`) no corpo
        # abre uma página nova e deixa uma em branco antes da capa. O número de
        # página do original nos créditos é coberto; o rodapé só numera o meio.
        "    include-in-header:",
        "      text: |",
        "        #set page(",
        "          background: context {",
        "            let p = here().page()",
        "            let ultima = counter(page).final().first()",
        '            if p == 1 { image("original.pdf", page: 1, width: 100%) }',
        "            else if p == ultima {",
        f'              image("original.pdf", page: {_n_paginas_pdf(pdf_original)}, width: 100%)',
        "              place(bottom + right, dx: -1.2cm, dy: -1.4cm, rect(fill: white, width: 3cm, height: 1.8cm))",
        "            }",
        "          },",
        "          footer: context {",
        "            let p = here().page()",
        "            if p > 1 and p < counter(page).final().first() { align(right, counter(page).display()) }",
        "          },",
        "        )",
        "---",
        "",
        "```{=typst}",
        "#set table(stroke: 0.5pt + luma(110))",
        "#show table: set text(size: 8.5pt, hyphenate: false)",
        "#show table: set par(justify: false)",
        "#v(1fr)",
        "#align(center)[#text(size: 13pt)[Versão 2 — atualização com o Censo 2022 (novembro de 2026)]]",
        "#v(1.2cm)",
        "#pagebreak()",
        "```",
        "",
        "**Centro de Estudos da Metrópole / CEM**",
        "",
        "Acervo cartográfico georreferenciado",
        "",
        "Bases Eleitorais",
        "",
        f"Arquivo: **{CEM_MU_2022_NOME}**",
        "",
        "Formato: ShapeFile · Tipo: Polígonos (áreas) · Data: 2022 · "
        "Projeção cartográfica: LatLong - SIRGAS 2000",
        "",
        "Tema: **Eleições 2022**",
        "",
        "Base cartográfica: Municípios eleitorais do Brasil",
        "",
        "Fonte de dados: TSE - TRE; IBGE - Censo Demográfico 2022 (população, domicílios e área)",
        "",
        "Descrição: " + DESCRICAO_CEM_MU_2022[0],
        "",
    ]
    for paragrafo in DESCRICAO_CEM_MU_2022[1:]:
        partes += [paragrafo, ""]
    partes += ["**Atualização — versão 2 (novembro de 2026)**", ""]
    partes += [f"- {linha}" for linha in nota] + [""]
    partes += quebra + ["**Atributos** (colunas do banco de dados):", "", _tabela_md(abas[nomes[1]].dropna(how="all")), ""]
    for aba, titulo in titulos_candidatos.items():
        partes += quebra + [f"**{titulo}**", "", _tabela_md(abas[aba].dropna(how="all")), ""]
    partes += [
        "```{=typst}",
        "#pagebreak()",
        "#hide[.]",
        "```",
        "",
    ]

    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        shutil.copyfile(pdf_original, pasta / "original.pdf")
        qmd = pasta / f"{Path(destino_pdf).stem}.qmd"
        qmd.write_text("\n".join(partes), encoding="utf-8")
        subprocess.run(f'quarto render "{qmd.name}" --to typst', cwd=pasta, check=True, shell=True)
        Path(destino_pdf).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pasta / f"{Path(destino_pdf).stem}.pdf", destino_pdf)
    return Path(destino_pdf)


# ==== Comparação com as bases de município do CEM ==============================

CEM_MU_ZIP = BRUTO_DIR / "cem" / "EL{ano}_MU_BRA_CEM_0.zip"


@functools.lru_cache(maxsize=4)
def _le_cem_mu(ano: int) -> pd.DataFrame:
    caminho = Path(str(CEM_MU_ZIP).format(ano=ano))
    cem = pd.DataFrame(pyogrio.read_dataframe(
        f"/vsizip/{caminho.as_posix()}/EL{ano}_MU_BRA_CEM.shp", encoding="ISO-8859-1", read_geometry=False
    ))
    cem = cem[cem["CD_MUN_I"].notna()].copy()
    cem["cd_mun"] = cem["CD_MUN_I"].astype("int64").astype(str)
    return cem


def compara_cem(gdf: gpd.GeoDataFrame, ano: int, cargo: str, turno: int) -> pd.DataFrame:
    """Compara o arquivo de municípios com a base `EL{ano}_MU_BRA_CEM`, coluna a coluna.

    Colunas do CEM: `{sigla}{aa}_{turno}{nr}` nos cargos com 2º turno,
    `{sigla}{aa}_{nr}` nos outros; 95 = brancos, 96 = nulos, `AP` = aptos.
    Uma linha por coluna, com o número de municípios em que os valores diferem e
    as somas dos dois lados. Municípios fora do arquivo (sem eleição no turno)
    contam como 0.
    """
    cem = _le_cem_mu(ano)
    prefixo = f"{CARGOS[cargo]['sigla']}{ano % 100:02d}_" + (str(turno) if cargo in CARGOS_COM_2_TURNO else "")
    nosso = pd.DataFrame(gdf.drop(columns="geometry"))
    nosso = nosso[nosso["cd_mun"].notna() & (nosso["cd_mun"] != SENTINELA_EXTERIOR)].set_index("cd_mun")
    cem = cem.set_index("cd_mun")

    def _nossa_coluna(sufixo: str) -> str | None:
        if sufixo == str(CODIGO_BRANCO):
            return "qt_brancos"
        if sufixo == str(CODIGO_NULO):
            return "qt_nulos"
        if sufixo == "AP":
            return "qt_aptos"
        return f"votos_{sufixo}" if sufixo.isdigit() else None

    linhas = []
    colunas_cem = [c for c in cem.columns if c.startswith(prefixo)]
    for col in colunas_cem:
        sufixo = col.removeprefix(prefixo)
        nossa = _nossa_coluna(sufixo)
        if nossa is None:  # AB = abstenção em %: já coberta pelos aptos e pelo comparecimento
            continue
        a = cem[col].fillna(0).round().astype("int64")
        b = (nosso[nossa] if nossa in nosso else pd.Series(dtype="float64")).reindex(cem.index).fillna(0)
        if nossa == "qt_nulos":  # o CEM soma aos nulos os votos em candidato inapto
            nossa = "qt_nulos + qt_anulados"
            b = b + nosso["qt_anulados"].reindex(cem.index).fillna(0)
        b = b.round().astype("int64")
        linhas.append({
            "coluna_cem": col, "coluna": nossa, "municipios_diferentes": int((a != b).sum()),
            "soma_cem": int(a.sum()), "soma_pacote": int(b.sum()),
        })
    # Números que só o pacote tem.
    usados = {f"votos_{c.removeprefix(prefixo)}" for c in colunas_cem}
    for nossa in [c for c in nosso.columns if c.startswith("votos_") and c not in usados]:
        linhas.append({
            "coluna_cem": None, "coluna": nossa, "municipios_diferentes": int((nosso[nossa] != 0).sum()),
            "soma_cem": 0, "soma_pacote": int(nosso[nossa].sum()),
        })
    return pd.DataFrame(linhas)


# ==== Documentação do pacote ===================================================

URL_REPOSITORIO = "https://github.com/gilandrade/dados_eleicoes"

DESCRICOES = {
    # Local de votação
    "id_local": "Identificador do local de votação: código TSE do município (5) + zona (4) + número do local (4)",
    "nr_local": "Número do local de votação na zona (TSE)",
    "nr_zona": "Número da zona eleitoral",
    "id_zona_mun": "Zona eleitoral × município: código IBGE do município (7) + zona (4); EXTERIOR + zona para o "
    "exterior; TSE + código TSE + zona para município sem código IBGE de 2022",
    "nm_local_votacao": "Nome do local de votação (TSE)",
    "ds_endereco": "Endereço do local de votação (TSE)",
    "nm_bairro": "Bairro do local de votação (TSE)",
    "nr_cep": "CEP do local de votação (TSE)",
    "ds_tipo_local": "Tipo do local (TSE): Convencional, Temporário, Preso provisório ou Voto em trânsito",
    "local_especial": "Verdadeiro para preso provisório e voto em trânsito (locais sem eleitorado residente)",
    "fonte_coord": "Origem da coordenada: tse (do TSE, validada no município); tse_confirmado (do TSE, "
    "reprovada na validação, mas a menos de 800 m de outra referência); ano_2018/ano_2022 (coordenada "
    "validada do mesmo local em outra eleição); hidalgo (geocodificação de Hidalgo et al.); geocodebr "
    "(endereço geocodificado com o CNEFE); centroide_municipio",
    "coord_aproximada": "Verdadeiro quando o ponto é o centroide do município (coordenada não resolvida)",
    "latitude": "Latitude do ponto (SIRGAS 2000)",
    "longitude": "Longitude do ponto (SIRGAS 2000)",
    "n_secoes": "Número de seções eleitorais do cadastro do TSE no turno (inclui seções agregadas)",
    # Município
    "cd_mun": "Código IBGE do município (7 dígitos); EXTERIOR na linha dos votos do exterior (só Presidente) "
    "no arquivo de municípios e vazio (sg_uf = ZZ) nos locais",
    "cd_mun_tse": "Código TSE do município (5 dígitos)",
    "nm_mun": "Nome do município",
    "nm_mun_sem_acento": "Nome do município em maiúsculas, sem acento (como MUN_NOME nas bases do CEM)",
    "sg_uf": "Sigla da UF (ZZ = exterior)",
    "nm_uf": "Nome da UF",
    "nm_regiao": "Grande região",
    "n_zonas": "Número de zonas eleitorais com seções no município, no turno",
    "n_locais": "Número de locais de votação com seções no município, no turno",
    "pop_2022": "Total de pessoas residentes, Censo 2022 (IBGE, V0001, soma dos setores)",
    "dom_2022": "Domicílios particulares ocupados, Censo 2022 (IBGE, V0007, soma dos setores)",
    "area_km2": "Área em km², soma dos setores do Censo 2022",
    "hab_km2": "Habitantes por km² (pop_2022 / area_km2)",
    # Resultado
    "ano": "Ano da eleição",
    "turno": "Turno (1 ou 2)",
    "qt_aptos": "Eleitores aptos para o cargo (TSE, detalhe_votacao_secao, por seção). Em Presidente inclui "
    "o voto em trânsito de outra UF, que só vota para Presidente; por isso os aptos mudam de um cargo para "
    "outro. Onde a seção não está nesse arquivo, usa-se o eleitorado do cadastro de locais",
    "qt_comparecimento": "Comparecimento (eleitores que votaram para o cargo)",
    "qt_abstencoes": "Abstenções (aptos − comparecimento)",
    "qt_validos": "Votos válidos (soma das colunas votos_). Em Senador com 2 vagas (2018 e 2026), cada "
    "eleitor dá 2 votos, e válidos + brancos + nulos + anulados somam o dobro do comparecimento",
    "qt_brancos": "Votos em branco",
    "qt_nulos": "Votos nulos",
    "qt_anulados": "Votos anulados: em candidato cuja candidatura foi indeferida, cancelada ou cassada. O "
    "TSE os publica por seção com o número do candidato, mas não os conta como válidos na totalização "
    "(destino 'Anulado' no votacao_candidato_munzona; em 2018, que não lista os anulados, candidato "
    "ausente dele); as bases do CEM os somam aos nulos",
    "qt_anulados_apu_sep": "Votos anulados e apurados em separado: em 2018, código 97 do TSE (746 votos em "
    "Salvador, em todos os cargos); em 2022 e 2026, votos em candidato com destino 'Anulado sub judice' "
    "(candidatura ainda em julgamento)",
    "qt_brancos_nulos": "Votos em branco + nulos",
    "pc_comparecimento": "Comparecimento sobre aptos (%)",
    "pc_abstencao": "Abstenção sobre aptos (%)",
    "pc_brancos": "Brancos sobre comparecimento (%); em Senador com 2 vagas pode passar de 100",
    "pc_nulos": "Nulos sobre comparecimento (%); em Senador com 2 vagas pode passar de 100",
    "pc_brancos_nulos": "Brancos + nulos sobre comparecimento (%); em Senador com 2 vagas pode passar de 100",
    "nr_mais_votado": "Número do candidato (ou do partido, nos deputados) mais votado",
    "margem_validos": "Diferença entre o 1º e o 2º colocados, em pontos percentuais dos válidos",
}


_OUTRA_ELEICAO = {2018: "ano_2022", 2022: "ano_2018", 2026: "ano_2022"}


def descricao_coluna(coluna: str, ano: int | None = None, camada: str | None = None) -> str:
    """Descrição de uma coluna; com `ano` e `camada`, o texto que vale naquela base."""
    if ano is None:
        return DESCRICOES[coluna]
    novo = ano >= ANO_MALHA_2024  # sem o painel de Hidalgo et al.
    malha = 2024 if novo else 2022
    if coluna == "cd_mun":
        return f"Código IBGE do município (7 dígitos, malha de {malha}); " + (
            "EXTERIOR na linha dos votos do exterior (só Presidente)" if camada == "municipios"
            else "vazio no exterior (sg_uf = ZZ)"
        )
    if coluna == "id_zona_mun":
        return "Zona eleitoral × município: código IBGE do município (7) + zona (4); EXTERIOR + zona para o exterior"
    if coluna == "fonte_coord":
        partes = [
            "tse (do TSE, validada no município)",
            "tse_confirmado (do TSE, reprovada na validação, mas a menos de 800 m de outra referência)",
            f"{_OUTRA_ELEICAO[ano]} (coordenada validada do mesmo local em outra eleição)",
        ]
        if not novo:
            partes.append("hidalgo (geocodificação de Hidalgo et al.)")
        partes += ["geocodebr (endereço geocodificado com o CNEFE)", "centroide_municipio"]
        return "Origem da coordenada: " + "; ".join(partes)
    return DESCRICOES[coluna]


def _descricao_votos(coluna: str, cargo: str) -> str:
    prefixo, nr = coluna.rsplit("_", 1)
    if CARGOS[cargo]["por_partido"]:
        texto = f"Votos no partido de número {nr} (nominais + legenda; ver a tabela candidatos)"
    elif cargo == "presidente":
        texto = f"Votos no candidato de número {nr} (ver a tabela candidatos)"
    else:
        texto = f"Votos no candidato de número {nr} da UF da linha (ver a tabela candidatos, por UF)"
    return texto + (" sobre os válidos (%)" if prefixo == "pc_validos" else "")


def dicionario_colunas(colunas: list[str], camada: str, cargo: str, ano: int | None = None) -> pd.DataFrame:
    """Uma linha por coluna da camada, com a descrição."""
    linhas = []
    for coluna in colunas:
        if coluna == "geometry":
            continue
        if coluna.startswith(("votos_", "pc_validos_")):
            descricao = _descricao_votos(coluna, cargo)
        else:
            descricao = descricao_coluna(coluna, ano, camada)
        linhas.append({"camada": camada, "cargo": CARGOS[cargo]["sigla"], "coluna": coluna, "descricao": descricao})
    return pd.DataFrame(linhas)


def _md(df: pd.DataFrame) -> str:
    cab = "| " + " | ".join(df.columns) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    corpo = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in linha) + " |" for linha in df.itertuples(index=False)]
    return "\n".join([cab, sep, *corpo])


def _fmt_votos(tabela: pd.DataFrame) -> pd.DataFrame:
    tabela = tabela.copy()
    tabela["qt_votos"] = tabela["qt_votos"].map(lambda v: f"{v:,}".replace(",", "."))
    tabela["pc_validos"] = tabela["pc_validos"].map(lambda v: f"{v:.2f}".replace(".", ","))
    return tabela


def escreve_leiame(destino: Path, arquivos: pd.DataFrame, tabelas: dict, dicionario: pd.DataFrame) -> Path:
    """`LEIAME.md` do pacote: conteúdo, colunas, candidatos, observações e citação.

    `tabelas` tem a tabela `candidatos` de cada arquivo, pela chave (ano, cargo, turno).
    """
    cargos = pd.DataFrame([
        {"código": "PS1, PS2", "cargo": "Presidente", "turno": "1º e 2º", "colunas de votos": "por candidato"},
        {"código": "GO1, GO2", "cargo": "Governador", "turno": "1º e 2º (o 2º só nas UFs que tiveram 2º turno)",
         "colunas de votos": "por candidato (o número é do candidato da UF da linha)"},
        {"código": "SE", "cargo": "Senador", "turno": "único",
         "colunas de votos": "por candidato (o número é do candidato da UF da linha)"},
        {"código": "DF", "cargo": "Deputado federal", "turno": "único", "colunas de votos": "por partido"},
        {"código": "DE", "cargo": "Deputado estadual (e distrital, no DF)", "turno": "único",
         "colunas de votos": "por partido"},
    ])
    colunas = (
        dicionario.drop_duplicates("coluna")[["coluna"]]
        .assign(descricao=lambda d: d["coluna"].map(lambda c: DESCRICOES.get(c, "")))
        .loc[lambda d: ~d["coluna"].str.match(r"^(votos|pc_validos)_\d+$")]
    )
    partes = [
        "# Eleições gerais de 2018, 2022 e 2026 por local de votação e por município",
        "",
        "Resultados de Presidente, Governador, Senador e Deputados federal e estadual/distrital, por "
        "local de votação e por município do Brasil, com os votos de cada candidato (ou partido, nos "
        "deputados) em colunas separadas. Os arquivos de município trazem tudo o que as bases "
        "`EL2018_MU_BRA_CEM` e `EL2022_MU_BRA_CEM` do Download de Dados do Centro de Estudos da "
        "Metrópole (CEM) trazem, com população, domicílios, área e densidade do Censo 2022 e a malha "
        "municipal do IBGE de 2022 nos três anos, e acrescentam 2026. Os locais de votação cobrem o "
        "Brasil inteiro (o CEM tem só o estado de São Paulo e regiões metropolitanas).",
        "",
        "## Arquivos",
        "",
        "Nome no padrão do CEM: `EL{ano}_{LV|MU}_BRA_{cargo}{turno}`, com `LV` = local de votação e "
        "`MU` = município. Os códigos de cargo são os das colunas das bases do CEM:",
        "",
        _md(cargos),
        "",
        "Um GeoPackage (`.gpkg`) e um Excel (`.xlsx`) por arquivo, com o mesmo conteúdo. No GeoPackage, "
        "a camada principal (`locais` ou `municipios`) traz a geometria em SIRGAS 2000 (EPSG:4674) e a "
        "tabela `candidatos` traz nome, nome de urna e partido de cada número (nos deputados, sigla e "
        "nome de cada partido). No Excel, as abas são `dados` e `candidatos`. Um zip por ano.",
        "",
        _md(arquivos),
        "",
        "## Colunas",
        "",
        "Percentuais de 0 a 100. Votos em `votos_{número}` e `pc_validos_{número}`; o dono de cada "
        "número está na tabela `candidatos` do arquivo. A lista completa, por arquivo, está em "
        "`dicionario_colunas.csv`.",
        "",
        _md(colunas),
        "",
        "## Candidatos a Presidente",
        "",
    ]
    for (ano, cargo, turno), cand in sorted(tabelas.items()):
        if cargo != "presidente":
            continue
        tabela = _fmt_votos(cand[["nr_candidato", "nm_urna", "sg_partido", "qt_votos", "pc_validos"]])
        partes += [f"### {ano}, {turno}º turno", "", _md(tabela), ""]
    partes += ["## Partidos (deputados)", ""]
    for (ano, cargo, turno), cand in sorted(tabelas.items()):
        if not CARGOS[cargo]["por_partido"]:
            continue
        tabela = _fmt_votos(cand[["nr_partido", "sg_partido", "qt_votos", "pc_validos"]])
        partes += [f"### {CARGOS[cargo]['rotulo']}, {ano}", "", _md(tabela), ""]
    partes += [
        "Os candidatos a Governador e a Senador, por UF, estão na tabela `candidatos` de cada arquivo.",
        "",
        "## Observações",
        "",
        "- **Fonte.** TSE (Portal de Dados Abertos): `eleitorado_local_votacao`, `votacao_secao`, "
        "`detalhe_votacao_secao` e `consulta_cand`. Malha municipal: IBGE 2022 (2018 e 2022) e 2024 (2026), "
        "via geobr. Censo 2022: "
        "IBGE, " + CENSO_VERSAO + ".",
        "- **Censo 2022 nos três anos.** A eleição de 2018 fica a 4 anos do Censo 2022 e a 8 do Censo "
        "2010. As bases do CEM de 2018 e 2022 usam o Censo 2010.",
        "- **Malha municipal.** 2018 e 2022 usam a malha do IBGE de 2022 (5.570 municípios), a divisão "
        "vigente nessas eleições. 2026 usa a de 2024 (5.571), que tem Boa Esperança do Norte (MT, "
        "5101837), desmembrado de Sorriso e Nova Ubiratã. O Censo 2022 não separa o município novo: os "
        "setores de Sorriso e Nova Ubiratã vão para o município da malha de 2024 que contém o ponto "
        "interno do setor (14 setores, 2.735 pessoas), e os dois perdem essa parte em 2026.",
        "- **Aptos por cargo.** Vêm do TSE por seção (`detalhe_votacao_secao`). O eleitor em trânsito fora "
        "da sua UF só vota para Presidente, então os aptos de Presidente são maiores que os dos outros "
        "cargos. Onde a seção não está nesse arquivo, usa-se o eleitorado do cadastro de locais.",
        "- **Exterior.** Só há voto no exterior para Presidente. Esses votos estão numa linha sem "
        "geometria: `cd_mun = EXTERIOR` no arquivo de municípios e `cd_mun` vazio (`sg_uf = ZZ`) nos "
        "locais. Com eles, a soma das colunas dá o total oficial do Brasil.",
        "- **Governador e Senador.** O número é do candidato da UF de cada linha: `votos_13` em São Paulo "
        "e em Minas Gerais são candidatos diferentes. Os percentuais dos válidos da tabela `candidatos` "
        "são sobre a UF. O 2º turno de Governador só traz as UFs em que ele aconteceu.",
        "- **Senador com 2 vagas** (2018 e 2026): cada eleitor dá até 2 votos, então os válidos somam "
        "quase o dobro do comparecimento.",
        "- **Deputados.** Votos por partido: os nominais (dos candidatos do partido) mais os de legenda, "
        "como nas bases do CEM. O número do partido são os 2 primeiros dígitos do número do candidato; "
        "o mesmo número pode ser de partidos diferentes em anos diferentes. Deputado estadual inclui o "
        "distrital, no DF.",
        "- **Coordenadas.** A coordenada do TSE é validada contra o município; quando falha, usa-se a "
        "coordenada validada do mesmo local em outra eleição, a geocodificação de Hidalgo et al., depois "
        "o endereço geocodificado com o CNEFE, e, em último caso, o centroide do município "
        "(`coord_aproximada`). A coluna `fonte_coord` diz a origem.",
        "- **Seções agregadas.** Uma seção agregada vota no local da sua seção principal; `n_secoes` "
        "conta as seções do cadastro, agregadas incluídas.",
        "- **Números de candidato mudam entre eleições.** Bolsonaro foi 17 (PSL) em 2018 e 22 (PL) em "
        "2022; em 2026 o 22 é Flávio Bolsonaro. Compare pelo nome da tabela `candidatos`.",
        "- **Votos anulados.** Voto em candidato cuja candidatura foi indeferida, cancelada ou cassada "
        "está em `qt_anulados`, fora dos válidos e dos votos do partido: o TSE o publica por seção com o "
        "número do candidato, mas não o conta na totalização. O destino de cada candidato vem do "
        "`votacao_candidato_munzona` do TSE (válido, válido para a legenda, anulado, anulado sub judice), "
        "na versão atual da totalização. Os votos anulados sub judice estão em `qt_anulados_apu_sep`. "
        "Entram aqui também candidatos cassados ou com registro negado depois da eleição, como Selma "
        "Arruda (Senado/MT, 2018), Daniel Silveira (Senado/RJ, 2022) e Ricardo Coutinho (Senado/PB, "
        "2022). Na tabela `candidatos` de Governador e Senador eles aparecem com `destino = anulado` (ou "
        "`anulado sub judice`) e o total de votos na UF, sem percentual. As bases do CEM somam os "
        "anulados aos nulos (`qt_nulos + qt_anulados`) e usam uma versão mais antiga da totalização: "
        "nela, por exemplo, os votos de Daniel Silveira ainda contam como válidos, e alguns partidos de "
        "deputado diferem um pouco (cerca de 0,4% dos votos).",
        "- **Senado em Mato Grosso, 2018.** A base `EL2018_MU_BRA_CEM` traz em MT os candidatos da eleição "
        "suplementar de 2020 (que substituiu Selma Arruda), não os de 2018. Aqui está a eleição de 2018.",
        "- **Local de votação muda entre turnos** em uma pequena fração das seções; por isso os "
        "arquivos de locais são por turno.",
        "- **Diferenças em relação às bases do CEM.** Não há os 3 polígonos sem município (lagoas dos "
        "Patos e Mirim, baía de Oiapoque). A abstenção vem em quantidade e em percentual.",
        "",
        "## Como citar",
        "",
        "Centro de Estudos da Metrópole (CEM/USP). Eleições gerais de 2018, 2022 e 2026 por local de "
        "votação e por município. Dados do TSE e do IBGE. Novembro de 2026. "
        f"Código: {URL_REPOSITORIO}.",
        "",
        "Licença: Creative Commons Atribuição 4.0 (CC BY 4.0).",
        "",
    ]
    destino = Path(destino)
    destino.write_text("\n".join(partes), encoding="utf-8")
    return destino


# ==== Dicionário em PDF do pacote (um por ano e unidade) =======================

# Créditos das bases que atualizam as do CEM (município de 2018 e 2022): os do PDF
# de 2022 do CEM, com Gil Andrade Gontijo na equipe.
EQUIPE_TRANSFERENCIA = [
    "Mariana Giannotti",
    "Daniel Waldvogel Thomé da Silva",
    "Edgard Fusaro",
    "José Donizete Cazzolato",
    "Fernando Gomes",
    "Hans Harley Ccacyahuillca Bejar",
    "Kaue Oliveira Almeida",
    "Gil Andrade Gontijo",
]
CONSELHO_CEM = [
    "Adrián Gurza Lavalle", "Bianca Freire-Medeiros", "Charles Kirschbaum", "Eduardo Marques",
    "Gabriela Lotta", "Jonathan Phillips", "Marcos Nobre", "Mariana Giannotti", "Marta Arretche",
    "Renata Mirandola Bichir", "Úrsula Peres", "Vera Schattan Coelho",
]
# Bases novas (todas, menos município de 2018 e 2022): logo novo e créditos atuais.
LOGO_CEM = Path(__file__).resolve().parents[2] / "assets" / "cem" / "CEM_LOGO_COMPLETO_VER.svg"
EQUIPE_TRANSFERENCIA_ATUAL = ["Mariana Giannotti", "Gil Andrade Gontijo"]
CONSELHO_CEM_ATUAL = [
    "Adrián Gurza Lavalle", "Eduardo Marques", "Gabriela Lotta", "Mariana Giannotti", "Marta Arretche",
    "Renata Mirandola Bichir", "Úrsula Peres", "Catarina Segatto", "Fernanda Lima e Silva",
]
# Títulos das tabelas de candidatos, como nos PDFs do CEM.
TITULO_CARGO = {
    "PS": "Candidatos a presidente do Brasil",
    "GO": "Candidatos a governador por estado",
    "SE": "Candidatos a senador por estado",
    "DF": "Partidos com candidatos a deputado federal",
    "DE": "Partidos com candidatos a deputado estadual/distrital",
}
_MINUSCULAS = {"da", "de", "do", "das", "dos", "e"}


def nota_atualizacao_pacote(ano: int) -> list[str]:
    """O que muda no arquivo de municípios em relação à base `EL{ano}_MU_BRA_CEM`."""
    nota = [
        f"Esta base atualiza a EL{ano}_MU_BRA_CEM. População e domicílios passam a ser o total de pessoas "
        "residentes (V0001) e os domicílios particulares ocupados (V0007) do Censo Demográfico 2022, no "
        "lugar dos moradores e dos domicílios particulares permanentes do Censo 2010 (conceitos "
        "diferentes, não comparáveis diretamente). Área e densidade também são de 2022.",
        "Polígonos da malha municipal do IBGE de 2022 (versão simplificada do geobr, Ipea), com 5.570 "
        "municípios. Saem os 3 polígonos sem município da base anterior (lagoas dos Patos e Mirim e baía "
        "de Oiapoque).",
        "Um arquivo por cargo e turno (PS1, PS2, GO1, GO2, SE, DF e DE, os códigos das colunas da base "
        "anterior), em GeoPackage e Excel, com os nomes das colunas por extenso. Os mesmos dados também "
        f"saem por local de votação, para o Brasil inteiro (EL{ano}_LV_BRA).",
        "Aptos e abstenção de cada cargo, inclusive dos deputados, do detalhe_votacao_secao do TSE (a "
        "base anterior tinha aptos só de Presidente e de Governador/Senador, e abstenção só em "
        "percentual).",
        "Votos em candidatos com candidatura indeferida, cancelada ou cassada ficam em qt_anulados, "
        "separados dos nulos (a base anterior os somava aos nulos), segundo a versão atual da "
        "totalização do TSE. Por isso os votos dos partidos de deputado podem diferir um pouco da base "
        "anterior (cerca de 0,4%).",
    ]
    if ano == 2018:
        nota.append(
            "Senado em Mato Grosso: a base anterior trazia os candidatos da eleição suplementar de 2020; "
            "aqui está a eleição de 2018. Os votos de Selma Arruda (eleita e cassada em 2019) estão em "
            "qt_anulados."
        )
    else:
        nota.append(
            "Os votos de Daniel Silveira (Senado/RJ), contados como válidos na base anterior, estão em "
            "qt_anulados, como na totalização atual."
        )
    return nota


def _ty(texto: object) -> str:
    """Texto como string Typst (escapa `\\` e `"`), seguro para qualquer conteúdo."""
    return '"' + str(texto).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _caixa_mista(nome: str) -> str:
    """'PARTIDO DA MULHER BRASILEIRA' -> 'Partido da Mulher Brasileira'."""
    palavras = str(nome).lower().split()
    return " ".join(p if (i and p in _MINUSCULAS) else p[:1].upper() + p[1:] for i, p in enumerate(palavras))


def _votos(v: object) -> str:
    return f"{int(v):,}".replace(",", ".")


def _pc_txt(v: object) -> str:
    if pd.isna(v):
        return ""
    if 0 < v < 0.005:
        return "<0,01"
    return f"{v:.2f}".replace(".", ",")


def _tabela_typst(
    df: pd.DataFrame, colunas: str, alinhamento: list[str], separa_por: str | None = None,
) -> str:
    """Tabela Typst: cabeçalho em negrito, centralizado e repetido a cada página.

    `colunas` é a lista de larguras do Typst (ex.: "(auto, 1fr)"); `alinhamento`, um
    por coluna (left, center, right). Com `separa_por`, um filete mais grosso entre
    grupos (as UFs).
    """
    cab = ", ".join(f"table.cell(align: center + horizon, strong({_ty(c)}))" for c in df.columns)
    linhas, anterior = [], None
    for _, linha in df.iterrows():
        if separa_por is not None:
            if anterior is not None and linha[separa_por] != anterior:
                linhas.append("table.hline(stroke: 1.2pt + luma(60)),")
            anterior = linha[separa_por]
        linhas.append(", ".join(_ty("" if pd.isna(v) else v) for v in linha) + ",")
    alin = "(" + ", ".join(alinhamento) + ",)"
    return "\n".join([
        f"#table(columns: {colunas}, align: {alin},",
        f"  table.header({cab}),",
        *linhas,
        ")",
    ])


def _tabela_candidatos(cand: pd.DataFrame) -> str:
    """Tabela de candidatos (ou partidos) de um arquivo, nos moldes do CEM."""
    t = cand.copy()
    t["qt_votos"] = t["qt_votos"].map(_votos)
    t["pc_validos"] = t["pc_validos"].map(_pc_txt)
    t["nm_partido"] = t["nm_partido"].map(_caixa_mista)
    if "nr_partido" in t:
        t = t.sort_values("nr_partido")[["nr_partido", "sg_partido", "nm_partido", "qt_votos", "pc_validos"]]
        t.columns = ["Número do partido", "Sigla do partido", "Nome do partido", "Votos", "% válidos"]
        return _tabela_typst(t, "(auto, auto, 1fr, auto, auto)", ["center", "left", "left", "right", "right"])
    if "destino" in t:
        t["nm_urna"] = t["nm_urna"].where(t["destino"] == "válido", t["nm_urna"] + " (" + t["destino"] + ")")
    colunas = ["nr_candidato", "sg_partido", "nm_partido", "nm_candidato", "nm_urna", "qt_votos", "pc_validos"]
    rotulos = ["Número", "Sigla do partido", "Nome do partido", "Nome completo", "Nome de urna", "Votos",
               "% válidos"]
    larguras = ["auto", "auto", "1.2fr", "1.5fr", "1fr", "auto", "auto"]
    alin = ["center", "left", "left", "left", "left", "right", "right"]
    separa = None
    if "sg_uf" in t:
        t = t.sort_values(["sg_uf", "nr_candidato"])
        colunas, rotulos, larguras, alin, separa = (
            ["sg_uf", *colunas], ["UF", *rotulos], ["auto", *larguras], ["center", *alin], "UF",
        )
    else:
        t = t.sort_values("nr_candidato")
    t = t[colunas].set_axis(rotulos, axis=1)
    return _tabela_typst(t, "(" + ", ".join(larguras) + ")", alin, separa_por=separa)


def _logo_antigo_png(original: bytes, destino: Path) -> Path:
    """Faixa do logo (topo da capa do PDF de 2022 do CEM) como PNG, sem a camada de texto."""
    import subprocess

    pasta = destino.parent
    (pasta / "_original.pdf").write_bytes(original)
    (pasta / "_faixa.typ").write_text(
        '#set page(width: 21cm, height: 5.5cm, margin: 0pt)\n'
        # Um `image` maior que a página sai centralizado; o box com clip corta o topo.
        '#box(width: 21cm, height: 5.5cm, clip: true, align(top, '
        'image("_original.pdf", page: 1, width: 21cm, height: 29.7cm)))\n',
        encoding="utf-8",
    )
    subprocess.run(
        f'quarto typst compile _faixa.typ "{destino.name}" --ppi 300', cwd=pasta, check=True, shell=True
    )
    return destino


def _pdf_dicionario_cem_2022() -> bytes:
    with zipfile.ZipFile(CEM_MU_2022_ZIP) as zf:
        return zf.read(f"DIC_{CEM_MU_2022_NOME}.pdf")


def escreve_dicionario_pacote(ano: int, unidade: str, destino_pdf: Path, pasta: Path = PACOTE_DIR) -> Path:
    """PDF de dicionário de uma base do pacote (`EL{ano}_{unidade}_BRA`), nos moldes do CEM.

    Lê os arquivos já escritos em `pasta` (tabela `candidatos` de cada GeoPackage e
    `dicionario_colunas.csv`) e escreve o documento em Typst. Município de 2018 e
    2022 (atualizações de bases do CEM): logo do PDF de 2022 do CEM (só a faixa do
    logo, rasterizada), créditos daquele PDF e seção de atualização. As demais
    bases: logo novo (`LOGO_CEM`) e créditos atuais.
    """
    import subprocess

    municipio = unidade == "MU"
    camada = "municipios" if municipio else "locais"
    atualiza = municipio and ano in (2018, 2022)
    equipe = EQUIPE_TRANSFERENCIA if atualiza else EQUIPE_TRANSFERENCIA_ATUAL
    conselho = CONSELHO_CEM if atualiza else CONSELHO_CEM_ATUAL

    chaves = [(cargo, turno) for cargo in CARGOS_GERAIS for turno in turnos(cargo)]
    arquivos = {chave: nome_arquivo(ano, *chave, unidade) for chave in chaves}
    dic = pd.read_csv(Path(pasta) / "dicionario_colunas.csv", dtype=str)
    dic = dic[dic["arquivo"].isin(arquivos.values())]
    atributos = (
        dic.loc[~dic["coluna"].str.match(r"^(votos|pc_validos)_\d+$"), ["coluna", "descricao"]]
        .drop_duplicates("coluna")
    )
    atributos = pd.concat([atributos, pd.DataFrame([
        {"coluna": "votos_NN", "descricao": "Votos no candidato (ou partido, nos deputados) de número NN. Em "
         "Governador e Senador, o candidato da UF da linha, e vazio nas UFs sem candidato com esse número; "
         "nos deputados, votos nominais e de legenda do partido, sem os votos anulados (em qt_anulados)"},
        {"coluna": "pc_validos_NN", "descricao": "Votos do número NN sobre os válidos (%)"},
    ])], ignore_index=True).set_axis(["Variável", "Descrição"], axis=1)
    lista = pd.DataFrame([
        {
            "Arquivo": arquivo, "Cargo": CARGOS[cargo]["rotulo"],
            "Turno": f"{turno}º" if len(turnos(cargo)) > 1 else "único",
            "Linhas": _votos(pyogrio.read_info(Path(pasta) / f"{arquivo}.gpkg", layer=camada)["features"]),
        }
        for (cargo, turno), arquivo in arquivos.items()
    ])
    ps1 = pyogrio.read_dataframe(
        Path(pasta) / f"{arquivos[('presidente', 1)]}.gpkg", layer=camada, columns=["sg_uf"], read_geometry=False
    )
    n_exterior = int((ps1["sg_uf"] == "ZZ").sum())

    cargos_txt = "Presidente, Governador, Senador, Deputado federal e Deputado estadual/distrital"
    ano_malha = 2024 if ano >= ANO_MALHA_2024 else 2022
    n_mun = _votos(pyogrio.read_info(malha_municipios(ano))["features"])
    if municipio:
        descricao = [
            "Base cartográfica georreferenciada em formato GeoPackage contendo os resultados das eleições "
            f"gerais de {ano} ({cargos_txt}) nos {n_mun} municípios do Brasil, com os votos de cada "
            "candidato (ou partido, nos deputados) em colunas separadas, aptos, comparecimento, abstenção, "
            "brancos, nulos e votos anulados. Inclui o total de pessoas residentes, os domicílios "
            "particulares ocupados, a área e a densidade do Censo Demográfico 2022 do IBGE, sobre a malha "
            f"municipal do IBGE de {ano_malha} (versão simplificada do geobr, Ipea). Os votos do exterior "
            "(só Presidente) estão numa linha sem geometria."
            + (" A malha de 2024 tem Boa Esperança do Norte (MT), desmembrado de Sorriso e Nova Ubiratã "
               "depois do Censo 2022: os setores do Censo desses dois municípios vão para o município da "
               "malha de 2024 que contém o ponto interno do setor (14 setores, 2.735 pessoas)."
               if ano_malha == 2024 else ""),
        ]
    else:
        geocod = (
            "a geocodificação de Hidalgo et al. (geocode_br_polling_stations), " if ano < 2026 else ""
        )
        descricao = [
            "Base cartográfica georreferenciada em formato GeoPackage contendo os resultados das eleições "
            f"gerais de {ano} ({cargos_txt}) nos {_votos(len(ps1))} locais de votação do 1º turno "
            f"(inclusive {n_exterior} no exterior, sem geometria), com os votos de cada candidato (ou "
            "partido, nos deputados) em colunas separadas, aptos, comparecimento, abstenção, brancos, "
            "nulos e votos anulados.",
            "A coordenada do TSE é validada contra a malha municipal do IBGE de 2022"
            + (" (de 2024 em Boa Esperança do Norte, MT, criado depois)" if ano_malha == 2024 else "")
            + ". Quando falha, usa-se "
            f"a coordenada validada do mesmo local em outra eleição, {geocod}o endereço geocodificado com o "
            "CNEFE 2022 ou, em último caso, o centroide do município (coord_aproximada; esses pontos "
            "continuam no arquivo). A coluna fonte_coord diz a origem"
            + (" (o painel de Hidalgo et al. não cobre 2026)." if ano >= 2026 else ".")
            + " O local de votação de uma seção pode mudar entre turnos, por isso há um arquivo por turno.",
        ]
    senado = (
        "Em Senador havia 2 vagas por UF e cada eleitor deu 2 votos; brancos, nulos e anulados também "
        "contam por voto, então válidos, brancos, nulos e anulados somam o dobro do comparecimento, e "
        "pc_brancos e pc_nulos podem passar de 100."
        if ano in (2018, 2026) else "Em Senador havia 1 vaga por UF."
    )
    descricao.append(
        "Cada arquivo tem um GeoPackage (camada principal e tabela candidatos) e um Excel (abas dados e "
        f"candidatos), com o mesmo conteúdo. {senado} Nos deputados, os votos do partido são os nominais "
        "mais os de legenda; Deputado estadual inclui o distrital, no DF. Percentuais de 0 a 100. Nas "
        "tabelas, os candidatos com votos anulados aparecem com o destino entre parênteses e sem "
        "percentual. Dados do TSE: eleitorado_local_votacao, votacao_secao, detalhe_votacao_secao, "
        "votacao_candidato_munzona e consulta_cand."
    )
    fonte = (
        f"TSE - Portal de Dados Abertos; IBGE - Censo Demográfico 2022 e malha municipal {ano_malha}"
        if municipio else
        "TSE - Portal de Dados Abertos; IBGE - malha municipal 2022"
        + (" e 2024" if ano_malha == 2024 else "") + " e CNEFE 2022"
        + ("; Hidalgo et al. (geocode_br_polling_stations)" if ano < 2026 else "")
    )
    siglas = ", ".join(CARGOS[c]["sigla"] + (str(t) if len(turnos(c)) > 1 else "") for c, t in chaves)
    alvo = "Municípios" if municipio else "Locais de Votação"
    logo = (
        'place(top + left, image("logo_antigo.png", width: 21cm))' if atualiza
        else 'place(top + center, dy: 1.6cm, image("logo.svg", width: 4.6cm))'
    )

    def _nomes(nomes: list[str]) -> str:
        return " \\\n".join(f"#emph({_ty(n)})" for n in nomes)

    doc = [
        f"#set document(title: {_ty(f'Dicionário EL{ano}_{unidade}_BRA')}, author: \"Centro de Estudos da Metrópole\")",
        '#set text(font: "Calibri", size: 11pt, lang: "pt", hyphenate: false)',
        "#set par(justify: true)",
        "#set page(",
        '  paper: "a4",',
        "  margin: (left: 3cm, right: 1.7cm, top: 2.5cm, bottom: 2.5cm),",
        "  footer: context align(right, text(size: 10pt, counter(page).display())),",
        "  background: context {",
        "    let p = here().page()",
        "    if p == 1 or p == counter(page).final().first() {",
        f"      {logo}",
        "    }",
        "  },",
        ")",
        "#set table(stroke: 0.5pt + luma(110), inset: (x: 4pt, y: 3pt))",
        "#show table: set text(size: 8.5pt)",
        "#show table: set par(justify: false)",
        "",
        # Capa
        "#v(9.2cm)",
        '#align(center, text(font: "Arial", size: 20pt)[',
        "  Base Cartográfica Digital \\",
        "  Georreferenciada do Resultado das \\",
        f"  #{_ty(f'Eleições nos {alvo} do Brasil')}",
        "  #v(1cm)",
        f"  #{_ty(str(ano))}",
        "])",
        "#pagebreak()",
        "",
        # Ficha
        "#strong[Centro de Estudos da Metrópole / CEM]",
        "",
        "Acervo cartográfico georreferenciado",
        "",
        "Bases Eleitorais",
        "",
        f"Arquivo: #strong({_ty(f'EL{ano}_{unidade}_BRA_{{{siglas}}}')})",
        "",
        "#rect(width: 100%, stroke: 0.5pt, inset: 6pt)[",
        f"  Formato: GeoPackage e Excel | Tipo: {'Polígonos (áreas)' if municipio else 'Pontos'} | Data: {ano} \\",
        "  Projeção cartográfica: LatLong - SIRGAS 2000 (EPSG:4674)",
        "]",
        "",
        f"Tema: #strong({_ty(f'Eleições {ano}')})",
        "",
        "Base cartográfica: "
        + _ty_inline(f"Municípios do Brasil (malha municipal IBGE {ano_malha})" if municipio
                     else "Locais de votação do Brasil"),
        "",
        "Fonte de dados: " + _ty_inline(fonte),
        "",
        "Descrição: " + _ty_inline(descricao[0]),
        "",
        *[x for p in descricao[1:] for x in (_ty_inline(p), "")],
        _tabela_typst(lista, "(auto, 1fr, auto, auto)", ["left", "left", "center", "right"]),
        "",
    ]
    if atualiza:
        doc += [
            f"#block(sticky: true, above: 1.4em)[#strong({_ty(f'Atualização em relação à base EL{ano}_MU_BRA_CEM (novembro de 2026)')})]",
            "",
            *[f"- {_ty_inline(linha)}" for linha in nota_atualizacao_pacote(ano)],
            "",
        ]
    doc += [
        "#block(sticky: true, above: 1.4em)[#strong[Atributos] (variáveis do banco de dados):]",
        _tabela_typst(atributos, "(auto, 1fr)", ["left", "left"]),
        "",
    ]
    for (cargo, turno), arquivo in arquivos.items():
        cand = pyogrio.read_dataframe(Path(pasta) / f"{arquivo}.gpkg", layer="candidatos")
        sufixo = f" - {turno}º turno" if len(turnos(cargo)) > 1 else ""
        titulo = f"{TITULO_CARGO[CARGOS[cargo]['sigla']]} - Eleição {ano}{sufixo} ({arquivo})"
        doc += [
            f"#block(sticky: true, above: 1.8em)[#strong({_ty(titulo)})]",
            _tabela_candidatos(cand),
            "",
        ]
    doc += [
        # Créditos
        "#pagebreak()",
        "#v(9.5cm)",
        "#set par(justify: false)",
        "#strong[Diretor] \\",
        '#emph("Eduardo Marques")',
        "#v(0.6em)",
        "#strong[Equipe de Transferência de Tecnologia – Centro de Estudos da Metrópole] \\",
        _nomes(equipe),
        "#v(0.6em)",
        "#strong[Conselho Diretor do CEM] \\",
        _nomes(conselho),
        "",
    ]

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if atualiza:
            _logo_antigo_png(_pdf_dicionario_cem_2022(), tmp / "logo_antigo.png")
        else:
            shutil.copyfile(LOGO_CEM, tmp / "logo.svg")
        typ = tmp / f"{Path(destino_pdf).stem}.typ"
        typ.write_text("\n".join(doc), encoding="utf-8")
        subprocess.run(f'quarto typst compile "{typ.name}"', cwd=tmp, check=True, shell=True)
        Path(destino_pdf).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(typ.with_suffix(".pdf"), destino_pdf)
    return Path(destino_pdf)


def _ty_inline(texto: str) -> str:
    """Parágrafo como string Typst inline (`#"..."`)."""
    return "#" + _ty(texto)
