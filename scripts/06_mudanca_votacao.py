#!/usr/bin/env python
"""Mudança de votação entre dois anos, por município — só 2º turno (2 candidatos).

Lê os dois `res_presidente_{ano}_t2_municipio.parquet` (gerados por
`processa_turno.py` ou `scripts/processa_municipio.py`) e escreve
`mudanca_presidente_{ano_antes}_{ano_depois}_t2_municipio.parquet`.

Uso:
    python scripts/06_mudanca_votacao.py --de 2018 --para 2022
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from eleicoes import exporta, resultados
from eleicoes.config import NR_PT, RESULTADOS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TURNO = 2  # só faz sentido no 2º turno (2 candidatos)


def carrega_municipio(ano: int) -> pd.DataFrame:
    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{TURNO}_municipio.parquet"
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe — rode processa_turno.py ou "
            f"scripts/processa_municipio.py --ano {ano} --turno {TURNO} primeiro."
        )
    return pd.read_parquet(caminho)


def main(ano_antes: int, ano_depois: int) -> None:
    logger.info("Mudança de votação: %d -> %d (2º turno)", ano_antes, ano_depois)

    res_antes = carrega_municipio(ano_antes)
    res_depois = carrega_municipio(ano_depois)

    mudanca = resultados.mudanca_votacao(
        res_antes, res_depois, ano_antes=ano_antes, ano_depois=ano_depois, nr_pt=NR_PT
    )

    caminho = RESULTADOS_DIR / f"mudanca_presidente_{ano_antes}_{ano_depois}_t{TURNO}_municipio.parquet"
    exporta.escreve_resultados(mudanca, caminho)

    logger.info(
        "Concluído. %d municípios. Viradas: %s",
        len(mudanca), mudanca["sentido_virada"].value_counts().to_dict(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--de", type=int, required=True, dest="ano_antes")
    parser.add_argument("--para", type=int, required=True, dest="ano_depois")
    args = parser.parse_args()
    main(args.ano_antes, args.ano_depois)
