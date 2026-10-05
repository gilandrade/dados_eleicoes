# Etapa 00b — insumos nacionais sem repos irmãos: malha de setores 2022 (geobr)
# e domicílios do CNEFE 2022 (cnefetools), no formato que a etapa 03 lê.
#
# Saídas (dados/01_bruto/):
#   aux/setores/BR_setores_CD2022.gpkg   CD_SETOR (15 dígitos), CD_MUN, CD_UF; EPSG:4674
#   cnefe/cnefe_{cd_mun}.parquet         cd_mun, cd_setor, cod_especie, latitude, longitude
#
# Retomável: municípios com parquet pronto e UFs já gravadas na malha são
# pulados. O CNEFE é baixado por município (~5.570 downloads do FTP do IBGE).
#
# Roda com: Rscript scripts/00b_insumos_brasil.R [setores|cnefe]   (padrão: os dois)
#           Rscript scripts/00b_insumos_brasil.R cnefe 35   (só os municípios da UF 35)

suppressMessages({
  library(dplyr)
  library(stringr)
  library(arrow)
  library(sf)
})

args <- commandArgs(trailingOnly = TRUE)
etapas <- if (length(args) >= 1) args[1] else c("setores", "cnefe")
filtro_uf <- if (length(args) >= 2) args[2] else NULL

CNEFE_MUN_DIR <- "dados/01_bruto/cnefe"
SETORES_DIR <- "dados/01_bruto/aux/setores"
AUX_DIR <- "dados/02_processado/aux"
for (pasta in c(CNEFE_MUN_DIR, SETORES_DIR)) dir.create(pasta, showWarnings = FALSE, recursive = TRUE)

CNEFE_ESPECIES_DOMICILIO <- c(1L, 2L)  # 1 = particular, 2 = coletivo
TENTATIVAS <- 4

com_retry <- function(expr_fn) {
  for (t in seq_len(TENTATIVAS)) {
    r <- tryCatch(expr_fn(), error = function(e) {
      message("  tentativa ", t, "/", TENTATIVAS, " falhou: ", conditionMessage(e))
      NULL
    })
    if (!is.null(r)) return(r)
    Sys.sleep(2^t)
  }
  NULL
}

crosswalk <- read_parquet(file.path(AUX_DIR, "crosswalk_mun_tse_ibge.parquet"))
municipios <- sort(unique(crosswalk$cd_mun))
if (!is.null(filtro_uf)) municipios <- municipios[startsWith(municipios, filtro_uf)]

# ---- Malha de setores --------------------------------------------------------
if ("setores" %in% etapas) {
  gpkg <- file.path(SETORES_DIR, "BR_setores_CD2022.gpkg")
  ufs <- sort(unique(substr(municipios, 1, 2)))
  feitas <- if (file.exists(gpkg)) unique(st_read(gpkg, query = "select distinct CD_UF from setores", quiet = TRUE)$CD_UF) else character()
  for (uf in setdiff(ufs, feitas)) {
    message("Setores da UF ", uf)
    s <- com_retry(function() geobr::read_census_tract(
      year = 2022, code_tract = as.integer(uf), simplified = FALSE,
      showProgress = FALSE, verbose = FALSE
    ))
    if (is.null(s)) stop("Falha ao baixar os setores da UF ", uf)
    s <- s |>
      transmute(
        CD_SETOR = sprintf("%.0f", code_tract),
        CD_MUN = sprintf("%.0f", code_muni),
        CD_UF = uf
      ) |>
      st_transform(4674) |>
      st_make_valid()
    st_write(s, gpkg, layer = "setores", append = file.exists(gpkg), quiet = TRUE)
    message("  ", nrow(s), " setores")
  }
}

# ---- CNEFE por município -----------------------------------------------------
if ("cnefe" %in% etapas) {
  cache <- file.path(tempdir(), "cnefe_cache")
  falhas <- character()
  for (i in seq_along(municipios)) {
    cd_mun <- municipios[i]
    saida <- file.path(CNEFE_MUN_DIR, sprintf("cnefe_%s.parquet", cd_mun))
    if (file.exists(saida)) next
    message(sprintf("[%d/%d] CNEFE %s", i, length(municipios), cd_mun))
    cnefe <- com_retry(function() {
      cnefetools::read_cnefe(code_muni = as.integer(cd_mun), output = "arrow", verbose = FALSE, cache = FALSE) |>
        select(COD_SETOR, COD_ESPECIE, LATITUDE, LONGITUDE) |>
        collect()
    })
    if (is.null(cnefe)) {
      falhas <- c(falhas, cd_mun)
      next
    }
    cnefe <- cnefe |>
      filter(COD_ESPECIE %in% CNEFE_ESPECIES_DOMICILIO, !is.na(LATITUDE), !is.na(LONGITUDE)) |>
      transmute(
        cd_mun = cd_mun,
        cd_setor = str_remove(COD_SETOR, "P$"),  # sufixo "P" ausente na malha
        cod_especie = as.integer(COD_ESPECIE),
        latitude = LATITUDE,
        longitude = LONGITUDE
      )
    write_parquet(cnefe, saida)
  }
  if (length(falhas)) message("Municípios sem CNEFE (rodar de novo): ", paste(falhas, collapse = ", "))
}
message("Concluído.")
