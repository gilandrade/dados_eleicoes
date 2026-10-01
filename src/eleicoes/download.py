"""Download com cache e retry.

Baixa um arquivo para `destino`, pulando o download se ele já existir (o
padrão usado nos repos R da GeoReDUS, ver `inep-escolas-geo/index.qmd`).
Grava primeiro em um `.part` e renomeia no final, para que uma falha no meio
do download nunca deixe um arquivo corrompido com o nome final.

O CDN do TSE devolveu 403 a partir do ambiente onde este repositório foi
criado (ver PLAN.md > Riscos e pendências) — testar a partir da rede do CEM.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    # O CDN do TSE bloqueia alguns clientes sem User-Agent de navegador.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )
}


def baixar(
    url: str,
    destino: Path,
    *,
    tentativas: int = 3,
    timeout: int = 60,
    pular_se_existe: bool = True,
) -> Path:
    """Baixa `url` para `destino`, com retry exponencial (2^n segundos).

    Args:
        url: URL do arquivo.
        destino: caminho local final.
        tentativas: número máximo de tentativas.
        timeout: timeout por tentativa, em segundos.
        pular_se_existe: se True (padrão), não baixa de novo se `destino` já
            existir e tiver tamanho > 0.

    Returns:
        O caminho `destino`.

    Raises:
        requests.HTTPError: se todas as tentativas falharem.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    if pular_se_existe and destino.exists() and destino.stat().st_size > 0:
        logger.info("Já existe, pulando: %s", destino)
        return destino

    parcial = destino.with_suffix(destino.suffix + ".part")
    ultimo_erro: Exception | None = None

    for tentativa in range(1, tentativas + 1):
        try:
            with requests.get(
                url, headers=DEFAULT_HEADERS, timeout=timeout, stream=True
            ) as resp:
                resp.raise_for_status()
                with open(parcial, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if chunk:
                            f.write(chunk)
            parcial.replace(destino)
            logger.info("Baixado: %s <- %s", destino, url)
            return destino
        except requests.RequestException as exc:
            ultimo_erro = exc
            logger.warning(
                "Tentativa %d/%d falhou para %s: %s", tentativa, tentativas, url, exc
            )
            if parcial.exists():
                parcial.unlink()
            if tentativa < tentativas:
                time.sleep(2**tentativa)

    assert ultimo_erro is not None
    raise ultimo_erro
