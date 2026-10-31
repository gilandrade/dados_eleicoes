"""Base por urna (seção principal + agregadas): perfil do eleitorado × voto sobre os aptos.

Uma **urna** é a seção principal mais as seções agregadas a ela
(`DS_TIPO_SECAO_AGREGADA`): o voto sai todo na principal, então o cadastro dos
eleitores tem de ser somado por urna, senão a razão perfil/votos passa de 100%
nas agregadas (acontecia em 3,8% das linhas da planilha de 2018).

O denominador é o **eleitor apto** (`QT_APTOS` do `detalhe_votacao_secao`), que
é igual à soma de `QT_ELEITOR_ELEICAO_FEDERAL` do `eleitorado_local_votacao` em
100% das urnas de 2026 (conferido em 31/10/2026). `QT_ELEITOR_SECAO` não serve
para isso: difere em 63% das urnas, por causa de voto em trânsito e presos.

Chave: `id_urna` = `cd_mun_tse` (5) + zona (4) + seção principal (4), texto.
"""

from __future__ import annotations

import gzip
import json
import logging
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from . import fontes
from .config import (
    CODIGO_ANULADO_APURADO_SEPARADO,
    CODIGO_BRANCO,
    CODIGO_NULO,
    NR_BOLSONARO_POR_ANO,
    NR_PT,
    PROCESSADO_DIR,
    UFS_27,
)

logger = logging.getLogger(__name__)

TIPOS_LOCAL_ESPECIAL = ("Preso provisório", "Voto em trânsito")
ESC_POBRES = (1, 2, 3)  # analfabeto, lê e escreve, fundamental incompleto
ROTULOS_ESC = {
    1: "analfabeto",
    2: "le_escreve",
    3: "fund_incompleto",
    4: "fund_completo",
    5: "medio_incompleto",
    6: "medio_completo",
    7: "superior_incompleto",
    8: "superior_completo",
}
REGIAO_POR_DIGITO = {"1": "N", "2": "NE", "3": "SE", "4": "S", "5": "CO"}

URL_SIDRA = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/{tabela}/periodos/{periodo}"
    "/variaveis/{variavel}?localidades=N6[all]"
)
SIDRA_PIB = (5938, 37)  # PIB a preços correntes, mil reais
SIDRA_POPULACAO = (4714, 93)  # Censo 2022

# ==== Seção -> urna ===========================================================


def le_secao_urna(caminho_zip: Path, turno: int) -> pd.DataFrame:
    """Lê o `eleitorado_local_votacao_{ano}.zip` de um turno, uma linha por seção.

    Args:
        caminho_zip: zip do TSE (o de 2026 traz um CSV por UF e o `_BRASIL`; o de
            2018 e 2022 traz um CSV só).
        turno: 1 ou 2 (o local da seção pode mudar entre os turnos).

    Returns:
        Colunas `sg_uf`, `cd_mun_tse`, `nm_municipio`, `nr_zona`, `nr_secao`,
        `nr_secao_urna` (a principal, para as agregadas), `tipo_secao`,
        `nr_local_votacao`, `ds_tipo_local`, `qt_eleitor_secao` e
        `qt_aptos_federal`. As "distribuídas de ofício" (2022) ficam como urna própria.
    """
    colunas = [
        "NR_TURNO", "SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_ZONA", "NR_SECAO",
        "DS_TIPO_SECAO_AGREGADA", "NR_SECAO_PRINCIPAL", "NR_LOCAL_VOTACAO", "DS_TIPO_LOCAL",
        "QT_ELEITOR_SECAO", "QT_ELEITOR_ELEICAO_FEDERAL",
    ]
    with zipfile.ZipFile(caminho_zip) as z:
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        nome = next((n for n in csvs if n.endswith("_BRASIL.csv")), csvs[0])
        with z.open(nome) as f:
            df = pd.read_csv(f, sep=";", encoding="latin-1", dtype=str, usecols=colunas)
    df = df.loc[df["NR_TURNO"] == str(turno)]
    secao = df["NR_SECAO"].astype(int)
    principal = df["NR_SECAO_PRINCIPAL"].astype(int)
    return pd.DataFrame(
        {
            "sg_uf": df["SG_UF"],
            "cd_mun_tse": df["CD_MUNICIPIO"].str.zfill(5),
            "nm_municipio": df["NM_MUNICIPIO"],
            "nr_zona": df["NR_ZONA"].astype(int),
            "nr_secao": secao,
            "nr_secao_urna": secao.where(df["DS_TIPO_SECAO_AGREGADA"] != "Agregada", principal),
            "tipo_secao": df["DS_TIPO_SECAO_AGREGADA"],
            "nr_local_votacao": df["NR_LOCAL_VOTACAO"],
            "ds_tipo_local": df["DS_TIPO_LOCAL"],
            "qt_eleitor_secao": df["QT_ELEITOR_SECAO"].astype(int),
            "qt_aptos_federal": df["QT_ELEITOR_ELEICAO_FEDERAL"].astype(int),
        }
    ).reset_index(drop=True)


def monta_urnas(secoes: pd.DataFrame) -> pd.DataFrame:
    """Soma as seções de cada urna; local e tipo de local vêm da principal."""
    chave = ["cd_mun_tse", "nr_zona", "nr_secao_urna"]
    principais = secoes.loc[secoes["nr_secao"] == secoes["nr_secao_urna"]]
    base = principais[[*chave, "sg_uf", "nm_municipio", "nr_local_votacao", "ds_tipo_local", "tipo_secao"]]
    base = base.drop_duplicates(chave)
    soma = secoes.groupby(chave, as_index=False).agg(
        n_secoes=("nr_secao", "size"),
        qt_eleitor_secao=("qt_eleitor_secao", "sum"),
        qt_aptos_federal=("qt_aptos_federal", "sum"),
    )
    urnas = base.merge(soma, on=chave, how="outer", validate="one_to_one")
    assert urnas["sg_uf"].notna().all(), "urna sem seção principal"
    urnas = urnas.rename(columns={"nr_secao_urna": "nr_secao"})
    urnas["id_urna"] = (
        urnas["cd_mun_tse"]
        + urnas["nr_zona"].astype(str).str.zfill(4)
        + urnas["nr_secao"].astype(str).str.zfill(4)
    )
    return urnas


# ==== Votos por urna ==========================================================


def le_votos_urna(ano: int, turno: int) -> pd.DataFrame:
    """Votos do Presidente por seção principal, com aptos e comparecimento do TSE.

    Usa `fontes.obtem_resultados_secao` (que lê o `detalhe_votacao_secao` se
    estiver em disco, para `qt_aptos` real por seção).

    Returns:
        Uma linha por `(cd_mun_tse, nr_zona, nr_secao)` com `qt_pt`, `qt_bolso`,
        `qt_outros` (candidatos além dos dois; 0 no 2º turno), `qt_brancos`,
        `qt_nulos` (nulos + anulados e apurados em separado), `qt_aptos` e
        `qt_comparecimento`.
    """
    longo = fontes.obtem_resultados_secao(ano, turno, "votacao_secao", False)
    nr = longo["nr_votavel"]
    nr_bolso = NR_BOLSONARO_POR_ANO[ano]
    grupo = np.select(
        [
            nr == NR_PT,
            nr == nr_bolso,
            nr == CODIGO_BRANCO,
            nr.isin([CODIGO_NULO, CODIGO_ANULADO_APURADO_SEPARADO]),
        ],
        ["qt_pt", "qt_bolso", "qt_brancos", "qt_nulos"],
        default="qt_outros",
    )
    chave = ["cd_mun_tse", "nr_zona", "nr_secao"]
    votos = (
        longo.assign(grupo=grupo)
        .pivot_table(index=chave, columns="grupo", values="qt_votos", aggfunc="sum", fill_value=0)
        .reindex(columns=["qt_pt", "qt_bolso", "qt_outros", "qt_brancos", "qt_nulos"], fill_value=0)
    )
    totais = longo.groupby(chave)[["qt_aptos", "qt_comparecimento"]].first()
    res = votos.join(totais).reset_index()
    res.columns.name = None
    return res


# ==== Perfil por urna =========================================================


def le_perfil_brasil(
    ano: int, zips_dir: Path, cache_dir: Path, tmp_dir: Path, ufs: list[str] | None = None
) -> pd.DataFrame:
    """Lê o perfil do eleitorado de todas as UFs, uma linha por seção.

    Extrai o CSV de uma UF por vez para `tmp_dir`, grava um parquet por UF em
    `cache_dir` e apaga o CSV (o de SP passa de vários GB descompactado). Pula
    as UFs que já têm parquet, então pode ser interrompido e retomado.

    Args:
        ano: ano do cadastro.
        zips_dir: pasta com `perfil_eleitor_secao_{ano}_{UF}.zip` (27 UFs).
        cache_dir: onde guardar `perfil_secao_{ano}_{UF}.parquet`.
        tmp_dir: pasta temporária, de preferência fora do OneDrive.
        ufs: restringe a algumas UFs (teste); `None` lê as 27.

    Returns:
        Saída de `fontes.ler_perfil_secao_largo`, as 27 UFs juntas.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    partes = []
    for uf in ufs or UFS_27:
        parquet = cache_dir / f"perfil_secao_{ano}_{uf}.parquet"
        if not parquet.exists():
            zip_uf = zips_dir / f"perfil_eleitor_secao_{ano}_{uf}.zip"
            csv_uf = tmp_dir / f"perfil_eleitor_secao_{ano}_{uf}.csv"
            logger.info("Perfil %s %s...", ano, uf)
            fontes.extrai_csv_do_zip(zip_uf, csv_uf, nome_interno=f"perfil_eleitor_secao_{ano}_{uf}.csv")
            try:
                fontes.ler_perfil_secao_largo(csv_uf).to_parquet(parquet, index=False)
            finally:
                csv_uf.unlink(missing_ok=True)
        partes.append(pd.read_parquet(parquet))
    return pd.concat(partes, ignore_index=True)


def soma_perfil_por_urna(
    perfil_secao: pd.DataFrame, secoes: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Soma o perfil das seções de cada urna (principal + agregadas).

    Returns:
        `(perfil_urna, sem_urna)`: o perfil somado por `(cd_mun_tse, nr_zona,
        nr_secao)` da principal, e as seções do perfil que não existem no
        eleitorado do turno (para contar e reportar, não descartar em silêncio).
    """
    chave = ["cd_mun_tse", "nr_zona", "nr_secao"]
    ligacao = secoes[[*chave, "nr_secao_urna"]]
    m = perfil_secao.merge(ligacao, on=chave, how="left", validate="one_to_one")
    sem_urna = m.loc[m["nr_secao_urna"].isna(), [*chave, "qt_eleitores_perfil"]]
    m = m.dropna(subset=["nr_secao_urna"]).copy()
    m["nr_secao"] = m["nr_secao_urna"].astype(int)
    contagens = [c for c in m.columns if c.startswith("qt_")]
    soma = m.groupby(chave, as_index=False)[contagens].sum()
    return soma, sem_urna


# ==== Indicadores =============================================================


def calcula_indicadores_urna(df: pd.DataFrame) -> pd.DataFrame:
    """Percentuais (0–100) sobre os válidos e sobre os aptos, e a medida `pobres`.

    `pobres` segue a planilha de 2018: (analfabeto + lê e escreve + fundamental
    incompleto) / eleitores com escolaridade informada (1 a 8) × 100.
    As quatro partes sobre os aptos (PT, bolsonarista, branco/nulo, abstenção)
    somam 100.
    """
    d = df.copy()
    esc = [f"qt_esc_{c}" for c in ROTULOS_ESC]
    d["qt_perfil_esc"] = d[esc].sum(axis=1)
    informado = d["qt_perfil_esc"].replace(0, np.nan)
    d["pobres"] = 100 * d[[f"qt_esc_{c}" for c in ESC_POBRES]].sum(axis=1) / informado
    for c, nome in ROTULOS_ESC.items():
        d[f"pc_{nome}"] = 100 * d[f"qt_esc_{c}"] / informado

    d["qt_validos"] = d["qt_pt"] + d["qt_bolso"] + d["qt_outros"]
    d["qt_abstencao"] = d["qt_aptos"] - d["qt_comparecimento"]
    d["qt_brancos_nulos"] = d["qt_brancos"] + d["qt_nulos"]
    validos = d["qt_validos"].replace(0, np.nan)
    aptos = d["qt_aptos"].replace(0, np.nan)
    d["pc_validos_pt"] = 100 * d["qt_pt"] / validos
    d["pc_validos_bolso"] = 100 * d["qt_bolso"] / validos
    d["pc_aptos_pt"] = 100 * d["qt_pt"] / aptos
    d["pc_aptos_bolso"] = 100 * d["qt_bolso"] / aptos
    d["pc_aptos_branco_nulo"] = 100 * d["qt_brancos_nulos"] / aptos
    d["pc_aptos_abstencao"] = 100 * d["qt_abstencao"] / aptos
    d["pc_comparecimento"] = 100 * d["qt_comparecimento"] / aptos

    d["razao_cadastro_aptos"] = d["qt_eleitores_perfil"] / aptos
    perfil = d["qt_eleitores_perfil"].replace(0, np.nan)
    d["pc_facultativo"] = 100 * d["qt_facultativo"] / perfil
    genero = (d["qt_genero_feminino"] + d["qt_genero_masculino"]).replace(0, np.nan)
    d["pc_feminino"] = 100 * d["qt_genero_feminino"] / genero
    d["idade_media"] = d["qt_idade_soma"] / d["qt_idade_valida"].replace(0, np.nan)
    for faixa in fontes.FAIXAS_IDADE_PERFIL:
        d[f"pc_idade_{faixa}"] = 100 * d[f"qt_idade_{faixa}"] / d["qt_idade_valida"].replace(0, np.nan)
    return d


def marca_exclusoes(df: pd.DataFrame) -> pd.DataFrame:
    """Coluna `motivo_exclusao` (vazia = entra na análise); o primeiro motivo vence."""
    motivos = [
        (df["sg_uf"] == "ZZ", "exterior"),
        (df["ds_tipo_local"].isin(TIPOS_LOCAL_ESPECIAL), "local especial (trânsito ou preso provisório)"),
        (df["tipo_secao"].str.startswith("Distribu"), "distribuída de ofício (sem aptos)"),
        (df["qt_comparecimento"].isna(), "urna sem resultado (não instalada)"),
        (df["qt_aptos"].fillna(0) <= 0, "sem aptos"),
        (df["qt_eleitores_perfil"].fillna(0) <= 0, "sem perfil no cadastro"),
    ]
    d = df.copy()
    d["motivo_exclusao"] = ""
    for cond, rotulo in reversed(motivos):
        d.loc[cond, "motivo_exclusao"] = rotulo
    return d


def adiciona_regiao(df: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Junta `cd_mun` (IBGE, 7 dígitos) e a região (N, NE, SE, S, CO) pelo crosswalk TSE↔IBGE."""
    cw = crosswalk[["cd_mun_tse", "cd_mun"]].drop_duplicates("cd_mun_tse")
    d = df.merge(cw, on="cd_mun_tse", how="left", validate="many_to_one")
    d["regiao"] = d["cd_mun"].str[:1].map(REGIAO_POR_DIGITO)
    return d


# ==== PIB per capita ==========================================================


def _le_sidra(tabela: int, variavel: int, periodo: int) -> pd.Series:
    url = URL_SIDRA.format(tabela=tabela, variavel=variavel, periodo=periodo)
    req = urllib.request.Request(url, headers={"User-Agent": "dados_eleicoes (CEM/USP)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        corpo = r.read()
    if corpo[:2] == bytes([0x1F, 0x8B]):  # a API as vezes responde comprimido, sem avisar
        corpo = gzip.decompress(corpo)
    dados = json.loads(corpo.decode("utf-8"))
    series = dados[0]["resultados"][0]["series"]
    valores = {s["localidade"]["id"]: s["serie"][str(periodo)] for s in series}
    return pd.to_numeric(pd.Series(valores), errors="coerce")


def le_pib_per_capita(ano_pib: int, cache_dir: Path = PROCESSADO_DIR / "aux") -> pd.DataFrame:
    """PIB municipal (IBGE, SIDRA 5938) dividido pela população do Censo 2022 (SIDRA 4714).

    A população é a de 2022 mesmo para o PIB de 2023: o Censo é a única tabela
    municipal com todos os municípios, e o ranking dos municípios muda pouco.
    Guarda o resultado em `cache_dir/pib_per_capita_{ano_pib}.parquet`.

    Returns:
        `cd_mun` (7 dígitos), `pib_mil_reais`, `populacao` e `pib_per_capita` (R$).
    """
    caminho = cache_dir / f"pib_per_capita_{ano_pib}.parquet"
    if caminho.exists():
        return pd.read_parquet(caminho)
    pib = _le_sidra(*SIDRA_PIB, ano_pib)
    pop = _le_sidra(*SIDRA_POPULACAO, 2022)
    df = pd.DataFrame({"pib_mil_reais": pib, "populacao": pop}).rename_axis("cd_mun").reset_index()
    df["cd_mun"] = df["cd_mun"].astype(str)
    df["pib_per_capita"] = 1000 * df["pib_mil_reais"] / df["populacao"]
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(caminho, index=False)
    return df


# ==== Planilha de 2018 (Simoni Jr.) ===========================================


def le_planilha_2018(caminho_xlsx: Path, cache: Path | None = None) -> pd.DataFrame:
    """Lê `elei_18_2t_escolaridade.xlsx` (uma linha por seção, 448.827 linhas).

    Lê o XML da aba direto (openpyxl leva muito mais tempo nos 364 MB de XML) e
    guarda um parquet em `cache`. Os percentuais `haddad`/`bolso` e `pobres` são
    fórmulas na planilha; o Excel guarda o último valor calculado, que é o lido.

    Returns:
        `sg_uf`, `cd_mun_tse` (texto), `nr_zona`, `nr_local`, `nr_secao`,
        `qt_validos`, `qt_13`, `haddad`, `qt_17`, `bolso`, `qt_perfil`, `qt_esc_1..8`
        e `pobres`.
    """
    if cache is not None and cache.exists():
        return pd.read_parquet(cache)
    import re
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    col = re.compile(r"[A-Z]+")
    with zipfile.ZipFile(caminho_xlsx) as z:
        textos = [
            "".join(t.text or "" for t in si.iter(ns + "t"))
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(ns + "si")
        ]
        linhas = []
        with z.open("xl/worksheets/sheet1.xml") as f:
            for _, el in ET.iterparse(f, events=("end",)):
                if el.tag != ns + "row":
                    continue
                if el.get("r") != "1":
                    d = {}
                    for c in el.findall(ns + "c"):
                        v = c.find(ns + "v")
                        if v is None:
                            continue
                        d[col.match(c.get("r")).group()] = textos[int(v.text)] if c.get("t") == "s" else v.text
                    if d.get("A"):
                        linhas.append(d)
                el.clear()
    df = pd.DataFrame(linhas)
    nomes = {
        "A": "sg_uf", "B": "cd_mun_tse", "D": "nr_zona", "E": "nr_local", "F": "nr_secao",
        "G": "qt_validos", "H": "qt_13", "I": "haddad", "J": "qt_17", "K": "bolso", "L": "qt_perfil",
        **{chr(ord("M") + i): f"qt_esc_{i + 1}" for i in range(8)},
        "U": "pobres",
    }
    df = df[list(nomes)].rename(columns=nomes)
    df["cd_mun_tse"] = df["cd_mun_tse"].str.zfill(5)
    for c in df.columns.difference(["sg_uf", "cd_mun_tse"]):
        df[c] = pd.to_numeric(df[c])
    # célula vazia = zero (é assim que a fórmula de `pobres` do Excel a trata)
    contagens = [c for c in df.columns if c.startswith("qt_")]
    df[contagens] = df[contagens].fillna(0)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache, index=False)
    return df
