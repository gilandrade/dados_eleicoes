"""Constantes do pipeline: caminhos, CRS, URLs e cargos.

Ver PLAN.md para o contexto completo e o modelo de dados.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ==== Caminhos ==============================================================

REPO_ROOT = Path(__file__).resolve().parents[2]

# .env é git-ignorado (ver .gitignore) — segredos/chaves de API nunca ficam
# hardcoded no código-fonte. Ver .env.example para o que precisa preencher.
load_dotenv(REPO_ROOT / ".env")

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
# "Voto anulado e apurado em separado" — existe no 2018 (1º turno), não em
# 2022. Não é candidato: sem excluí-lo, entra como "voto válido" e distorce
# todos os percentuais. Conta como nulo em `pc_brancos_nulos`.
CODIGO_ANULADO_APURADO_SEPARADO = 97
CODIGOS_NAO_CANDIDATO = (CODIGO_BRANCO, CODIGO_NULO, CODIGO_ANULADO_APURADO_SEPARADO)

# 2º turno: PT contra Bolsonaro, que troca de número (partido) a cada eleição.
NR_BOLSONARO_POR_ANO = {2018: 17, 2022: 22}
NOME_PT_POR_ANO = {2018: "Haddad", 2022: "Lula"}

# Número do PT no 2º turno — usado em `resultados.mudanca_votacao` para achar
# quem venceu (o do Bolsonaro muda de partido a cada eleição: 17 em 2018,
# 22 em 2022, então não dá para fixar um "número do opositor").
NR_PT = 13

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

# ==== Mapas (etapa 05) ========================================================

# CARTO Basemaps agora exige API key mesmo para o preset "CartoDB positron"
# do folium (confirmado em 29/09/2026 — a chamada sem key dá warning e os
# tiles não carregam). A chave vem de .env (git-ignorado), nunca hardcoded
# aqui — ver .env.example. Fica None se .env não tiver CARTO_API_KEY;
# src/eleicoes/mapas.py é quem falha com uma mensagem clara ao tentar gerar
# um mapa sem ela (as outras etapas não dependem disso).
CARTO_API_KEY = os.environ.get("CARTO_API_KEY")
TILES_CARTO_POSITRON = (
    "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
    f"?api_key={CARTO_API_KEY}"
)
TILES_CARTO_ATTR = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
    'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
)

# Fundo do visualizador do site. Testado em 29/09/2026: o CARTO devolve o
# mesmo tile "API KEY REQUIRED" com ou sem `?api_key=` (de qualquer referer),
# então a chave não está tendo efeito — e os HTML Folium publicados mostravam
# esse aviso. O Esri Light Gray é claro como o Positron e não pede chave.
TILES_VISUALIZADOR = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
    "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)
TILES_VISUALIZADOR_ATTR = "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors"

# `SETORES_2022_GPKG` não tem sigla de 2 letras — só `CD_UF` (código IBGE) e
# `NM_UF` (achado tentando filtrar por "SG_UF", que não existe nesse
# arquivo). Código oficial do IBGE, usado para filtrar a malha por UF em
# `mapas.py`.
CD_UF_POR_SG_UF = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27,
    "SE": 28, "BA": 29,
    "MG": 31, "ES": 32, "RJ": 33, "SP": 35,
    "PR": 41, "SC": 42, "RS": 43,
    "MS": 50, "MT": 51, "GO": 52, "DF": 53,
}
