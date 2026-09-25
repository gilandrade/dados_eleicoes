# Teste manual (dados sintéticos) da lógica de chaves de 01_locais_votacao.qmd:
# construção de id_local/id_secao, join com o crosswalk, flags de seção
# especial e a contagem de zonas por município.
#
# Roda com: Rscript scripts/_teste_manual_locais.R

suppressMessages({
  library(dplyr)
  library(stringr)
})

CRESCE_ZONA <- 4
CRESCE_NUM <- 4

# município fictício com 1 zona (2 locais) e município com 2 zonas (1 local
# cada), incluindo uma seção especial (voto em trânsito) e uma agregada.
locais_bruto <- tibble::tribble(
  ~cd_municipio, ~sg_uf, ~nr_zona, ~nr_secao, ~nr_local_votacao, ~nm_local_votacao, ~ds_situacao_local_votacao, ~nr_latitude, ~nr_longitude,
  "88001", "SP", 1, 1, 10, "ESCOLA A", "", "-23.55", "-46.63",
  "88001", "SP", 1, 2, 10, "ESCOLA A", "", "-23.55", "-46.63",  # seção agregada no mesmo local
  "88001", "SP", 1, 3, 20, "ESCOLA B", "", "-23.56", "-46.64",
  "88002", "SP", 1, 1, 30, "ESCOLA C", "", "-23.50", "-46.60",
  "88002", "SP", 2, 2, 40, "ESCOLA D", "", "-23.51", "-46.61",
  "88002", "SP", 3, 3, 50, "ESCOLA TRANSITO", "Voto em trânsito", "-23.52", "-46.62",
)

crosswalk <- tibble::tibble(cd_mun_tse = c("88001", "88002"), cd_mun = c("3550001", "3550002"), sg_uf = "SP")

locais_com_chave <- locais_bruto |>
  mutate(
    id_local = str_c(
      cd_municipio,
      str_pad(nr_zona, CRESCE_ZONA, pad = "0"),
      str_pad(nr_local_votacao, CRESCE_NUM, pad = "0")
    )
  ) |>
  left_join(crosswalk, by = c("cd_municipio" = "cd_mun_tse"))

stopifnot(all(!is.na(locais_com_chave$cd_mun)))
cat("OK: join com crosswalk sem perda\n")

locais_flag <- locais_com_chave |>
  mutate(
    flag_transito = str_detect(coalesce(ds_situacao_local_votacao, ""), regex("trânsito", ignore_case = TRUE)),
    flag_especial = flag_transito
  )

stopifnot(sum(locais_flag$flag_especial) == 1)
cat("OK: 1 local marcado como especial (voto em trânsito)\n")

locais <- locais_flag |>
  distinct(id_local, .keep_all = TRUE)

# 5 locais distintos no total: 88001 tem 2 (local 10 e local 20 — as seções 1
# e 2 caem no mesmo id_local do local 10, a seção agregada não cria local
# extra); 88002 tem 3 (locais 30, 40 e 50, um por zona).
stopifnot(nrow(locais) == 5)
cat("OK:", nrow(locais), "locais distintos (esperado 5)\n")

secao_local <- locais_flag |>
  transmute(
    id_secao = str_c(
      cd_municipio,
      str_pad(nr_zona, CRESCE_ZONA, pad = "0"),
      str_pad(nr_secao, CRESCE_NUM, pad = "0")
    ),
    id_local
  )

stopifnot(nrow(secao_local) == 6)
stopifnot(all(secao_local$id_local %in% locais$id_local))
cat("OK: 6 seções, todas com id_local existente\n")

n_zonas_mun <- locais |>
  distinct(cd_mun, nr_zona) |>
  count(cd_mun, name = "n_zonas_mun")

stopifnot(n_zonas_mun$n_zonas_mun[n_zonas_mun$cd_mun == "3550001"] == 1)
stopifnot(n_zonas_mun$n_zonas_mun[n_zonas_mun$cd_mun == "3550002"] == 3)
cat("OK: contagem de zonas por município (3550001=1, 3550002=3)\n")

cat("\nOK — todos os testes manuais de 01_locais_votacao.qmd passaram.\n")
