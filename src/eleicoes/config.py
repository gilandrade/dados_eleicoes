"""Constantes do pipeline: caminhos, CRS, URLs e cargos.

Ver PLAN.md para o contexto completo e o modelo de dados.
"""

from __future__ import annotations

from pathlib import Path

# ==== Caminhos ==============================================================

REPO_ROOT = Path(__file__).resolve().parents[2]

DADOS_DIR = REPO_ROOT / "dados"
BRUTO_DIR = DADOS_DIR / "01_bruto"
PROCESSADO_DIR = DADOS_DIR / "02_processado"
GEOREDUS_DIR = DADOS_DIR / "03_georedus"
GEOMETRIAS_DIR = GEOREDUS_DIR / "geometrias"
RESULTADOS_DIR = GEOREDUS_DIR / "resultados"
CARGA_DIR = GEOREDUS_DIR / "carga"

# Repositório irmão com os pontos de endereço do CNEFE 2022 já processados
# (ver cnefe-etl/CLAUDE.md: geometria é WKB cru em EPSG:4674, sem metadados
# GeoParquet — decodificar com shapely.from_wkb, nunca gpd.read_parquet()).
CNEFE_ETL_DIR = REPO_ROOT.parent / "cnefe-etl"
CNEFE_POINTS_PARQUET = CNEFE_ETL_DIR / "output" / "points" / "cnefe_points_all_mun.parquet"

# pop18 por setor: calculado em R na etapa 00 (00_auxiliares.qmd), a partir dos
# indicadores já nomeados do cn22-pop (não das variáveis brutas do IBGE) — ver
# CLAUDE.md > Conventions e gotchas. Python só lê o resultado.
POP18_SETOR_PARQUET = PROCESSADO_DIR / "aux" / "pop18_setor.parquet"

# Malha de setores censitários 2022 (Brasil inteiro, ~472 mil polígonos,
# ~1,5 GB), já processada em repos irmãos — usar direto em vez de baixar de
# novo. `CD_SETOR` ali tem 15 dígitos, sem sufixo (diferente do `COD_SETOR`
# do CNEFE, que tem um "P" no final — ver `03_pesos_alocacao.carrega_domicilios_cnefe`).
SETORES_2022_GPKG = REPO_ROOT.parent / "process_gtfs" / "Dados" / "BR_setores_CD2022.gpkg"

# ==== CRS ====================================================================

CRS_WGS84 = 4326
CRS_SIRGAS2000 = 4674  # padrão de saída para a GeoReDUS (pontos e polígonos)
CRS_METRICO_BR = 5880  # SIRGAS 2000 / Brazil Polyconic — para distâncias nacionais

# ==== Escopo decidido (ver PLAN.md > Contexto) ==============================

ANOS = (2018, 2022, 2026)
CARGO_PRESIDENTE = 1  # código TSE do cargo Presidente (CD_CARGO / NR_CARGO = 1)
CODIGO_BRANCO = 95
CODIGO_NULO = 96

# ==== Fontes do TSE ===========================================================

TSE_CDN_BASE = "https://cdn.tse.jus.br/estatistica/sead/odsele"

URL_LOCAIS_VOTACAO = (
    f"{TSE_CDN_BASE}/eleitorado_locais_votacao/eleitorado_local_votacao_{{ano}}.zip"
)
URL_VOTACAO_SECAO = f"{TSE_CDN_BASE}/votacao_secao/votacao_secao_{{ano}}_BR.zip"
# QT_APTOS/QT_COMPARECIMENTO reais por seção — opcional (ver fontes.py:
# qt_comparecimento já sai certo sem ele, calculado como soma de qt_votos).
# Achado no portal humano em 29/09/2026, depois de eu chutar
# "detalhe_votacao_secao_{ano}_BR.zip" (com "_BR") e tomar 404: o nome certo
# NÃO tem "_BR" no zip — só nos CSVs de dentro dele.
URL_DETALHE_VOTACAO_SECAO = (
    f"{TSE_CDN_BASE}/detalhe_votacao_secao/detalhe_votacao_secao_{{ano}}.zip"
)
URL_CONSULTA_CAND = f"{TSE_CDN_BASE}/consulta_cand/consulta_cand_{{ano}}.zip"
URL_VOTACAO_CANDIDATO_MUNZONA = (
    f"{TSE_CDN_BASE}/votacao_candidato_munzona/votacao_candidato_munzona_{{ano}}.zip"
)
# Boletim de urna: um zip por UF, publicado pelo portal de resultados (não pelo
# CDN de dados abertos). Padrão de nome observado em eleições anteriores.
URL_BWEB = "https://resultados.tse.jus.br/oficial/ele{ano}/{pleito}/dados-simplificados/{uf}/{uf}-c0001-e{pleito}-r.json"

# Fallback: fontes de terceiros usadas para geocodificação/validação (ver etapa 02)
URL_HIDALGO_RELEASE = (
    "https://github.com/fdhidalgo/geocode_br_polling_stations/releases/latest/download/"
    "geocoded_polling_stations.csv.gz"
)

# ==== Regras de negócio =======================================================

# Distância acima da qual a coordenada do TSE é considerada suspeita mesmo
# estando dentro do município (ver geobr::read_polling_places: 800 m).
LIMIAR_DIST_TSE_REF_M = 800

# Buffer de tolerância para o teste "ponto dentro do município" (municípios
# costeiros/ilhas podem ter locais próximos à linha de costa).
BUFFER_MUNICIPIO_M = 500

# Espécies do CNEFE tratadas como domicílio (ver cnefe-etl: COD_ESPECIE)
CNEFE_ESPECIES_DOMICILIO = (1, 2)  # 1 = particular, 2 = coletivo
