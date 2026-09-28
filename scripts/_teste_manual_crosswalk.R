# Teste manual (sem rede) da lógica de crosswalk de 00_auxiliares.qmd.
# Usa uma cópia local do CSV do yuripassuelo (baixada fora do R, já que
# httr2/url() segfocam neste sandbox — ver PLAN.md > Riscos e pendências) para
# validar só a lógica de parsing/transmute/checagem, não o download.
#
# Roda com: Rscript scripts/_teste_manual_crosswalk.R /caminho/para/rel1.csv

suppressMessages({
  library(dplyr)
  library(readr)
  library(stringr)
})

args <- commandArgs(trailingOnly = TRUE)
caminho_crosswalk_bruto <- if (length(args) >= 1) args[[1]] else stop("passe o caminho do CSV")

crosswalk_bruto <- read_csv(
  caminho_crosswalk_bruto,
  locale = locale(encoding = "latin1"),
  col_types = cols(.default = col_character())
)

crosswalk <- crosswalk_bruto |>
  transmute(
    cd_mun_tse = str_pad(cd_mun_tse, 5, pad = "0"),
    cd_mun = str_pad(cd_mun_ibge, 7, pad = "0"),
    sg_uf = SG_UF,
    nm_mun_ibge = nm_mun_ibge,
    nm_mun_tse = nm_mun_tse
  )

stopifnot(
  "crosswalk deveria ter 5.570 municípios (sem o exterior)" = nrow(crosswalk) == 5570,
  "cd_mun_tse não deveria ter duplicatas" = !anyDuplicated(crosswalk$cd_mun_tse),
  "cd_mun (IBGE) não deveria ter duplicatas" = !anyDuplicated(crosswalk$cd_mun)
)
stopifnot(
  "cd_mun_tse deveria ter sempre 5 dígitos" = all(nchar(crosswalk$cd_mun_tse) == 5)
)
cat("OK: crosswalk com", nrow(crosswalk), "municípios, sem duplicatas, cd_mun_tse com 5 dígitos.\n")

normaliza_texto <- function(x) {
  x |>
    stringr::str_to_upper() |>
    stringi::stri_trans_general("Latin-ASCII") |>
    stringr::str_squish()
}

divergencias <- crosswalk |>
  mutate(
    dist_nome = stringdist::stringdist(
      normaliza_texto(nm_mun_ibge), normaliza_texto(nm_mun_tse), method = "jw"
    )
  ) |>
  filter(dist_nome > 0.15) |>
  arrange(desc(dist_nome))

cat("Municípios com nome IBGE x TSE muito diferente:", nrow(divergencias), "\n")
print(head(divergencias, 10))

cat("\nOK — teste manual do crosswalk passou.\n")
