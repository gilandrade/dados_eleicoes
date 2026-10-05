#!/usr/bin/env python
"""Etapa 05 — dados do visualizador de mapas do site (`docs/mapas/dados/`).

Exporta tudo o que já existir em disco: pontos (locais) e zona × município
para cada ano × turno processado, e a mudança de votação entre anos (2º
turno). A página que lê esses arquivos é `mapas.qmd` + `assets/mapas.js`.

Uso:
    python scripts/05_exporta_visualizador.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eleicoes import visualizador
from eleicoes.config import ANOS, GEOMETRIAS_DIR, RESULTADOS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TURNOS = (1, 2)


def main() -> None:
    pontos, zona_mun, mudancas = [], [], []

    for ano in ANOS:
        if (GEOMETRIAS_DIR / f"geom_locais_votacao_{ano}.gpkg").exists():
            ids = visualizador.exporta_locais(ano)
            for turno in TURNOS:
                if (RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_local.parquet").exists():
                    visualizador.exporta_locais_turno(ano, turno, ids)
                    pontos.append((ano, turno))

        if (GEOMETRIAS_DIR / f"geom_zona_mun_{ano}.gpkg").exists():
            visualizador.exporta_zona_mun_geom(ano)
            for turno in TURNOS:
                if (RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_zona_mun.parquet").exists():
                    visualizador.exporta_zona_mun_turno(ano, turno)
                    zona_mun.append((ano, turno))

    for ano_antes in ANOS:
        for ano_depois in ANOS:
            for turno in TURNOS:
                nome = f"mudanca_presidente_{ano_antes}_{ano_depois}_t{turno}_zona_mun.parquet"
                if (RESULTADOS_DIR / nome).exists():
                    visualizador.exporta_mudanca(ano_antes, ano_depois, turno)
                    mudancas.append((ano_antes, ano_depois, turno))

    visualizador.exporta_config(pontos, zona_mun, mudancas)
    logger.info("Pontos: %s | zona×mun: %s | mudança: %s", pontos, zona_mun, mudancas)


if __name__ == "__main__":
    main()
