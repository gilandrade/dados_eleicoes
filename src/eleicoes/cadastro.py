"""Helpers das linhas de cadastro das camadas na GeoReDUS (`08_cadastro_georedus.qmd`).

Presets, formatos de tooltip e o estilo (cor por classe, raio proporcional) das
camadas. As cores e os cortes vêm de `cores.py`; a montagem de cada linha fica
no notebook.
"""

from __future__ import annotations

from .config import NOME_BOLSONARO_POR_ANO, NOME_PT_POR_ANO, NR_BOLSONARO_POR_ANO, NR_PT

COLLECTION_ID = "Eleicoes / Presidente"
INDICATOR_ID = "eleicoes_presidente"

PRESET_PONTOS = "vector_point_categorical"
PRESET_POLIGONOS = "vector_polygon_categorical"
OPACIDADE_PONTOS = 0.8
OPACIDADE_POLIGONOS = 0.85

# Títulos das legendas sequenciais, iguais aos de `visualizador.exporta_config`.
TITULO_BRANCO_NULO = "Brancos e nulos (% do total de votos)"
TITULO_ABSTENCAO = "Abstenção (% dos inscritos)"

# Símbolo proporcional, como em `assets/mapas.js`: área proporcional ao valor
# (raio ∝ √valor), com referência fixa para o tamanho valer o mesmo em todos os
# anos e turnos, e raio base que cresce 25% por nível de zoom.
REF_TAMANHO = 1000
ESCALA_MIN = 0.35
ESCALA_MAX = 3.5
RAIO_BASE_ZOOM_4 = 0.9
RAIO_BASE_MAX = 10
RAIO_MINIMO_PX = 1
ZOOMS_RAIO = range(4, 16)  # no zoom 15 o raio base já está no teto

FORMATO_PCT = {"number": ["pt-BR", {"style": "percent", "minimumFractionDigits": 1, "maximumFractionDigits": 1}]}
FORMATO_PP = {"number": ["pt-BR", {"style": "percent", "signDisplay": "exceptZero", "minimumFractionDigits": 1, "maximumFractionDigits": 1}]}
FORMATO_INTEIRO = {"number": ["pt-BR", {"maximumFractionDigits": 0}]}
FORMATO_SIM_NAO = {"boolean": {"true": "Sim", "false": "Não"}}

TITULO_ZONA = "${nm_mun} – ${sigla_uf} · zona ${nr_zona}"
ENTRADAS_LOCAL = [
    {"key": "nm_mun", "label": "Município"},
    {"key": "sigla_uf", "label": "UF"},
    {"key": "nr_zona", "label": "Zona eleitoral"},
]
ENTRADA_FRONTEIRA = {
    "key": "geom_estimada",
    "label": "Fronteira interna estimada",
    "format": FORMATO_SIM_NAO,
}


# ==== Estilo ==================================================================


def categorias(legenda: dict) -> list[dict]:
    """Categorias do preset a partir de uma legenda de `cores.py` (índice = classe)."""
    return [
        {"value": classe, "label": item["rotulo"], "color": item["cor"]}
        for classe, item in enumerate(legenda["itens"])
    ]


def raio_proporcional(coluna: str) -> list:
    """Expressão MapLibre do raio (px) pela regra de tamanho do site."""
    escala = [
        "max",
        ESCALA_MIN,
        ["min", ESCALA_MAX, ["sqrt", ["/", ["to-number", ["get", coluna], 0], REF_TAMANHO]]],
    ]
    paradas: list = []
    for zoom in ZOOMS_RAIO:
        base = min(RAIO_BASE_MAX, RAIO_BASE_ZOOM_4 * 1.25 ** (zoom - 4))
        paradas += [zoom, ["max", RAIO_MINIMO_PX, ["*", round(base, 2), ["var", "escala"]]]]
    return ["let", "escala", escala, ["interpolate", ["linear"], ["zoom"], *paradas]]


def estilo_pontos(coluna_classe: str, legenda: dict, coluna_tamanho: str) -> dict:
    """Cor por classe e tamanho pela base do percentual."""
    return {
        "categoryKey": coluna_classe,
        "categories": categorias(legenda),
        "radius": raio_proporcional(coluna_tamanho),
        "opacity": OPACIDADE_PONTOS,
        "border": False,
    }


def estilo_poligonos(coluna_classe: str, legenda: dict) -> dict:
    return {
        "categoryKey": coluna_classe,
        "categories": categorias(legenda),
        "opacity": OPACIDADE_POLIGONOS,
    }


def entradas_presidente(ano: int) -> list[dict]:
    return [
        {"key": f"pc_validos_{NR_PT}", "label": NOME_PT_POR_ANO[ano], "format": FORMATO_PCT},
        {
            "key": f"pc_validos_{NR_BOLSONARO_POR_ANO[ano]}",
            "label": NOME_BOLSONARO_POR_ANO[ano],
            "format": FORMATO_PCT,
        },
        {
            "key": "margem_pt_bolsonaro",
            "label": f"Diferença ({NOME_PT_POR_ANO[ano]} − {NOME_BOLSONARO_POR_ANO[ano]})",
            "format": FORMATO_PP,
        },
    ]
