#!/usr/bin/env python
"""Etapa 10 — perfil do eleitorado x voto por partido, para um município.

Cruza o cadastro do TSE (escolaridade, idade, gênero), a renda do Censo 2022 e
os votos por partido de Presidente e Deputado Federal. Gera, em
`dados/04_analise/{cd_mun}/`:

  - `perfil_voto_local_{ano}.parquet`: por local de votação. Voto e cadastro
    são os publicados pelo TSE (observados); a renda é a dos setores da área
    estimada do local.
  - `perfil_voto_setor_{ano}.parquet`: por setor censitário. Voto e cadastro
    são estimados pela matriz de pesos; a renda é a observada no Censo.
  - `partidos_{ano}.parquet`: número e sigla dos partidos.

As colunas de cada disputa levam o prefixo dela (`pres_t1_`, `pres_t2_`,
`depfed_t1_`), e o número em `qt_votos_{nr}`/`pc_validos_{nr}` é o do partido.

Antes: `scripts/09_insumos_municipio.R` e `scripts/03_pesos_alocacao.py --cd-mun`.

Sem `--cd-mun`, roda o país inteiro, com a zona × município como unidade
(`main_brasil`): voto e escolaridade são observados (soma dos locais da zona) e
só a renda é estimada, pelos pesos nacionais de `03_pesos_alocacao.py`. Precisa
de `pesos_local_setor_{ano}.parquet` e `censo_setor_BR.parquet`
(`scripts/09_censo_setor_brasil.R`), e dos zips de perfil e votos de todas as UFs.

Uso:
    python scripts/10_perfil_voto_municipio.py --ano 2022 --cd-mun 2211001
    python scripts/10_perfil_voto_municipio.py --ano 2022 --zips-dir D:/tse2022
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
import pandas as pd

from eleicoes import fontes, perfil, resultados
from eleicoes.config import (
    ANALISE_DIR,
    BRUTO_DIR,
    CARGO_DEPUTADO_FEDERAL,
    CARGO_PRESIDENTE,
    CODIGOS_NAO_CANDIDATO,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# (prefixo das colunas, cargo, turno, abrangência do arquivo de votos). O
# votacao_secao da UF só tem os cargos da eleição estadual; Presidente só
# existe no arquivo _BR.
DISPUTAS = [
    ("pres_t1", CARGO_PRESIDENTE, 1, "BR"),
    ("pres_t2", CARGO_PRESIDENTE, 2, "BR"),
    ("depfed_t1", CARGO_DEPUTADO_FEDERAL, 1, "UF"),
]

COLUNAS_RENDA = ["renda_media_resp", "renda_mediana_resp"]

UFS_27 = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
]


def csv_do_municipio(nome_zip: str, ano: int, cd_mun: str, cd_mun_tse: str) -> Path:
    """Devolve o CSV com as linhas do município, extraindo do zip se preciso."""
    caminho_zip = BRUTO_DIR / str(ano) / f"{nome_zip}.zip"
    caminho_csv = BRUTO_DIR / str(ano) / f"{nome_zip}_{cd_mun}.csv"
    if caminho_csv.exists():
        return caminho_csv
    if not caminho_zip.exists():
        raise FileNotFoundError(
            f"{caminho_zip} não existe — rode scripts/09_insumos_municipio.R {cd_mun} {ano}."
        )
    logger.info("Extraindo o município de %s...", caminho_zip.name)
    n_linhas = fontes.extrai_municipio_do_zip(
        caminho_zip, caminho_csv, cd_mun_tse, nome_interno=f"{nome_zip}.csv"
    )
    logger.info("%d linhas.", n_linhas)
    return caminho_csv


def carrega_partidos(ano: int) -> pd.DataFrame:
    """Número e sigla dos partidos, de `consulta_cand_{ano}_BRASIL.csv`."""
    caminho_zip = BRUTO_DIR / str(ano) / f"consulta_cand_{ano}.zip"
    caminho_csv = BRUTO_DIR / str(ano) / f"consulta_cand_{ano}_BRASIL.csv"
    if not caminho_csv.exists():
        with zipfile.ZipFile(caminho_zip) as z:
            z.extract(caminho_csv.name, caminho_csv.parent)
    return duckdb.sql(
        f"""
        select distinct cast(nr_partido as integer) as nr_partido, sg_partido as sg_partido
        from {fontes._sql_read_csv(caminho_csv)}
        order by nr_partido
        """
    ).df()


def resultados_da_disputa(
    caminho_votos: Path,
    ano: int,
    turno: int,
    cd_cargo: int,
    secao_local: pd.DataFrame,
    locais: pd.DataFrame,
    pesos: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Votos por partido de uma disputa, por local (observado) e por setor (estimado)."""
    votos = fontes.ler_votacao_secao(caminho_votos, ano, turno, cd_cargo=cd_cargo)
    if votos.empty:
        return None
    votos = resultados.agrega_por_partido(votos)

    com_id = resultados.secao_para_id_local(votos, secao_local)
    n_sem_local = com_id["id_local"].isna().sum()
    if n_sem_local:
        logger.warning("%d linhas de voto sem id_local.", n_sem_local)

    partidos = sorted(
        nr for nr in com_id["nr_votavel"].unique() if nr not in CODIGOS_NAO_CANDIDATO
    )
    cols_validos = [f"qt_votos_{nr}" for nr in partidos]

    res_local = resultados.agrega_por_local(com_id, locais)
    res_setor = resultados.agrega_por_setor(res_local, pesos)
    logger.info(
        "  comparecimento %d | válidos %d | no setor %.1f%% dos válidos",
        res_local["qt_comparecimento"].sum(),
        res_local[cols_validos].sum().sum(),
        100 * res_setor[cols_validos].sum().sum() / res_local[cols_validos].sum().sum(),
    )
    return (
        resultados.calcula_indicadores(res_local, cols_votos_validos=cols_validos),
        resultados.calcula_indicadores(res_setor, cols_votos_validos=cols_validos),
    )


def com_prefixo(df: pd.DataFrame, prefixo: str, chave: str) -> pd.DataFrame:
    return df.rename(columns={c: f"{prefixo}_{c}" for c in df.columns if c != chave})


def renda_da_area_do_local(pesos: pd.DataFrame, censo_setor: pd.DataFrame) -> pd.DataFrame:
    """Renda dos setores atendidos por cada local, ponderada pelos adultos
    que o setor manda para ele. É o caminho inverso do da tabela por setor."""
    base = pesos.merge(censo_setor[["cd_setor", *COLUNAS_RENDA]], on="cd_setor", how="inner")
    partes = {}
    for col in COLUNAS_RENDA:
        validos = base.dropna(subset=[col])
        soma = (validos[col] * validos["adultos"]).groupby(validos["id_local"]).sum()
        partes[f"{col}_area"] = soma / validos.groupby("id_local")["adultos"].sum()
    return pd.DataFrame(partes).reset_index()


def main(ano: int, cd_mun: str) -> None:
    crosswalk = pd.read_parquet(PROCESSADO_DIR / "aux" / "crosswalk_mun_tse_ibge.parquet")
    municipio = crosswalk.loc[crosswalk["cd_mun"] == cd_mun].iloc[0]
    cd_mun_tse, sg_uf = municipio["cd_mun_tse"], municipio["sg_uf"]
    logger.info("Município %s (%s/%s), ano %d", cd_mun, municipio["nm_mun_ibge"], sg_uf, ano)

    secao_local = pd.read_parquet(PROCESSADO_DIR / f"secao_local_{ano}.parquet")
    locais = pd.read_parquet(PROCESSADO_DIR / f"locais_{ano}.parquet")
    locais = locais.loc[locais["cd_mun"] == cd_mun]
    pesos = pd.read_parquet(RESULTADOS_DIR / f"pesos_local_setor_{ano}_{cd_mun}.parquet")
    censo_setor = pd.read_parquet(PROCESSADO_DIR / "aux" / f"censo_setor_{cd_mun}.parquet")

    # ==== Perfil do cadastro ================================================
    caminho_perfil = csv_do_municipio(
        f"perfil_eleitor_secao_{ano}_{sg_uf}", ano, cd_mun, cd_mun_tse
    )
    perfil_local = perfil.agrega_perfil_por_local(
        fontes.ler_perfil_eleitor_secao(caminho_perfil), secao_local
    )
    perfil_setor = resultados.agrega_por_setor(perfil_local, pesos)
    logger.info(
        "Perfil: %d eleitores em %d locais (%d inscritos em locais_%d).",
        perfil_local["qt_eleitores_perfil"].sum(), len(perfil_local),
        locais["qt_eleitores"].sum(), ano,
    )

    tabela_local = (
        locais[["id_local", "nr_zona", "nm_local_votacao", "flag_especial", "qt_eleitores"]]
        .merge(perfil.calcula_indicadores_perfil(perfil_local), on="id_local", how="left")
        .merge(renda_da_area_do_local(pesos, censo_setor), on="id_local", how="left")
    )
    # Local que mais manda adultos para o setor: agrupa o erro padrão na
    # regressão por setor (setores do mesmo local repetem o mesmo voto).
    dominante = pesos.loc[pesos.groupby("cd_setor")["adultos"].idxmax()]
    tabela_setor = (
        censo_setor
        .merge(
            dominante[["cd_setor", "id_local"]].rename(columns={"id_local": "id_local_dominante"}),
            on="cd_setor", how="left",
        )
        .merge(perfil.calcula_indicadores_perfil(perfil_setor), on="cd_setor", how="left")
    )

    # ==== Votos por partido =================================================
    for prefixo, cd_cargo, turno, abrangencia in DISPUTAS:
        logger.info("Disputa %s...", prefixo)
        sufixo = "BR" if abrangencia == "BR" else sg_uf
        caminho_votos = csv_do_municipio(
            f"votacao_secao_{ano}_{sufixo}", ano, cd_mun, cd_mun_tse
        )
        res = resultados_da_disputa(
            caminho_votos, ano, turno, cd_cargo, secao_local, locais, pesos
        )
        if res is None:
            logger.info("  sem votos (não houve esse turno).")
            continue
        res_local, res_setor = res
        tabela_local = tabela_local.merge(
            com_prefixo(res_local, prefixo, "id_local"), on="id_local", how="left"
        )
        tabela_setor = tabela_setor.merge(
            com_prefixo(res_setor, prefixo, "cd_setor"), on="cd_setor", how="left"
        )

    destino = ANALISE_DIR / cd_mun
    destino.mkdir(parents=True, exist_ok=True)
    tabela_local.to_parquet(destino / f"perfil_voto_local_{ano}.parquet", index=False)
    tabela_setor.to_parquet(destino / f"perfil_voto_setor_{ano}.parquet", index=False)
    carrega_partidos(ano).to_parquet(destino / f"partidos_{ano}.parquet", index=False)
    logger.info(
        "Concluído: %d locais e %d setores em %s.", len(tabela_local), len(tabela_setor), destino
    )


# ==== País inteiro: unidade = zona x município ================================


def extrai_csv(caminho_zip: Path, nome_interno: str, pasta: Path) -> Path:
    """Extrai um CSV de um zip do TSE para `pasta` (em disco, fora do OneDrive)."""
    destino = pasta / nome_interno
    with zipfile.ZipFile(caminho_zip) as z, z.open(nome_interno) as origem, open(destino, "wb") as saida:
        shutil.copyfileobj(origem, saida, length=1 << 24)
    return destino


def id_zona_mun(locais: pd.DataFrame) -> pd.Series:
    """`id_zona_mun` de cada local, com a mesma regra de `resultados.agrega_por_zona_mun`."""
    return (
        locais["cd_mun"].fillna(resultados.SENTINELA_EXTERIOR).astype(str)
        + locais["nr_zona"].astype(int).astype(str).str.zfill(4)
    )


def resultados_zona_mun(
    votos: pd.DataFrame, secao_local: pd.DataFrame, locais: pd.DataFrame
) -> pd.DataFrame:
    """Votos por partido de uma disputa, somados por zona × município (observado).

    `votos` já vem somado por partido (`ler_votacao_secao(..., por_partido=True)`).
    """
    com_id = resultados.secao_para_id_local(votos, secao_local)
    return resultados.agrega_por_zona_mun(com_id, locais)


def resultados_com_indicadores(res: pd.DataFrame, prefixo: str) -> pd.DataFrame:
    """Indicadores de uma disputa por zona × município, com o prefixo dela nas colunas."""
    cols_validos = sorted(
        (
            c for c in res.columns
            if c.startswith("qt_votos_")
            and int(c.removeprefix("qt_votos_")) not in CODIGOS_NAO_CANDIDATO
        ),
        key=lambda c: int(c.removeprefix("qt_votos_")),
    )
    res = resultados.calcula_indicadores(
        res.fillna({c: 0 for c in cols_validos}), cols_votos_validos=cols_validos
    )
    return com_prefixo(res, prefixo, "id_zona_mun")


def main_brasil(ano: int, zips_dir: Path, tmp_dir: Path) -> None:
    inicio = time.time()

    def marca(texto: str) -> None:
        logger.info("[%5.1f min] %s", (time.time() - inicio) / 60, texto)

    tmp_dir.mkdir(parents=True, exist_ok=True)
    crosswalk = pd.read_parquet(PROCESSADO_DIR / "aux" / "crosswalk_mun_tse_ibge.parquet")
    secao_local = pd.read_parquet(PROCESSADO_DIR / f"secao_local_{ano}.parquet")
    locais = pd.read_parquet(PROCESSADO_DIR / f"locais_{ano}.parquet")
    pesos = pd.read_parquet(RESULTADOS_DIR / f"pesos_local_setor_{ano}.parquet")
    censo_setor = pd.read_parquet(PROCESSADO_DIR / "aux" / "censo_setor_BR.parquet")
    locais["id_zona_mun"] = id_zona_mun(locais)
    marca(f"{len(locais)} locais, {len(pesos)} linhas de peso, {len(censo_setor)} setores no Censo")

    # ==== Perfil do cadastro, UF por UF (um CSV extraído de cada vez) =========
    partes = []
    for uf in UFS_27:
        nome = f"perfil_eleitor_secao_{ano}_{uf}"
        caminho_csv = extrai_csv(zips_dir / f"{nome}.zip", f"{nome}.csv", tmp_dir)
        try:
            perfil_secao = fontes.ler_perfil_eleitor_secao(caminho_csv)
        finally:
            caminho_csv.unlink()
        partes.append(perfil.agrega_perfil_por_local(perfil_secao, secao_local))
        marca(f"perfil {uf}: {perfil_secao['qt_eleitores'].sum():,.0f} eleitores")
    perfil_local = pd.concat(partes, ignore_index=True)

    por_zona = (
        perfil_local.merge(locais[["id_local", "id_zona_mun"]], on="id_local", how="left")
        .drop(columns="id_local")
        .groupby("id_zona_mun")
        .sum()
        .reset_index()
    )
    tabela = perfil.calcula_indicadores_perfil(por_zona)

    # ==== Renda: dos setores à área de cada local, e dos locais à zona =========
    renda_local = renda_da_area_do_local(pesos, censo_setor)
    adultos = pesos.groupby("id_local")["adultos"].sum().rename("adultos").reset_index()
    base = renda_local.merge(adultos, on="id_local").merge(
        locais[["id_local", "id_zona_mun"]], on="id_local"
    )
    renda_zona = {}
    for col in COLUNAS_RENDA:
        ok = base.dropna(subset=[f"{col}_area"])
        soma = (ok[f"{col}_area"] * ok["adultos"]).groupby(ok["id_zona_mun"]).sum()
        renda_zona[f"{col}_area"] = soma / ok.groupby("id_zona_mun")["adultos"].sum()
    tabela = tabela.merge(pd.DataFrame(renda_zona).reset_index(), on="id_zona_mun", how="left")
    marca("perfil e renda por zona prontos")

    # ==== Votos por partido ====================================================
    csv_br = extrai_csv(zips_dir / f"votacao_secao_{ano}_BR.zip", f"votacao_secao_{ano}_BR.csv", tmp_dir)
    try:
        for prefixo, cd_cargo, turno, abrangencia in DISPUTAS:
            if abrangencia != "BR":
                continue
            votos = fontes.ler_votacao_secao(
                csv_br, ano, turno, cd_cargo=cd_cargo, por_partido=True
            )
            res = resultados_zona_mun(votos, secao_local, locais)
            marca(f"{prefixo}: {len(votos):,} linhas, comparecimento {res['qt_comparecimento'].sum():,.0f}")
            tabela = tabela.merge(
                resultados_com_indicadores(res, prefixo), on="id_zona_mun", how="left"
            )
    finally:
        csv_br.unlink()

    for prefixo, cd_cargo, turno, abrangencia in DISPUTAS:
        if abrangencia != "UF":
            continue
        partes_uf = []
        for uf in UFS_27:
            nome = f"votacao_secao_{ano}_{uf}"
            caminho_csv = extrai_csv(zips_dir / f"{nome}.zip", f"{nome}.csv", tmp_dir)
            try:
                votos = fontes.ler_votacao_secao(
                    caminho_csv, ano, turno, cd_cargo=cd_cargo, por_partido=True
                )
            finally:
                caminho_csv.unlink()
            partes_uf.append(resultados_zona_mun(votos, secao_local, locais))
            marca(f"{prefixo} {uf}: {len(votos):,} linhas")
        todos = pd.concat(partes_uf, ignore_index=True)
        colunas_qt = [c for c in todos.columns if c.startswith("qt_")]
        # uma zona não cruza UF, mas cada UF tem o seu conjunto de partidos
        res = todos.groupby("id_zona_mun")[colunas_qt].sum(min_count=1).reset_index()
        tabela = tabela.merge(
            resultados_com_indicadores(res, prefixo), on="id_zona_mun", how="left"
        )

    # ==== Identificação e saída (o exterior fica de fora: não é território) ====
    com_mun = locais.dropna(subset=["cd_mun"])
    n_zonas = com_mun.drop_duplicates(["cd_mun", "nr_zona"]).groupby("cd_mun").size()
    ident = (
        com_mun.groupby("id_zona_mun")
        .agg(cd_mun=("cd_mun", "first"), nr_zona=("nr_zona", "first"), n_locais=("id_local", "size"))
        .reset_index()
        .merge(crosswalk[["cd_mun", "sg_uf", "nm_mun_ibge"]], on="cd_mun", how="left")
    )
    ident["n_zonas_mun"] = ident["cd_mun"].map(n_zonas)
    tabela = ident.merge(tabela, on="id_zona_mun", how="left")

    destino = ANALISE_DIR / "BR"
    destino.mkdir(parents=True, exist_ok=True)
    tabela.to_parquet(destino / f"perfil_voto_zona_mun_{ano}.parquet", index=False)
    carrega_partidos(ano).to_parquet(destino / f"partidos_{ano}.parquet", index=False)
    marca(f"Concluído: {len(tabela)} zonas × município em {destino}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, required=True, choices=[2018, 2022, 2026])
    parser.add_argument(
        "--cd-mun", default=None,
        help="código IBGE do município (7 dígitos); sem ele, roda o país inteiro por zona × município",
    )
    parser.add_argument(
        "--zips-dir", type=Path, default=None,
        help="(país) pasta com os zips de perfil e votos das 27 UFs e o votacao_secao_{ano}_BR.zip",
    )
    parser.add_argument(
        "--tmp-dir", type=Path, default=None,
        help="(país) pasta para os CSVs extraídos, um por vez (padrão: temporária do sistema)",
    )
    args = parser.parse_args()
    if args.cd_mun:
        main(args.ano, args.cd_mun)
    else:
        main_brasil(
            args.ano,
            args.zips_dir or BRUTO_DIR / str(args.ano),
            args.tmp_dir or Path(tempfile.gettempdir()) / "dados_eleicoes_tmp",
        )
