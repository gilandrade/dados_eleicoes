#!/usr/bin/env python
"""Gera as linhas de cadastro das camadas de eleições na GeoReDUS.

Uma linha por mapa do site (`mapas.qmd` + `assets/mapas.js`) × ano × turno, no
formato da aba `eleicoes` da planilha de cadastro. As cores, os cortes e os
rótulos da legenda vêm de `cores.py`, os mesmos do visualizador; a classe de
cor já está calculada nas views (`scripts/07_gera_carga.py`).

As camadas usam os presets categóricos do `@redus/georedus-ui`
(`vector_point_categorical`, `vector_polygon_categorical`). Os contínuos não
servem na UI 0.23.1: o de pontos só varia o tamanho, sem cor por valor, e o de
polígonos não deixa voltar aos cortes fixos depois de trocar o método de
classificação (ver GeoReDUS/georedus#51).

O arquivo sai em `dados/03_georedus/carga/cadastro_georedus.csv`, pronto para
colar na planilha.

Uso:
    python scripts/08_gera_cadastro.py --anos 2018 2022 2026
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pyarrow.parquet as pq

from eleicoes import cores, exporta
from eleicoes.config import (
    CARGA_DIR,
    NOME_BOLSONARO_POR_ANO,
    NOME_PT_POR_ANO,
    NR_BOLSONARO_POR_ANO,
    NR_PT,
    RESULTADOS_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

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


# ==== Linhas ==================================================================


def linhas_turno(ano: int, turno: int) -> list[dict[str, str]]:
    """As camadas de um ano × turno: três de pontos e uma de zona × município."""
    view_locais = f"tse_malha_locais_votacao_{ano}_t{turno}"
    view_zona = f"tse_malha_zona_mun_{ano}_t{turno}"
    caminho = f"Eleições / {ano} / {turno}º turno"
    comum = {"collection_id": COLLECTION_ID, "indicator_id": INDICATOR_ID}

    linhas = [
        exporta.linha_cadastro(
            id_camada=f"{view_locais}.pct_brancos_nulos",
            view=view_locais,
            preset=PRESET_PONTOS,
            path=f"{caminho} / Locais de votação",
            label="Brancos e nulos",
            descricao=(
                "Votos brancos e nulos sobre o total de votos de cada local de votação."
                " Tamanho: total de votos."
            ),
            style=estilo_pontos(
                "classe_brancos_nulos",
                cores.legenda_sequencial(
                    TITULO_BRANCO_NULO, cores.QUEBRAS_BRANCO_NULO, rampa=cores.RAMPA_CINZA
                ),
                "qt_comparecimento",
            ),
            tooltip={
                "title": "nm_local_votacao",
                "entries": [
                    *ENTRADAS_LOCAL,
                    {"key": "pc_brancos_nulos", "label": "Brancos e nulos", "format": FORMATO_PCT},
                    {"key": "qt_comparecimento", "label": "Comparecimento (votos)", "format": FORMATO_INTEIRO},
                ],
            },
            **comum,
        ),
        exporta.linha_cadastro(
            id_camada=f"{view_locais}.pct_abstencao",
            view=view_locais,
            preset=PRESET_PONTOS,
            path=f"{caminho} / Locais de votação",
            label="Abstenção",
            descricao=(
                "Inscritos que não compareceram, sobre os inscritos de cada local de votação."
                " Tamanho: eleitores inscritos."
            ),
            style=estilo_pontos(
                "classe_abstencao",
                cores.legenda_sequencial(TITULO_ABSTENCAO, cores.QUEBRAS_ABSTENCAO),
                "qt_aptos",
            ),
            tooltip={
                "title": "nm_local_votacao",
                "entries": [
                    *ENTRADAS_LOCAL,
                    {"key": "pc_abstencao", "label": "Abstenção", "format": FORMATO_PCT},
                    {"key": "qt_aptos", "label": "Inscritos", "format": FORMATO_INTEIRO},
                ],
            },
            **comum,
        ),
    ]

    if ano not in NR_BOLSONARO_POR_ANO:
        logger.warning("%d: sem número de Bolsonaro — mapas de Presidente fora do cadastro", ano)
        return linhas

    nome_pt = NOME_PT_POR_ANO[ano]
    nome_bolsonaro = NOME_BOLSONARO_POR_ANO[ano]
    # A legenda é a do site, que chama o polo azul de "Bolsonaro" em todos os anos.
    legenda = cores.legenda_margem(nome_pt)
    rotulo = "Votos válidos - Presidente"
    linhas_presidente = [
        exporta.linha_cadastro(
            id_camada=f"{view_locais}.classe_margem",
            view=view_locais,
            preset=PRESET_PONTOS,
            path=f"{caminho} / Locais de votação",
            label=rotulo,
            descricao=f"Diferença entre {nome_pt} e {nome_bolsonaro} nos votos válidos. Tamanho: votos válidos.",
            style=estilo_pontos("classe_margem", legenda, "qt_validos"),
            tooltip={
                "title": "nm_local_votacao",
                "entries": [
                    *ENTRADAS_LOCAL,
                    *entradas_presidente(ano),
                    {"key": "qt_validos", "label": "Votos válidos", "format": FORMATO_INTEIRO},
                ],
            },
            **comum,
        ),
        exporta.linha_cadastro(
            id_camada=f"{view_zona}.margem_pt_bolsonaro",
            view=view_zona,
            preset=PRESET_POLIGONOS,
            path=f"{caminho} / Zona eleitoral × município",
            label=rotulo,
            descricao=f"Diferença entre {nome_pt} e {nome_bolsonaro} nos votos válidos, por zona eleitoral.",
            style=estilo_poligonos("classe_margem", legenda),
            tooltip={
                "title": TITULO_ZONA,
                "entries": [
                    *entradas_presidente(ano),
                    {"key": "qt_comparecimento", "label": "Comparecimento (votos)", "format": FORMATO_INTEIRO},
                    ENTRADA_FRONTEIRA,
                ],
            },
            **comum,
        ),
    ]
    # Mesma ordem do seletor do site: Presidente primeiro.
    return [linhas_presidente[0], *linhas, linhas_presidente[1]]


def parquet_mudanca(ano_antes: int, ano_depois: int, turno: int) -> Path:
    return RESULTADOS_DIR / f"mudanca_presidente_{ano_antes}_{ano_depois}_t{turno}_zona_mun.parquet"


def linha_mudanca(ano_antes: int, ano_depois: int, turno: int) -> dict[str, str]:
    """Mapa "quem cresceu mais nos votos válidos" entre dois anos, no mesmo turno.

    No 2º turno são 11 classes; o 3º lugar (mais duas) só existe no 1º turno.
    """
    view = f"tse_malha_zona_mun_mudanca_{ano_antes}_{ano_depois}_t{turno}"
    nome_pt = {ano: NOME_PT_POR_ANO[ano] for ano in (ano_antes, ano_depois)}
    nome_bolsonaro = {ano: NOME_BOLSONARO_POR_ANO[ano] for ano in (ano_antes, ano_depois)}
    entradas = [
        {"key": f"pc_validos_pt_{ano}", "label": f"{nome_pt[ano]} em {ano}", "format": FORMATO_PCT}
        for ano in (ano_antes, ano_depois)
    ]

    # Tabelas anteriores a `cresceu_mais_validos` (2018 → 2022) só têm a variação
    # de Lula/Haddad; o id e o texto delas ficam como já estão cadastrados.
    if "cresceu_mais_validos" not in pq.read_schema(parquet_mudanca(ano_antes, ano_depois, turno)).names:
        coluna_id = "var_pc_validos_pt"
        descricao = (
            f"Variação de {cores.nome_pt_par(ano_antes, ano_depois)} nos votos válidos entre {ano_antes} e {ano_depois},"
            " por zona eleitoral."
        )
        entradas.append({"key": "var_pc_validos_pt", "label": "Variação (p.p.)", "format": FORMATO_PP})
    else:
        coluna_id = "var_pc_validos_maior"
        descricao = (
            f"Quem cresceu mais nos votos válidos entre {ano_antes} e {ano_depois}"
            f" ({turno}º turno), em pontos percentuais, por zona eleitoral."
        )
        entradas.append(
            {"key": "var_pc_validos_pt", "label": f"Variação de {nome_pt[ano_depois]} (p.p.)", "format": FORMATO_PP}
        )
        entradas += [
            {
                "key": f"pc_validos_bolsonaro_{ano}",
                "label": f"{nome_bolsonaro[ano]} em {ano}",
                "format": FORMATO_PCT,
            }
            for ano in (ano_antes, ano_depois)
        ]
        entradas.append(
            {
                "key": "var_pc_validos_bolsonaro",
                "label": f"Variação de {nome_bolsonaro[ano_depois]} (p.p.)",
                "format": FORMATO_PP,
            }
        )
        if turno == 1:
            descricao += (
                f" 3º lugar: zonas em que {nome_pt[ano_depois]} e {nome_bolsonaro[ano_depois]}"
                " caíram; compara o mais votado fora os dois em cada eleição, mesmo que"
                " não seja o mesmo candidato."
            )
            entradas += [
                {"key": f"pc_validos_terceiro_{ano}", "label": f"3º lugar em {ano}", "format": FORMATO_PCT}
                for ano in (ano_antes, ano_depois)
            ]
            entradas.append(
                {"key": "var_pc_validos_terceiro", "label": "Variação do 3º lugar (p.p.)", "format": FORMATO_PP}
            )

    return exporta.linha_cadastro(
        id_camada=f"{view}.{coluna_id}",
        view=view,
        preset=PRESET_POLIGONOS,
        path=f"Eleições / {ano_depois} / {turno}º turno / Zona eleitoral × município",
        label=f"Mudança de votação {ano_antes} → {ano_depois}",
        descricao=descricao,
        style=estilo_poligonos("classe_mudanca", cores.legenda_mudanca(ano_antes, ano_depois, turno)),
        tooltip={
            "title": TITULO_ZONA,
            "entries": [
                *entradas,
                {"key": "mudou_vencedor", "label": "Trocou de vencedor", "format": FORMATO_SIM_NAO},
                ENTRADA_FRONTEIRA,
            ],
        },
        collection_id=COLLECTION_ID,
        indicator_id=INDICATOR_ID,
    )


def main(anos: list[int], turnos: list[int], saida: Path) -> None:
    """Ordem das linhas (a do menu): do ano mais recente ao mais antigo, 1º e
    2º turno, e a mudança de votação logo depois das camadas do turno a que chega."""
    linhas: list[dict[str, str]] = []
    anos = sorted(anos, reverse=True)
    for ano, ano_anterior in zip(anos, [*anos[1:], None]):
        for turno in sorted(turnos):
            if not (RESULTADOS_DIR / f"res_presidente_{ano}_t{turno}_local.parquet").exists():
                logger.warning("%d, turno %d: sem resultados — fora do cadastro", ano, turno)
                continue
            linhas += linhas_turno(ano, turno)
            if ano_anterior is not None and parquet_mudanca(ano_anterior, ano, turno).exists():
                linhas.append(linha_mudanca(ano_anterior, ano, turno))

    caminho = exporta.escreve_cadastro(linhas, saida)
    logger.info("%d camadas em %s", len(linhas), caminho)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anos", type=int, nargs="+", required=True)
    parser.add_argument("--turnos", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--saida", type=Path, default=CARGA_DIR / "cadastro_georedus.csv")
    args = parser.parse_args()
    main(args.anos, args.turnos, args.saida)
