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

Uso:
    python scripts/10_perfil_voto_municipio.py --ano 2022 --cd-mun 2211001
"""

from __future__ import annotations

import argparse
import logging
import sys
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, required=True, choices=[2018, 2022, 2026])
    parser.add_argument("--cd-mun", required=True, help="código IBGE do município (7 dígitos)")
    args = parser.parse_args()
    main(args.ano, args.cd_mun)
