"""Pacote de dados para distribuição: Presidente por local de votação e por município.

Funções chamadas por `13_pacote_dados.qmd`. Os arquivos seguem o padrão de nome do
Download de Dados do CEM (`EL{ano}_{LV|MU}_BRA_PS{turno}`): um GeoPackage e um
`.xlsx` por ano/turno, com os votos de cada candidato em colunas separadas.

Também gera a versão 2 do `EL2022_MU_BRA_CEM` (base de municípios do CEM), com
população, domicílios, área e densidade do Censo 2022 no lugar do Censo 2010.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio

from .config import (
    BRUTO_DIR,
    CARGO_PRESIDENTE,
    CODIGO_ANULADO_APURADO_SEPARADO,
    CODIGO_BRANCO,
    CODIGO_NULO,
    CODIGOS_NAO_CANDIDATO,
    CRS_SIRGAS2000,
    DADOS_DIR,
    GEOMETRIAS_DIR,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
)
from .fontes import TIPO_ELEICAO_ORDINARIA, converte_zip_em_parquet
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

PREFIXO = "EL{ano}_{unidade}_BRA_PS{turno}"


def nome_arquivo(ano: int, turno: int, unidade: str) -> str:
    """`EL2022_LV_BRA_PS1` etc. (`unidade` = `LV` ou `MU`)."""
    return PREFIXO.format(ano=ano, unidade=unidade, turno=turno)


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
    """Colunas de resultado do pacote a partir de um `res_presidente_*` (formato largo).

    Mantém o índice de `res`. Percentuais de 0 a 100.
    """
    nrs = _nrs_candidatos(res)
    votos = {nr: _inteiro(res, f"qt_votos_{nr}") for nr in nrs}
    aptos = _inteiro(res, "qt_aptos")
    comparecimento = _inteiro(res, "qt_comparecimento")
    brancos = _inteiro(res, f"qt_votos_{CODIGO_BRANCO}")
    nulos = _inteiro(res, f"qt_votos_{CODIGO_NULO}")
    anulados = _inteiro(res, f"qt_votos_{CODIGO_ANULADO_APURADO_SEPARADO}")
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
    destino = BRUTO_DIR / str(ano)
    parquet = converte_zip_em_parquet(
        destino / f"consulta_cand_{ano}.zip",
        destino / f"consulta_cand_{ano}_candidatos.parquet",
        (
            "cd_tipo_eleicao", "nr_turno", "sg_uf", "cd_cargo", "nr_candidato", "nm_candidato",
            "nm_urna_candidato", "sg_partido", "nm_partido", "ds_situacao_candidatura",
        ),
        nome_interno=f"consulta_cand_{ano}_BRASIL.csv",
    )
    cand = pd.read_parquet(parquet)
    cand.columns = cand.columns.str.lower()
    cand = cand[cand["cd_tipo_eleicao"].astype(int) == TIPO_ELEICAO_ORDINARIA]
    for col in ("nr_turno", "cd_cargo", "nr_candidato"):
        cand[col] = cand[col].astype(int)
    return cand


def candidatos(ano: int, turno: int, res: pd.DataFrame) -> pd.DataFrame:
    """Tabela `candidatos`: número, nome, nome de urna, partido e votos no Brasil.

    `res` é o resultado por município (com a linha do exterior), para os totais.
    Quando o mesmo número tem mais de um registro (candidatura trocada, como Lula →
    Haddad em 2018), vale o nome que aparece no `votacao_secao`.
    """
    nrs = _nrs_candidatos(res)
    nomes_votacao = {int(nr): nome.upper() for nr, nome in nomes_candidatos(ano, turno).items()}
    cand = _consulta_cand(ano)
    cand = cand[(cand["cd_cargo"] == CARGO_PRESIDENTE) & (cand["nr_turno"] == turno)]

    linhas = []
    for nr in nrs:
        opcoes = cand[cand["nr_candidato"] == nr]
        alvo = nomes_votacao.get(nr, "")
        escolha = opcoes[opcoes["nm_candidato"].str.upper() == alvo]
        if escolha.empty:
            escolha = opcoes[opcoes["ds_situacao_candidatura"].str.upper() == "APTO"]
        if escolha.empty:
            escolha = opcoes
        if escolha.empty:
            linhas.append({"nr_candidato": nr, "nm_candidato": alvo or None})
            continue
        linha = escolha.iloc[0]
        linhas.append({
            "nr_candidato": nr,
            "nm_candidato": linha["nm_candidato"],
            "nm_urna": linha["nm_urna_candidato"],
            "sg_partido": linha["sg_partido"],
            "nm_partido": linha["nm_partido"],
        })
    tabela = pd.DataFrame(linhas)
    votos = pd.Series({nr: _inteiro(res, f"qt_votos_{nr}").sum() for nr in nrs}, dtype="int64")
    tabela["qt_votos"] = tabela["nr_candidato"].map(votos).astype("int64")
    tabela["pc_validos"] = (100 * tabela["qt_votos"] / tabela["qt_votos"].sum()).round(4)
    return tabela


# ==== Contagens e atributos ====================================================


def _municipios_ibge() -> pd.DataFrame:
    mun = pd.DataFrame(
        pyogrio.read_dataframe(
            PROCESSADO_DIR / "aux" / "municipios.gpkg",
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


def censo_municipio() -> pd.DataFrame:
    """População (V0001), domicílios particulares ocupados (V0007) e área por município.

    Soma dos setores do Censo 2022 (tabela Básico). `hab_km2` = população / área.
    """
    # O duckdb recusa o arquivo como latin-1 (bytes de controle); pandas lê só as
    # colunas usadas, direto do zip.
    setores = pd.read_csv(
        CENSO_BASICO_SETOR_ZIP, sep=";", encoding="latin-1", dtype=str,
        usecols=["CD_SETOR", "CD_MUN", "AREA_KM2", "v0001", "v0007"],
    )
    setores["pop"] = pd.to_numeric(setores["v0001"], errors="coerce")
    setores["dom"] = pd.to_numeric(setores["v0007"], errors="coerce")
    setores["area"] = pd.to_numeric(setores["AREA_KM2"].str.replace(",", "."), errors="coerce")
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
MUNICIPIOS_SEM_IBGE = {
    "73709": {"nm_mun": "Boa Esperança do Norte", "sg_uf": "MT", "nm_uf": "Mato Grosso", "nm_regiao": "Centro-Oeste"},
}


def _chave_municipio(cd_mun: pd.Series, cd_mun_tse: pd.Series) -> pd.Series:
    """`cd_mun`; sem ele, `TSE{código}` para os MUNICIPIOS_SEM_IBGE ou EXTERIOR."""
    sem_ibge = cd_mun_tse.where(cd_mun_tse.isin(list(MUNICIPIOS_SEM_IBGE)))
    return cd_mun.fillna(("TSE" + sem_ibge).fillna(SENTINELA_EXTERIOR))


def _separa_sem_ibge_do_exterior(res: pd.DataFrame, ano: int, turno: int) -> pd.DataFrame:
    """Tira da linha EXTERIOR do resultado por município os votos dos
    MUNICIPIOS_SEM_IBGE (somados dos locais) e cria uma linha para cada um.

    Acrescenta a coluna `chave` (`cd_mun`, `TSE{código}` ou EXTERIOR); essas linhas
    ficam com `cd_mun` nulo.
    """
    res = res.copy()
    qt = [c for c in res.columns if c.startswith("qt_")]
    res[qt] = res[qt].astype("float64")
    res["chave"] = res["cd_mun"]
    res_local = pd.read_parquet(RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_local.parquet")
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
    return res


def monta_locais(ano: int, turno: int) -> gpd.GeoDataFrame:
    """Uma linha por local de votação, com o resultado do turno e o ponto."""
    res = pd.read_parquet(RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_local.parquet")
    locais = pd.read_parquet(PROCESSADO_DIR / f"locais_{ano}.parquet")
    geom = gpd.read_file(
        GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}.gpkg",
        columns=["id_local", "fonte_coord", "flag_sem_coord"],
    ).to_crs(CRS_SIRGAS2000)
    por_local, _ = contagens_secao(ano, turno)
    mun = _municipios_ibge()[["cd_mun", "nm_mun"]]

    base = res[["id_local"]].merge(locais, on="id_local", how="left", validate="1:1")
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
    uf = uf.where(~sem_ibge, cd_tse.map(lambda c: MUNICIPIOS_SEM_IBGE.get(c, {}).get("sg_uf")))
    if sem_ibge.any():
        pontos_tse = gpd.points_from_xy(base["longitude"], base["latitude"], crs=CRS_SIRGAS2000)
        base["geometry"] = base["geometry"].where(~sem_ibge, pd.Series(list(pontos_tse), index=base.index))
        base["fonte_coord"] = base["fonte_coord"].where(~sem_ibge, "tse_nao_validada")
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


def monta_municipios(ano: int, turno: int) -> gpd.GeoDataFrame:
    """Uma linha por município (+ exterior sem geometria), com Censo 2022."""
    res = pd.read_parquet(RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_municipio.parquet")
    poligonos = gpd.read_file(PROCESSADO_DIR / "aux" / "municipios.gpkg", columns=["cd_mun"]).to_crs(CRS_SIRGAS2000)
    mun = _municipios_ibge()
    crosswalk = pd.read_parquet(PROCESSADO_DIR / "aux" / "crosswalk_mun_tse_ibge.parquet", columns=["cd_mun", "cd_mun_tse"])
    _, por_mun = contagens_secao(ano, turno)
    censo = censo_municipio()[["cd_mun", "pop_2022", "dom_2022", "area_km2", "hab_km2"]]

    res = _separa_sem_ibge_do_exterior(res, ano, turno)
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

    atributos = base[[
        "cd_mun", "cd_mun_tse", "nm_mun", "sg_uf", "nm_uf", "nm_regiao", "n_zonas", "n_locais",
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
    "reprovada na validação mas a menos de 800 m de outra referência); ano_2018/ano_2022 (coordenada "
    "validada do mesmo local em outra eleição); hidalgo (geocodificação de Hidalgo et al.); geocodebr "
    "(endereço geocodificado com o CNEFE); centroide_municipio; tse_nao_validada (município sem malha do "
    "IBGE de 2022, coordenada do TSE sem validação)",
    "coord_aproximada": "Verdadeiro quando o ponto é o centróide do município (coordenada não resolvida)",
    "latitude": "Latitude do ponto (SIRGAS 2000)",
    "longitude": "Longitude do ponto (SIRGAS 2000)",
    "n_secoes": "Número de seções eleitorais do cadastro do TSE no turno (inclui seções agregadas)",
    # Município
    "cd_mun": "Código IBGE do município (7 dígitos); EXTERIOR para os votos do exterior; vazio para município "
    "criado depois da malha de 2022 (Boa Esperança do Norte, MT)",
    "cd_mun_tse": "Código TSE do município (5 dígitos)",
    "nm_mun": "Nome do município",
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
    "qt_aptos": "Eleitores aptos (TSE, por seção; inclui o voto em trânsito)",
    "qt_comparecimento": "Comparecimento (eleitores que votaram)",
    "qt_abstencoes": "Abstenções (aptos − comparecimento)",
    "qt_validos": "Votos válidos (soma dos votos nos candidatos)",
    "qt_brancos": "Votos em branco",
    "qt_nulos": "Votos nulos",
    "qt_anulados_apu_sep": "Votos anulados e apurados em separado (código 97; só no 1º turno de 2018)",
    "qt_brancos_nulos": "Votos em branco + nulos",
    "pc_comparecimento": "Comparecimento sobre aptos (%)",
    "pc_abstencao": "Abstenção sobre aptos (%)",
    "pc_brancos": "Brancos sobre comparecimento (%)",
    "pc_nulos": "Nulos sobre comparecimento (%)",
    "pc_brancos_nulos": "Brancos + nulos sobre comparecimento (%)",
    "nr_mais_votado": "Número do candidato mais votado",
    "margem_validos": "Diferença entre o 1º e o 2º colocados, em pontos percentuais dos válidos",
}


def dicionario_colunas(colunas: list[str], camada: str) -> pd.DataFrame:
    """Uma linha por coluna da camada, com a descrição."""
    linhas = []
    for coluna in colunas:
        if coluna == "geometry":
            continue
        if coluna.startswith("votos_"):
            descricao = f"Votos no candidato de número {coluna.removeprefix('votos_')} (ver a tabela candidatos)"
        elif coluna.startswith("pc_validos_"):
            descricao = f"Votos no candidato de número {coluna.removeprefix('pc_validos_')} sobre os válidos (%)"
        else:
            descricao = DESCRICOES[coluna]
        linhas.append({"camada": camada, "coluna": coluna, "descricao": descricao})
    return pd.DataFrame(linhas)


def escreve_leiame(destino: Path, arquivos: pd.DataFrame, candidatos_por_turno: dict, dicionario: pd.DataFrame) -> Path:
    """`LEIAME.md` do pacote: conteúdo, colunas, candidatos, observações e citação."""

    def _md(df: pd.DataFrame) -> str:
        cab = "| " + " | ".join(df.columns) + " |"
        sep = "|" + "|".join("---" for _ in df.columns) + "|"
        return "\n".join([cab, sep, *("| " + " | ".join(str(v) for v in linha) + " |" for linha in df.itertuples(index=False))])

    partes = [
        "# Eleições para Presidente — locais de votação (2018, 2022, 2026) e municípios (2026)",
        "",
        "Resultados do 1º e do 2º turno para Presidente, com os votos de cada candidato em colunas "
        "separadas. Complementam as bases eleitorais do Download de Dados do Centro de Estudos da "
        "Metrópole (CEM): os locais de votação cobrem o Brasil inteiro (o CEM tem só o estado de "
        "São Paulo e regiões metropolitanas) e o município de 2026 ainda não existe lá. Os municípios "
        "de 2018 e 2022 estão nas bases `EL2018_MU_BRA_CEM` e `EL2022_MU_BRA_CEM` do CEM, com os "
        "mesmos votos e também os outros cargos.",
        "",
        "## Arquivos",
        "",
        "Um GeoPackage (`.gpkg`) e um Excel (`.xlsx`) por ano e turno, com o mesmo conteúdo. No "
        "GeoPackage, a camada principal (`locais` ou `municipios`) traz a geometria em SIRGAS 2000 "
        "(EPSG:4674) e a tabela `candidatos` traz nome, nome de urna e partido de cada número. No "
        "Excel, as abas são `dados` e `candidatos`.",
        "",
        _md(arquivos),
        "",
        "## Colunas",
        "",
        "Percentuais de 0 a 100. Votos por candidato em `votos_{número}` e `pc_validos_{número}`; "
        "o nome de cada número está na tabela `candidatos` (e abaixo). A lista completa está em "
        "`dicionario_colunas.csv`.",
        "",
        _md(dicionario.drop_duplicates("coluna")[["coluna", "descricao"]]
            .loc[lambda d: ~d["coluna"].str.match(r"^(votos|pc_validos)_\d+$")]),
        "",
        "## Candidatos",
        "",
    ]
    for (ano, turno), cand in sorted(candidatos_por_turno.items()):
        tabela = cand[["nr_candidato", "nm_urna", "sg_partido", "qt_votos", "pc_validos"]].copy()
        tabela["qt_votos"] = tabela["qt_votos"].map(lambda v: f"{v:,}".replace(",", "."))
        tabela["pc_validos"] = tabela["pc_validos"].map(lambda v: f"{v:.2f}".replace(".", ","))
        partes += [f"### {ano}, {turno}º turno", "", _md(tabela), ""]
    partes += [
        "## Observações",
        "",
        "- **Fonte.** TSE (Portal de Dados Abertos): `eleitorado_local_votacao`, `votacao_secao`, "
        "`detalhe_votacao_secao` e `consulta_cand`. Malha municipal: IBGE (via geobr). Censo 2022: "
        "IBGE, " + CENSO_VERSAO + ".",
        "- **Exterior.** Os votos do exterior estão numa linha sem geometria: `cd_mun = EXTERIOR` "
        "no arquivo de municípios e `cd_mun` vazio (`sg_uf = ZZ`) nos locais. Com eles, a soma das "
        "colunas dá o total oficial do Brasil.",
        "- **Boa Esperança do Norte (MT)** foi criado depois da malha municipal do IBGE de 2022: "
        "não tem código IBGE nem polígono. Seus 3 locais trazem a coordenada do TSE (`fonte_coord = "
        "tse_nao_validada`) e, no arquivo de municípios de 2026, ele é uma linha sem geometria.",
        "- **Aptos.** Vêm do TSE por seção (`detalhe_votacao_secao`) e incluem o voto em trânsito; "
        "onde a seção não está nesse arquivo, usa-se o eleitorado do cadastro de locais.",
        "- **Coordenadas.** A coordenada do TSE é validada contra o município; quando falha, usa-se a "
        "coordenada validada do mesmo local em outra eleição, a "
        "geocodificação de Hidalgo et al., depois o endereço geocodificado com o CNEFE, e, em último "
        "caso, o centróide do município (`coord_aproximada`). A coluna `fonte_coord` diz a origem.",
        "- **Seções agregadas.** Uma seção agregada vota no local da sua seção principal; `n_secoes` "
        "conta as seções do cadastro, agregadas incluídas.",
        "- **Números de candidato mudam entre eleições.** Bolsonaro foi 17 (PSL) em 2018 e 22 (PL) "
        "em 2022; em 2026 o 22 é Flávio Bolsonaro. Compare pelo nome da tabela `candidatos`.",
        "- **Código 97** (voto anulado e apurado em separado) só aparece no 1º turno de 2018.",
        "- **Local de votação muda entre turnos** em uma pequena fração das seções; por isso os "
        "arquivos de locais são por turno.",
        "",
        "## Como citar",
        "",
        "Centro de Estudos da Metrópole (CEM/USP). Eleições para Presidente por local de votação "
        "(2018, 2022 e 2026) e por município (2026). Dados do TSE e do IBGE. Novembro de 2026. "
        f"Código: {URL_REPOSITORIO}.",
        "",
        "Licença: Creative Commons Atribuição 4.0 (CC BY 4.0).",
        "",
    ]
    destino = Path(destino)
    destino.write_text("\n".join(partes), encoding="utf-8")
    return destino
