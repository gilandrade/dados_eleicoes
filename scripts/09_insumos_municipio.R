# Etapa 09 — insumos de um município para a análise perfil do eleitorado × voto.
#
# Reúne, só para o município pedido, o que as etapas seguintes precisam, sem
# depender dos repos irmãos (cnefe-etl, cn22-pop, process_gtfs):
#   - zips do TSE: perfil do eleitorado, votos por seção e candidatos;
#   - domicílios do CNEFE (cnefetools);
#   - renda do responsável e população por setor (censobr);
#   - malha de setores 2022 (geobr);
#   - pontos dos locais de votação, com a coordenada do TSE validada.
#
# Pré-requisito: 00_auxiliares.qmd e 01_locais_votacao.qmd (do mesmo ano) já
# renderizados.
#
# Roda com: Rscript scripts/09_insumos_municipio.R 2211001 2022

suppressMessages({
  library(dplyr)
  library(stringr)
  library(httr2)
  library(arrow)
  library(sf)
})

# ---- Parâmetros --------------------------------------------------------------

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Uso: Rscript scripts/09_insumos_municipio.R <cd_mun IBGE> [ano]")
}
cd_mun <- args[1]
cd_mun_int <- as.integer(cd_mun)
ano <- if (length(args) >= 2) as.integer(args[2]) else 2022L

BRUTO_DIR <- file.path("dados/01_bruto", ano)
CNEFE_MUN_DIR <- "dados/01_bruto/cnefe"
SETORES_MUN_DIR <- "dados/01_bruto/aux/setores"
AUX_DIR <- "dados/02_processado/aux"
PROCESSADO_DIR <- "dados/02_processado"
GEOMETRIAS_DIR <- "dados/03_georedus/geometrias"
for (pasta in c(BRUTO_DIR, CNEFE_MUN_DIR, SETORES_MUN_DIR, GEOMETRIAS_DIR)) {
  dir.create(pasta, showWarnings = FALSE, recursive = TRUE)
}

TSE_CDN_BASE <- "https://cdn.tse.jus.br/estatistica/sead/odsele"
CNEFE_ESPECIES_DOMICILIO <- c(1L, 2L)  # 1 = particular, 2 = coletivo
BUFFER_MUNICIPIO_M <- 500
# Acima disto, a validação simples daqui não basta: rodar a etapa 02, que tem
# a cadeia de fallback (outro ano, Hidalgo, geocodebr).
LIMITE_INSCRITOS_SEM_COORD <- 0.01

crosswalk <- read_parquet(file.path(AUX_DIR, "crosswalk_mun_tse_ibge.parquet")) |>
  filter(cd_mun == !!cd_mun)
if (nrow(crosswalk) != 1) stop("Município ", cd_mun, " não está no crosswalk TSE x IBGE.")
sg_uf <- crosswalk$sg_uf
message(sprintf(
  "Município %s (%s/%s), código TSE %s, ano %d",
  cd_mun, crosswalk$nm_mun_ibge, sg_uf, crosswalk$cd_mun_tse, ano
))

# ---- 1. Zips do TSE ----------------------------------------------------------
# Baixados por aqui (httr2) porque o CDN devolve 403 para o Python. O
# votacao_secao da UF tem os cargos da eleição estadual (deputado federal
# entre eles); Presidente só existe no arquivo _BR.

baixar_com_retry <- function(url, destino, tentativas = 3) {
  if (file.exists(destino)) {
    return(invisible(destino))
  }
  parcial <- paste0(destino, ".part")
  for (tentativa in seq_len(tentativas)) {
    resultado <- tryCatch(
      {
        request(url) |>
          req_user_agent("dados_eleicoes (CEM/USP)") |>
          req_perform(path = parcial)
        TRUE
      },
      error = function(e) {
        message(sprintf("Tentativa %d/%d falhou: %s", tentativa, tentativas, conditionMessage(e)))
        FALSE
      }
    )
    if (isTRUE(resultado)) break
    if (tentativa < tentativas) Sys.sleep(2^tentativa)
  }
  if (!isTRUE(resultado)) stop("Não foi possível baixar ", url)
  file.rename(parcial, destino)
  invisible(destino)
}

arquivos_tse <- c(
  sprintf("perfil_eleitor_secao/perfil_eleitor_secao_%d_%s.zip", ano, sg_uf),
  sprintf("votacao_secao/votacao_secao_%d_%s.zip", ano, sg_uf),
  sprintf("votacao_secao/votacao_secao_%d_BR.zip", ano),
  sprintf("consulta_cand/consulta_cand_%d.zip", ano)
)
for (arquivo in arquivos_tse) {
  message("TSE: ", basename(arquivo))
  baixar_com_retry(
    file.path(TSE_CDN_BASE, arquivo),
    file.path(BRUTO_DIR, basename(arquivo))
  )
}

# ---- 2. Domicílios do CNEFE --------------------------------------------------
# COD_SETOR vem com um sufixo "P" (16 caracteres) que a malha de setores não
# tem — conferido em Teresina em 03/10/2026, igual ao que o cnefe-etl guardava.

cnefe <- cnefetools::read_cnefe(
  code_muni = cd_mun_int, output = "arrow", verbose = FALSE
) |>
  select(COD_SETOR, COD_ESPECIE, LATITUDE, LONGITUDE) |>
  collect() |>
  filter(COD_ESPECIE %in% CNEFE_ESPECIES_DOMICILIO) |>
  transmute(
    cd_mun = !!cd_mun,
    cd_setor = str_remove(COD_SETOR, "P$"),
    cod_especie = as.integer(COD_ESPECIE),
    latitude = LATITUDE,
    longitude = LONGITUDE
  )

n_sem_coord_cnefe <- sum(is.na(cnefe$latitude) | is.na(cnefe$longitude))
if (n_sem_coord_cnefe > 0) {
  message(n_sem_coord_cnefe, " domicílios do CNEFE sem coordenada, descartados.")
  cnefe <- cnefe |> filter(!is.na(latitude), !is.na(longitude))
}
stopifnot("cd_setor do CNEFE deveria ter 15 dígitos" = all(nchar(cnefe$cd_setor) == 15))

write_parquet(cnefe, file.path(CNEFE_MUN_DIR, sprintf("cnefe_%s.parquet", cd_mun)))
message(nrow(cnefe), " domicílios do CNEFE.")

# ---- 3. Censo 2022 por setor -------------------------------------------------
# code_tract é double no censobr; vira texto com sprintf (ver 00_auxiliares.qmd).
# V06004 e V06006 são a média e a mediana do rendimento nominal mensal dos
# responsáveis com rendimento (dicionário do IBGE de 08/05/2026).

basico <- censobr::read_tracts(year = 2022, dataset = "Basico", showProgress = FALSE) |>
  filter(code_muni == cd_mun_int) |>
  select(code_tract, pop_total = V0001) |>
  collect()

renda <- censobr::read_tracts(year = 2022, dataset = "ResponsavelRenda", showProgress = FALSE) |>
  filter(code_muni == cd_mun_int) |>
  select(
    code_tract,
    n_resp = V06001,
    renda_media_resp = V06004,
    renda_mediana_resp = V06006
  ) |>
  collect()

censo_setor <- basico |>
  left_join(renda, by = "code_tract") |>
  mutate(cd_setor = sprintf("%.0f", code_tract)) |>
  select(cd_setor, pop_total, n_resp, renda_media_resp, renda_mediana_resp)

write_parquet(censo_setor, file.path(AUX_DIR, sprintf("censo_setor_%s.parquet", cd_mun)))
message(sprintf(
  "%d setores no Censo, %d com renda do responsável.",
  nrow(censo_setor), sum(!is.na(censo_setor$renda_media_resp))
))

# ---- 4. Malha de setores -----------------------------------------------------
# Gravada com os nomes que a etapa 03 já espera da malha do IBGE.

setores <- geobr::read_census_tract(
  year = 2022, code_tract = cd_mun_int, simplified = FALSE
) |>
  transmute(
    CD_SETOR = sprintf("%.0f", code_tract),
    CD_MUN = sprintf("%.0f", code_muni)
  ) |>
  st_transform(4674)

n_repetidos <- sum(duplicated(setores$CD_SETOR))
if (n_repetidos > 0) {
  message(n_repetidos, " códigos de setor repetidos na malha (a etapa 03 junta os pedaços).")
}

st_write(
  setores, file.path(SETORES_MUN_DIR, sprintf("setores_%s.gpkg", cd_mun)),
  delete_dsn = TRUE, quiet = TRUE
)
message(nrow(setores), " setores na malha.")

# ---- 5. Pontos dos locais de votação ----------------------------------------
# Mesmas regras de validação da etapa 02 (coordenada nula, sentinela, fora do
# município, repetida em 3 ou mais locais), sem a cadeia de fallback: o local
# reprovado vai para o centróide do município e fica fora da alocação.

locais <- read_parquet(file.path(PROCESSADO_DIR, sprintf("locais_%d.parquet", ano))) |>
  filter(cd_mun == !!cd_mun)
if (nrow(locais) == 0) stop("Nenhum local de votação do município em locais_", ano, ".parquet.")

municipio <- st_read(file.path(AUX_DIR, "municipios.gpkg"), quiet = TRUE) |>
  filter(cd_mun == !!cd_mun)
municipio_buffer <- municipio |>
  st_transform(5880) |>
  st_buffer(BUFFER_MUNICIPIO_M) |>
  st_transform(4674)

locais <- locais |>
  mutate(
    coord_nula = is.na(latitude) | is.na(longitude),
    coord_sentinela = !coord_nula & (
      (latitude == 0 & longitude == 0) | (latitude == -1 & longitude == -1)
    )
  ) |>
  group_by(latitude, longitude) |>
  mutate(coord_repetida = n() >= 3) |>
  ungroup()

candidatos <- !locais$coord_nula & !locais$coord_sentinela
dentro_do_municipio <- rep(FALSE, nrow(locais))
if (any(candidatos)) {
  pontos <- st_as_sf(locais[candidatos, ], coords = c("longitude", "latitude"), crs = 4674)
  dentro_do_municipio[candidatos] <- lengths(st_intersects(pontos, municipio_buffer)) > 0
}

centroide <- municipio |>
  st_transform(5880) |>
  st_centroid() |>
  st_transform(4674) |>
  st_coordinates()

locais <- locais |>
  mutate(
    coord_valida = candidatos & dentro_do_municipio & !coord_repetida,
    fonte_coord = if_else(coord_valida, "tse", "centroide_municipio"),
    latitude = if_else(coord_valida, latitude, centroide[1, "Y"]),
    longitude = if_else(coord_valida, longitude, centroide[1, "X"]),
    flag_sem_coord = !coord_valida,
    precisao = fonte_coord
  )

elegiveis <- locais |> filter(!flag_especial)
pc_inscritos_sem_coord <- sum(elegiveis$qt_eleitores[elegiveis$flag_sem_coord], na.rm = TRUE) /
  sum(elegiveis$qt_eleitores, na.rm = TRUE)
message(sprintf(
  "%d locais, %d com coordenada do TSE reprovada (%.2f%% dos inscritos dos locais elegíveis).",
  nrow(locais), sum(locais$flag_sem_coord), 100 * pc_inscritos_sem_coord
))
if (pc_inscritos_sem_coord > LIMITE_INSCRITOS_SEM_COORD) {
  stop(
    "Mais de ", 100 * LIMITE_INSCRITOS_SEM_COORD, "% dos inscritos em locais sem ",
    "coordenada válida — rodar 02_geocodificacao_locais.qmd em vez deste passo."
  )
}

geom_locais <- locais |>
  st_as_sf(coords = c("longitude", "latitude"), crs = 4674, remove = FALSE) |>
  select(id_local, cd_mun, nr_zona, fonte_coord, precisao, flag_especial, flag_sem_coord, qt_eleitores)

st_write(
  geom_locais,
  file.path(GEOMETRIAS_DIR, sprintf("geom_locais_votacao_%d_%s.gpkg", ano, cd_mun)),
  delete_dsn = TRUE, quiet = TRUE
)

message("Concluído.")
