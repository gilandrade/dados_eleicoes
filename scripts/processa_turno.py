#!/usr/bin/env python
"""Etapas 04–05 — resultados de um turno: local -> zona x município -> setor -> export.

Uso:
    python scripts/processa_turno.py --ano 2026 --turno 1 --fonte bweb --baixar
    python scripts/processa_turno.py --ano 2022 --turno 1 --fonte votacao_secao

A matriz de pesos (`pesos_local_setor_{ano}.parquet`) já deve existir — ela é
calculada uma vez por ano por `scripts/03_pesos_alocacao.py`, bem antes da
eleição. Este script só lê, agrega e escreve; não recalcula nada espacial.

Ver PLAN.md > Noite da eleição.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from eleicoes import exporta, fontes, resultados
from eleicoes.config import (
    BRUTO_DIR,
    CODIGO_BRANCO,
    CODIGO_NULO,
    PROCESSADO_DIR,
    RESULTADOS_DIR,
    URL_BWEB,
    URL_VOTACAO_SECAO,
)
from eleicoes.download import baixar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

UFS_27 = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
]


def carrega_secao_local(ano: int) -> pd.DataFrame:
    caminho = PROCESSADO_DIR / f"secao_local_{ano}.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode 01_locais_votacao.qmd (R).")
    return pd.read_parquet(caminho)


def carrega_locais(ano: int) -> pd.DataFrame:
    caminho = PROCESSADO_DIR / f"locais_{ano}.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode 01_locais_votacao.qmd (R).")
    return pd.read_parquet(caminho)


def carrega_pesos(ano: int) -> pd.DataFrame:
    caminho = RESULTADOS_DIR / f"pesos_local_setor_{ano}.parquet"
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe — rode scripts/03_pesos_alocacao.py --ano {ano} "
            "antes da eleição."
        )
    return pd.read_parquet(caminho)


def obtem_resultados_secao(
    ano: int, turno: int, fonte: str, deve_baixar: bool
) -> pd.DataFrame:
    """Baixa (se pedido) e lê os resultados por seção, na fonte escolhida."""
    destino = BRUTO_DIR / str(ano)

    if fonte == "bweb":
        partes = []
        for uf in UFS_27:
            caminho_uf = destino / f"bweb_{turno}t_{uf}.json"
            if deve_baixar:
                url = URL_BWEB.format(ano=ano, pleito=f"{turno}t", uf=uf.lower())
                try:
                    baixar(url, caminho_uf)
                except Exception:
                    logger.exception("Falha ao baixar bweb de %s — pulando.", uf)
                    continue
            if caminho_uf.exists():
                partes.append(fontes.ler_bweb(caminho_uf, ano, turno, uf))
        if not partes:
            raise RuntimeError("Nenhum arquivo bweb disponível — ver logs acima.")
        return pd.concat(partes, ignore_index=True)

    if fonte == "votacao_secao":
        caminho_zip = destino / f"votacao_secao_{ano}_BR.zip"
        caminho_votos = destino / f"votacao_secao_{ano}_BR.csv"
        if deve_baixar:
            baixar(URL_VOTACAO_SECAO.format(ano=ano), caminho_zip)
        if not caminho_votos.exists():
            if not caminho_zip.exists():
                raise FileNotFoundError(
                    f"Nem {caminho_zip} nem {caminho_votos} existem — rode com --baixar "
                    "ou baixe manualmente (ver PLAN.md > Fontes de dados: o CDN do TSE "
                    "só respondeu via R/httr2 daqui, não via curl direto)."
                )
            logger.info("Extraindo %s...", caminho_zip)
            import zipfile

            with zipfile.ZipFile(caminho_zip) as z:
                nome_interno = next(n for n in z.namelist() if n.lower().endswith(".csv"))
                with z.open(nome_interno) as origem, open(caminho_votos, "wb") as saida:
                    saida.write(origem.read())
        return fontes.ler_votacao_secao(caminho_votos, ano, turno)

    raise ValueError(f"Fonte desconhecida: {fonte!r} (use 'bweb' ou 'votacao_secao')")


def main(ano: int, turno: int, fonte: str, deve_baixar: bool) -> None:
    logger.info("processa_turno: ano=%d turno=%d fonte=%s", ano, turno, fonte)

    secao_local = carrega_secao_local(ano)
    locais = carrega_locais(ano)
    pesos = carrega_pesos(ano)

    resultados_secao = obtem_resultados_secao(ano, turno, fonte, deve_baixar)
    logger.info("%d linhas de resultado por seção lidas.", len(resultados_secao))

    com_id = resultados.secao_para_id_local(resultados_secao, secao_local)
    n_sem_local = com_id["id_local"].isna().sum()
    if n_sem_local:
        logger.warning(
            "%d linhas (%.3f%%) sem id_local — deveria ser só o exterior.",
            n_sem_local, 100 * n_sem_local / len(com_id),
        )

    candidatos = sorted(
        c for c in com_id["nr_votavel"].unique() if c not in (CODIGO_BRANCO, CODIGO_NULO)
    )
    cols_votos_validos = [f"qt_votos_{c}" for c in candidatos]

    logger.info("Agregando por local...")
    res_local = resultados.agrega_por_local(com_id, locais)
    res_local = resultados.calcula_indicadores(res_local, cols_votos_validos=cols_votos_validos)

    logger.info("Agregando por zona x município...")
    res_zona_mun = resultados.agrega_por_zona_mun(com_id, locais)
    res_zona_mun = resultados.calcula_indicadores(
        res_zona_mun, cols_votos_validos=cols_votos_validos
    )

    logger.info("Distribuindo para os setores (W x votos)...")
    res_setor = resultados.agrega_por_setor(res_local, pesos)
    res_setor = resultados.calcula_indicadores(res_setor, cols_votos_validos=cols_votos_validos)

    prefixo = f"res_presidente_{ano}_t{turno}"
    exporta.escreve_resultados(res_local, RESULTADOS_DIR / f"{prefixo}_local.parquet")
    exporta.escreve_resultados(res_zona_mun, RESULTADOS_DIR / f"{prefixo}_zona_mun.parquet")
    exporta.escreve_resultados(res_setor, RESULTADOS_DIR / f"{prefixo}_setor.parquet")

    logger.info(
        "Concluído. Totais: local=%d zona_mun=%d setor=%.0f (deveriam ser iguais "
        "entre local e zona_mun — ver PLAN.md > Verificação).",
        res_local[cols_votos_validos].sum().sum(),
        res_zona_mun[cols_votos_validos].sum().sum(),
        res_setor[cols_votos_validos].sum().sum(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, required=True, choices=[2018, 2022, 2026])
    parser.add_argument("--turno", type=int, required=True, choices=[1, 2])
    parser.add_argument(
        "--fonte", required=True, choices=["bweb", "votacao_secao"],
        help="bweb = caminho rápido (boletim de urna); votacao_secao = consolidado oficial",
    )
    parser.add_argument("--baixar", action="store_true", help="baixa os arquivos antes de ler")
    args = parser.parse_args()
    main(args.ano, args.turno, args.fonte, args.baixar)
