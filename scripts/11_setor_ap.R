# Etapa 11 — tabela setor censitário -> área de ponderação do Censo 2022, Brasil inteiro.
#
# Liga as células da amostra do Censo (por área de ponderação, `P0090`) aos setores,
# e daí aos locais e às zonas (scripts/12_composicao_zona.py). A área de ponderação
# vem na malha de setores do geobr (`code_weighting`, 10 dígitos, mesmo formato de `P0090`).
#
# Roda com: Rscript scripts/11_setor_ap.R

suppressMessages({
  library(dplyr)
  library(arrow)
  library(sf)
})

AUX_DIR <- "dados/02_processado/aux"
dir.create(AUX_DIR, showWarnings = FALSE, recursive = TRUE)

UFS <- c(
  "RO", "AC", "AM", "RR", "PA", "AP", "TO", "MA", "PI", "CE", "RN", "PB", "PE", "AL",
  "SE", "BA", "MG", "ES", "RJ", "SP", "PR", "SC", "RS", "MS", "MT", "GO", "DF"
)

setor_ap <- lapply(UFS, function(uf) {
  message("Setores de ", uf)
  geobr::read_census_tract(
    year = 2022, code_tract = uf, simplified = TRUE, showProgress = FALSE, verbose = FALSE
  ) |>
    st_drop_geometry() |>
    transmute(
      cd_setor = sprintf("%.0f", code_tract),
      cd_mun = sprintf("%.0f", code_muni),
      cd_ap = sprintf("%.0f", code_weighting)
    )
}) |>
  bind_rows()

stopifnot(
  "cd_setor deveria ter 15 dígitos" = all(nchar(setor_ap$cd_setor) == 15),
  "setor sem área de ponderação" = !anyNA(setor_ap$cd_ap),
  "área de ponderação deveria ter 10 dígitos" = all(nchar(setor_ap$cd_ap) == 10)
)

n_repetidos <- sum(duplicated(setor_ap$cd_setor))
message(n_repetidos, " códigos de setor repetidos (um setor em mais de um polígono).")
setor_ap <- distinct(setor_ap, cd_setor, .keep_all = TRUE)

# Uma área de ponderação nunca cruza município; conferir.
cruza <- setor_ap |> distinct(cd_ap, cd_mun) |> count(cd_ap) |> filter(n > 1)
message(nrow(cruza), " áreas de ponderação em mais de um município (esperado: 0).")

write_parquet(setor_ap, file.path(AUX_DIR, "setor_ap.parquet"))
message(sprintf(
  "%d setores, %d áreas de ponderação, %d municípios.",
  nrow(setor_ap), n_distinct(setor_ap$cd_ap), n_distinct(setor_ap$cd_mun)
))
