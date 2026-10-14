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

# Pontos agrupados por zoom: o tile traz a coluna `nivel` e cada nível tem a sua
# referência de tamanho, o seu fator de pixel (o nível agregado é desenhado maior) e
# o seu teto. Nos níveis agregados o raio segue √valor sem o limite de escala dos
# locais, e quem limita é o teto em px: com o limite de escala, São Paulo ficava
# do tamanho de Osasco. O corte de zoom de cada nível fica na função de tile.
COLUNA_NIVEL = "nivel"
NIVEL_UF, NIVEL_MUNICIPIO, NIVEL_LOCAL = 0, 1, 2
REF_TAMANHO_POR_NIVEL = {NIVEL_UF: 1_000_000, NIVEL_MUNICIPIO: 10_000, NIVEL_LOCAL: REF_TAMANHO}
FATOR_RAIO_POR_NIVEL = {NIVEL_UF: 16, NIVEL_MUNICIPIO: 1.5, NIVEL_LOCAL: 1}
ESCALA_MAX_POR_NIVEL = {NIVEL_UF: 1000, NIVEL_MUNICIPIO: 1000, NIVEL_LOCAL: ESCALA_MAX}
RAIO_TETO_POR_NIVEL = {NIVEL_UF: 90, NIVEL_MUNICIPIO: 45, NIVEL_LOCAL: RAIO_BASE_MAX * ESCALA_MAX}

FORMATO_PCT = {"number": ["pt-BR", {"style": "percent", "minimumFractionDigits": 1, "maximumFractionDigits": 1}]}
FORMATO_PP = {"number": ["pt-BR", {"style": "percent", "signDisplay": "exceptZero", "minimumFractionDigits": 1, "maximumFractionDigits": 1}]}
FORMATO_INTEIRO = {"number": ["pt-BR", {"maximumFractionDigits": 0}]}
FORMATO_SIM_NAO = {"boolean": {"true": "Sim", "false": "Não"}}

# Atributo `titulo` da função de tile das zonas: "Município – UF · zona N" no nível das
# zonas e "Município – UF" (ou a UF) nos níveis agrupados (`carga.gera_funcao_tile`).
TITULO_ZONA = "titulo"
TITULO_MUNICIPIO = "${nm_mun} – ${sigla_uf}"
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


def _escala(valor: list, referencia: float, maximo: float = ESCALA_MAX) -> list:
    """Raio relativo: √(valor / referência), limitado a [ESCALA_MIN, maximo]."""
    return ["max", ESCALA_MIN, ["min", maximo, ["sqrt", ["/", valor, referencia]]]]


def raio_proporcional(coluna: str, *, por_nivel: bool = False) -> list:
    """Expressão MapLibre do raio (px) pela regra de tamanho do site.

    Args:
        coluna: coluna do tile com a base do tamanho (`qt_validos` etc.).
        por_nivel: se verdadeiro, a referência de tamanho e o fator de pixel
            vêm da coluna `nivel` do tile (UF, município, local), para as camadas
            agrupadas por zoom. Falso mantém uma referência única.

    Returns:
        Expressão para `style.radius` do preset `vector_point_categorical`.
    """
    valor = ["to-number", ["get", coluna], 0]
    paradas: list = []
    for zoom in ZOOMS_RAIO:
        base = min(RAIO_BASE_MAX, RAIO_BASE_ZOOM_4 * 1.25 ** (zoom - 4))
        if por_nivel:
            raio = ["min", ["var", "teto"], ["*", round(base, 2), ["var", "fator"], ["var", "escala"]]]
        else:
            raio = ["*", round(base, 2), ["var", "escala"]]
        paradas += [zoom, ["max", RAIO_MINIMO_PX, raio]]
    interpolacao = ["interpolate", ["linear"], ["zoom"], *paradas]

    if not por_nivel:
        return ["let", "escala", _escala(valor, REF_TAMANHO), interpolacao]

    nivel = ["get", COLUNA_NIVEL]

    def por_nivel_de(valores: dict[int, float], *, escala: bool = False) -> list:
        """`match` em `nivel`: o valor de cada nível (ou a escala, que usa `valor`)."""
        pares = {
            n: _escala(["var", "valor"], REF_TAMANHO_POR_NIVEL[n], ESCALA_MAX_POR_NIVEL[n])
            if escala
            else valores[n]
            for n in (NIVEL_UF, NIVEL_MUNICIPIO, NIVEL_LOCAL)
        }
        return ["match", nivel, NIVEL_UF, pares[NIVEL_UF], NIVEL_MUNICIPIO, pares[NIVEL_MUNICIPIO], pares[NIVEL_LOCAL]]

    # `let` não enxerga as variáveis do próprio `let`: `valor` fica no externo.
    return [
        "let", "valor", valor,
        ["let",
            "escala", por_nivel_de({}, escala=True),
            "fator", por_nivel_de(FATOR_RAIO_POR_NIVEL),
            "teto", por_nivel_de(RAIO_TETO_POR_NIVEL),
            interpolacao,
        ],
    ]  # fmt: skip


def fonte_por_zoom(view: str) -> str:
    """Nome da fonte de tile que escolhe o nível pelo zoom (função em `public_read_api`)."""
    return f"{view}_nivel"


def estilo_pontos(
    coluna_classe: str, legenda: dict, coluna_tamanho: str, *, por_nivel: bool = False
) -> dict:
    """Cor por classe e tamanho pela base do percentual."""
    return {
        "categoryKey": coluna_classe,
        "categories": categorias(legenda),
        "radius": raio_proporcional(coluna_tamanho, por_nivel=por_nivel),
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
