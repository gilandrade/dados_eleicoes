# Etapa 09 (país) — Censo 2022 por setor, Brasil inteiro, para a análise por zona x município.
#
# Mesmas colunas de `censo_setor_{cd_mun}.parquet` (scripts/09_insumos_municipio.R):
#   cd_setor, pop_total, n_resp, renda_media_resp, renda_mediana_resp
# `V06004`/`V06006` = média/mediana do rendimento nominal mensal dos responsáveis
# com rendimento (dicionário do IBGE de 08/05/2026).
#
# Roda com: Rscript scripts/09_censo_setor_brasil.R

suppressMessages({
  library(dplyr)
  library(arrow)
})

AUX_DIR <- "dados/02_processado/aux"
dir.create(AUX_DIR, showWarnings = FALSE, recursive = TRUE)

basico <- censobr::read_tracts(year = 2022, dataset = "Basico", showProgress = FALSE) |>
  select(code_tract, pop_total = V0001) |>
  collect()

renda <- censobr::read_tracts(year = 2022, dataset = "ResponsavelRenda", showProgress = FALSE) |>
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

stopifnot(
  "cd_setor deveria ter 15 dígitos" = all(nchar(censo_setor$cd_setor) == 15),
  "cd_setor não deveria ter duplicatas" = !anyDuplicated(censo_setor$cd_setor)
)

write_parquet(censo_setor, file.path(AUX_DIR, "censo_setor_BR.parquet"))
message(sprintf(
  "%d setores, %d com renda do responsável.",
  nrow(censo_setor), sum(!is.na(censo_setor$renda_media_resp))
))
