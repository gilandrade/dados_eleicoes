#!/usr/bin/env python
"""Etapa 05 — mapas coropléticos (Folium): um por UF (setor) + um nacional (município).

Um mapa nacional em setor censitário não cabe no GitHub Pages (Brasil
inteiro simplificado ainda dá ~380 MB só de geometria) — daí a divisão por
UF. Ver PLAN.md > "Mapas coropléticos por setor" para o raciocínio completo.

Uso:
    python scripts/05_gera_mapas.py --ano 2022 --turno 1                # nacional + 27 UFs
    python scripts/05_gera_mapas.py --ano 2022 --turno 1 --uf SP RJ     # só essas UFs
    python scripts/05_gera_mapas.py --ano 2022 --turno 1 --so-nacional  # só o mapa nacional
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eleicoes import mapas
from eleicoes.config import REPO_ROOT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

UFS_27 = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
]

DOCS_MAPAS_DIR = REPO_ROOT / "docs" / "mapas"


def gera_mapa_uf(sg_uf: str, ano: int, turno: int) -> Path:
    caminho = DOCS_MAPAS_DIR / f"{ano}_t{turno}" / f"{sg_uf}.html"
    gdf = mapas.monta_setor_uf(sg_uf, ano, turno)
    logger.info("%s: %d setores", sg_uf, len(gdf))
    resultado = mapas.gera_mapa(gdf, caminho, col_id="cd_setor", rotulo_id="Setor")
    tamanho_mb = resultado.stat().st_size / 1e6
    logger.info("%s: %s (%.1f MB)", sg_uf, resultado, tamanho_mb)
    # 60 MB é o teto prático achado testando SP (o pior caso: 103.620
    # setores, o estado mais complexo do Brasil) — a partir de ~50 MB, mais
    # simplificação só reduz uns MB a mais em troca de perder proporcionalmente
    # mais setores pequenos (ver src/eleicoes/mapas.py::simplifica_geometria).
    # Longe do teto duro do git (100 MB); só um alerta para revisar de olho.
    if tamanho_mb > 60:
        logger.warning(
            "%s: %.1f MB > 60 MB — bem acima do que SP (o pior caso testado) "
            "deu. Vale conferir se essa UF tem algo fora do comum.",
            sg_uf, tamanho_mb,
        )
    return resultado


def gera_mapa_nacional(ano: int, turno: int) -> Path:
    caminho = DOCS_MAPAS_DIR / f"{ano}_t{turno}" / "index.html"
    gdf = mapas.monta_municipios(ano, turno)
    logger.info("Nacional: %d municípios", len(gdf))
    resultado = mapas.gera_mapa(gdf, caminho, col_id="cd_mun", rotulo_id="Município")
    logger.info("Nacional: %s (%.1f MB)", resultado, resultado.stat().st_size / 1e6)
    return resultado


def gera_mapa_zona_mun(ano: int, turno: int) -> Path:
    """A camada mais fiel de todas — valores exatamente iguais ao que o TSE
    publica por município×zona; só a fronteira interna das cidades com mais
    de uma zona é estimada (`geom_estimada`, ver `mapas.monta_zona_mun`).
    """
    caminho = DOCS_MAPAS_DIR / f"{ano}_t{turno}" / "zona_mun.html"
    gdf = mapas.monta_zona_mun(ano, turno)
    logger.info("Zona×município: %d polígonos", len(gdf))
    resultado = mapas.gera_mapa(gdf, caminho, col_id="id_zona_mun", rotulo_id="Zona×Município")
    logger.info("Zona×município: %s (%.1f MB)", resultado, resultado.stat().st_size / 1e6)
    return resultado


def main(ano: int, turno: int, ufs: list[str] | None, so_nacional: bool) -> None:
    if not so_nacional:
        for sg_uf in (ufs or UFS_27):
            try:
                gera_mapa_uf(sg_uf, ano, turno)
            except Exception:
                logger.exception("Falha gerando o mapa de %s — pulando.", sg_uf)

    if ufs is None:
        gera_mapa_nacional(ano, turno)
        gera_mapa_zona_mun(ano, turno)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", type=int, required=True, choices=[2018, 2022, 2026])
    parser.add_argument("--turno", type=int, required=True, choices=[1, 2])
    parser.add_argument("--uf", nargs="+", default=None, help="só essas UFs (não gera o nacional)")
    parser.add_argument("--so-nacional", action="store_true", help="só o mapa nacional por município")
    args = parser.parse_args()
    main(args.ano, args.turno, args.uf, args.so_nacional)
