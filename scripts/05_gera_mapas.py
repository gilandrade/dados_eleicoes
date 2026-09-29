#!/usr/bin/env python
"""Mapas Folium coropléticos para **uso local** (não publicados).

O site publica os mapas pelo visualizador (`mapas.qmd`, dados de
`scripts/05_exporta_visualizador.py`). Estes HTML — setor por UF, município
e zona×município — saem em `dados/mapas_folium/{ano}_t{turno}/`, que é
git-ignorado: cada um tem ~30 MB e, publicados, estourariam o limite de
1 GB do GitHub Pages.

Uso:
    python scripts/05_gera_mapas.py --ano 2022 --turno 1                      # tudo
    python scripts/05_gera_mapas.py --ano 2022 --turno 1 --uf SP RJ           # só essas UFs (setor)
    python scripts/05_gera_mapas.py --ano 2018 --turno 2 --camadas zona_mun
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eleicoes import mapas
from eleicoes.config import DADOS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

UFS_27 = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
]
CAMADAS_VALIDAS = ("setor", "municipio", "zona_mun")
MAPAS_FOLIUM_DIR = DADOS_DIR / "mapas_folium"


def _salva(gdf, ano: int, turno: int, nome: str, col_id: str, rotulo_id: str) -> Path:
    caminho = MAPAS_FOLIUM_DIR / f"{ano}_t{turno}" / f"{nome}.html"
    resultado = mapas.gera_mapa(gdf, caminho, ano=ano, col_id=col_id, rotulo_id=rotulo_id)
    logger.info("%s: %d feições, %.1f MB", resultado, len(gdf), resultado.stat().st_size / 1e6)
    return resultado


def main(ano: int, turno: int, ufs: list[str] | None, camadas: list[str]) -> None:
    if "setor" in camadas:
        for sg_uf in (ufs or UFS_27):
            try:
                _salva(mapas.monta_setor_uf(sg_uf, ano, turno), ano, turno, sg_uf, "cd_setor", "Setor")
            except Exception:
                logger.exception("Falha gerando o mapa de %s — pulando.", sg_uf)

    if ufs is not None:
        return  # --uf é só para o setor

    if "municipio" in camadas:
        _salva(mapas.monta_municipios(ano, turno), ano, turno, "municipio", "cd_mun", "Município")
    if "zona_mun" in camadas:
        _salva(mapas.monta_zona_mun(ano, turno), ano, turno, "zona_mun", "id_zona_mun", "Zona×Município")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, required=True, choices=[2018, 2022, 2026])
    parser.add_argument("--turno", type=int, required=True, choices=[1, 2])
    parser.add_argument("--uf", nargs="+", default=None, help="só essas UFs (setor; não gera os nacionais)")
    parser.add_argument(
        "--camadas", nargs="+", default=list(CAMADAS_VALIDAS), choices=CAMADAS_VALIDAS,
        help="quais camadas gerar (padrão: todas)",
    )
    args = parser.parse_args()
    main(args.ano, args.turno, args.uf, args.camadas)
