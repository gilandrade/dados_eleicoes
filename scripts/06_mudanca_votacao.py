#!/usr/bin/env python
"""Mudança de votação entre dois anos, por zona × município — só 2º turno (2 candidatos).

Lê os dois `res_presidente_{ano}_t2_zona_mun.parquet` (`processa_turno.py`),
alinha os ids dos municípios de zona única cujo número de zona mudou
(`resultados.alinha_zona_mun`) e escreve
`mudanca_presidente_{ano_antes}_{ano_depois}_t2_zona_mun.parquet`.

Comparar nesse nível é válido entre 2018 e 2022 porque as zonas quase não
mudaram (mesmo conjunto de zonas nos 189 municípios com mais de uma; 99,84%
dos locais na mesma zona) — ver PLAN.md.

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


def carrega_zona_mun(ano: int) -> pd.DataFrame:
    caminho = RESULTADOS_DIR / f"res_presidente_{ano}_t{TURNO}_zona_mun.parquet"
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe — rode processa_turno.py --ano {ano} --turno {TURNO} primeiro."
        )
    return pd.read_parquet(caminho)


def main(ano_antes: int, ano_depois: int) -> None:
    logger.info("Mudança de votação: %d -> %d (2º turno, zona × município)", ano_antes, ano_depois)

    res_depois = carrega_zona_mun(ano_depois)
    res_antes = resultados.alinha_zona_mun(carrega_zona_mun(ano_antes), res_depois)

    mudanca = resultados.mudanca_votacao(
        res_antes, res_depois,
        ano_antes=ano_antes, ano_depois=ano_depois, nr_pt=NR_PT, chave="id_zona_mun",
    )

    territorio = lambda df: set(df.loc[~df["id_zona_mun"].str.startswith(resultados.SENTINELA_EXTERIOR), "id_zona_mun"])
    sem_par_antes = territorio(res_antes) - set(mudanca["id_zona_mun"])
    sem_par_depois = territorio(res_depois) - set(mudanca["id_zona_mun"])
    if sem_par_antes or sem_par_depois:
        logger.warning(
            "Sem par depois do alinhamento: %d em %d, %d em %d (ex.: %s)",
            len(sem_par_antes), ano_antes, len(sem_par_depois), ano_depois,
            sorted(sem_par_antes | sem_par_depois)[:5],
        )

    caminho = RESULTADOS_DIR / f"mudanca_presidente_{ano_antes}_{ano_depois}_t{TURNO}_zona_mun.parquet"
    exporta.escreve_resultados(mudanca, caminho)

    logger.info(
        "Concluído. %d zonas × município. Viradas: %s",
        len(mudanca), mudanca["sentido_virada"].value_counts().to_dict(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--de", type=int, required=True, dest="ano_antes")
    parser.add_argument("--para", type=int, required=True, dest="ano_depois")
    args = parser.parse_args()
    main(args.ano_antes, args.ano_depois)
