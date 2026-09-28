#!/usr/bin/env python
"""Resultados por município direto da seção — para anos sem locais processados.

`processa_turno.py` precisa de `secao_local_{ano}.parquet` e `locais_{ano}.parquet`
(saída das etapas 01/02, em R), que só existem para os anos já geocodificados.
Este script serve para os anos que ainda não passaram por 01–02 (2018, no
momento) e para a validação cruzada: rodado com um ano que já tem locais
(2022), o resultado por `cd_mun` deve ser idêntico ao de `processa_turno.py`
(soma de `res_..._zona_mun`) — ver PLAN.md > Verificação.

Uso:
    python scripts/processa_municipio.py --ano 2018 --turno 2
    python scripts/processa_municipio.py --ano 2022 --turno 2 --fonte votacao_secao
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from eleicoes import exporta, resultados
from eleicoes.config import CODIGO_BRANCO, CODIGO_NULO, PROCESSADO_DIR, RESULTADOS_DIR

# Reaproveita o adaptador de fonte (bweb/votacao_secao) de processa_turno.py —
# mesma lógica de download/extração, sem duplicar.
from processa_turno import obtem_resultados_secao

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def carrega_crosswalk() -> pd.DataFrame:
    caminho = PROCESSADO_DIR / "aux" / "crosswalk_mun_tse_ibge.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não existe — rode 00_auxiliares.qmd (R).")
    return pd.read_parquet(caminho)


def main(ano: int, turno: int, fonte: str, deve_baixar: bool) -> None:
    logger.info("processa_municipio: ano=%d turno=%d fonte=%s", ano, turno, fonte)

    crosswalk = carrega_crosswalk()
    resultados_secao = obtem_resultados_secao(ano, turno, fonte, deve_baixar)
    logger.info("%d linhas de resultado por seção lidas.", len(resultados_secao))

    candidatos = sorted(
        c for c in resultados_secao["nr_votavel"].unique() if c not in (CODIGO_BRANCO, CODIGO_NULO)
    )
    cols_votos_validos = [f"qt_votos_{c}" for c in candidatos]

    res_municipio = resultados.agrega_secao_por_municipio(resultados_secao, crosswalk)
    res_municipio = resultados.calcula_indicadores(
        res_municipio, cols_votos_validos=cols_votos_validos
    )

    n_sem_crosswalk = (res_municipio["cd_mun"] == resultados.SENTINELA_EXTERIOR).sum()
    if n_sem_crosswalk:
        logger.info(
            "%d linha(s) sem crosswalk (exterior) — mantida(s) com cd_mun=%s.",
            n_sem_crosswalk, resultados.SENTINELA_EXTERIOR,
        )

    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_municipio.parquet"
    exporta.escreve_resultados(res_municipio, caminho)
    logger.info(
        "Concluído. %d municípios, %.0f votos válidos.",
        len(res_municipio), res_municipio[cols_votos_validos].sum().sum(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, required=True, choices=[2018, 2022, 2026])
    parser.add_argument("--turno", type=int, required=True, choices=[1, 2])
    parser.add_argument(
        "--fonte", default="votacao_secao", choices=["bweb", "votacao_secao"],
        help="votacao_secao (padrão) = consolidado oficial; bweb = boletim de urna",
    )
    parser.add_argument("--baixar", action="store_true", help="baixa os arquivos antes de ler")
    args = parser.parse_args()
    main(args.ano, args.turno, args.fonte, args.baixar)
