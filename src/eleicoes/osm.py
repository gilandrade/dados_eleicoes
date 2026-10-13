"""Recorte do OpenStreetMap por município, para a rede do r5r (etapa 03c).

O Geofabrik só publica o Brasil por região (norte, nordeste, ...). O r5r
constrói a rede com tudo o que recebe, então um município é recortado da
região (só vias, `highway=*`, num retângulo em volta dele) antes.

Usa o `osmium-tool` (C++): é rápido e quase não usa RAM. A versão com
`pyosmium` (`BackReferenceWriter`) passou de 12 GB de RAM só no sudeste. Instalação
(Windows): `micromamba create -n osm -c conda-forge osmium-tool`, e `OSMIUM_BIN`
no `.env` apontando para o `osmium.exe` (ou o executável no PATH).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import urllib.request
from pathlib import Path

from .config import GEOFABRIK_BRASIL_URL, OSM_BUFFER_GRAUS, OSM_DIR, OSMIUM_BIN

logger = logging.getLogger(__name__)


def baixa_regiao(regiao: str, destino_dir: Path = OSM_DIR) -> Path:
    """Baixa `{regiao}-latest.osm.pbf` do Geofabrik, se ainda não estiver no disco.

    Args:
        regiao: `norte`, `nordeste`, `sudeste`, `sul` ou `centro-oeste`.
        destino_dir: pasta de destino.

    Returns:
        Caminho do `.osm.pbf` da região.
    """
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"regiao_{regiao}.osm.pbf"
    if destino.exists():
        return destino
    url = GEOFABRIK_BRASIL_URL.format(regiao=regiao)
    logger.info("Baixando %s", url)
    parcial = destino.with_suffix(".part")
    with urllib.request.urlopen(url) as resp, open(parcial, "wb") as f:
        shutil.copyfileobj(resp, f, length=1 << 20)
    parcial.rename(destino)
    return destino


def _acha_osmium() -> str:
    """Caminho do executável `osmium` (`OSMIUM_BIN` ou PATH)."""
    exe = OSMIUM_BIN or shutil.which("osmium")
    if not exe or not Path(exe).exists():
        raise FileNotFoundError(
            "osmium-tool não encontrado. Instale (conda-forge: osmium-tool) e defina "
            "OSMIUM_BIN no .env com o caminho do osmium.exe."
        )
    return str(exe)


def recorta_municipios(
    pbf_regiao: Path,
    bboxes: dict[str, tuple[float, float, float, float]],
    destino_dir: Path = OSM_DIR,
    *,
    buffer_graus: float = OSM_BUFFER_GRAUS,
) -> dict[str, Path]:
    """Recorta as vias de cada município (retângulo + buffer).

    Filtra as vias da região uma vez (`vias_{regiao}.osm.pbf`, mantido no disco)
    e extrai o retângulo de cada município com `osmium extract -s complete_ways`
    (vias inteiras que encostam no retângulo). Relações (restrições de conversão) não entram.

    Args:
        pbf_regiao: `regiao_{regiao}.osm.pbf` de `baixa_regiao`.
        bboxes: `cd_mun -> (lon_min, lat_min, lon_max, lat_max)` em EPSG:4326/4674.
        destino_dir: pasta dos recortes `{cd_mun}/{cd_mun}.osm.pbf`.
        buffer_graus: folga em volta do retângulo.

    Returns:
        `cd_mun -> caminho` dos recortes gravados (os que já existiam são mantidos).

    Raises:
        FileNotFoundError: se o `osmium-tool` não estiver instalado.
        subprocess.CalledProcessError: se o `osmium` falhar.
    """
    # Uma pasta por município: o r5r lê a pasta inteira e guarda a rede nela.
    saidas = {c: destino_dir / c / f"{c}.osm.pbf" for c in bboxes}
    pendentes = [c for c in bboxes if not saidas[c].exists()]
    if not pendentes:
        return saidas
    exe = _acha_osmium()

    vias = pbf_regiao.with_name(pbf_regiao.name.replace("regiao_", "vias_", 1))
    if not vias.exists():
        subprocess.run([exe, "tags-filter", str(pbf_regiao), "w/highway", "-o", str(vias)], check=True)

    # Um `osmium extract` por município (~3 s cada). O modo `--config` com vários
    # recortes na mesma passada passou de 15 GB de RAM mesmo com 5 municípios.
    for c in pendentes:
        x0, y0, x1, y1 = bboxes[c]
        (destino_dir / c).mkdir(parents=True, exist_ok=True)
        tmp = destino_dir / c / f"{c}.tmp.osm.pbf"
        caixa = f"{x0 - buffer_graus},{y0 - buffer_graus},{x1 + buffer_graus},{y1 + buffer_graus}"
        try:
            subprocess.run(
                [exe, "extract", "-b", caixa, "-s", "complete_ways", str(vias), "-o", str(tmp), "--overwrite"],
                check=True,
            )
        except BaseException:
            tmp.unlink(missing_ok=True)  # recorte incompleto não pode ficar na pasta que o r5r lê
            raise
        tmp.rename(saidas[c])
    logger.info("%d recortes gravados (%s)", len(pendentes), pbf_regiao.stem)
    return saidas
