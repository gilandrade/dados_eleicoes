# Teste manual (dados sintéticos) de valida_coordenada_tse (02_geocodificacao_locais.qmd).
# Confere que: coordenada nula/sentinela reprova; coordenada fora do
# município reprova; coordenada repetida >=3x no município reprova; e uma
# coordenada boa passa.
#
# Roda com: Rscript scripts/_teste_manual_geocodificacao.R

suppressMessages({
  library(dplyr)
  library(sf)
})

BUFFER_MUNICIPIO_M <- 500

valida_coordenada_tse <- function(locais, municipios, buffer_m = BUFFER_MUNICIPIO_M) {
  locais <- locais |>
    mutate(
      coord_nula = is.na(latitude) | is.na(longitude),
      coord_sentinela = !coord_nula & (
        (latitude == 0 & longitude == 0) | (latitude == -1 & longitude == -1)
      )
    )

  candidatos <- locais |> filter(!coord_nula & !coord_sentinela)

  if (nrow(candidatos) > 0) {
    pontos <- st_as_sf(candidatos, coords = c("longitude", "latitude"), crs = 4674, remove = FALSE)

    municipios_buffer <- municipios |>
      st_transform(5880) |>
      st_buffer(buffer_m) |>
      st_transform(4674)

    candidatos_por_ponto <- st_intersects(pontos, municipios_buffer)
    cd_mun_dos_poligonos <- municipios_buffer$cd_mun
    dentro_do_municipio <- vapply(
      seq_along(candidatos_por_ponto),
      \(i) pontos$cd_mun[i] %in% cd_mun_dos_poligonos[candidatos_por_ponto[[i]]],
      logical(1)
    )
    candidatos$coord_fora_municipio <- !dentro_do_municipio
  } else {
    candidatos$coord_fora_municipio <- logical(0)
  }

  candidatos <- candidatos |>
    group_by(cd_mun, latitude, longitude) |>
    mutate(coord_repetida = n() >= 3) |>
    ungroup()

  locais |>
    left_join(
      candidatos |> st_drop_geometry() |> select(id_local, coord_fora_municipio, coord_repetida),
      by = "id_local"
    ) |>
    mutate(
      coord_valida = !coord_nula & !coord_sentinela &
        !coalesce(coord_fora_municipio, TRUE) & !coalesce(coord_repetida, TRUE)
    )
}

# Município quadrado 1x1 grau centrado em (-46.65, -23.55).
municipios <- st_sf(
  cd_mun = "9999999",
  geometry = st_sfc(st_polygon(list(rbind(
    c(-47.15, -24.05), c(-46.15, -24.05), c(-46.15, -23.05), c(-47.15, -23.05), c(-47.15, -24.05)
  ))), crs = 4674)
)

locais <- tibble::tribble(
  ~id_local, ~cd_mun, ~latitude, ~longitude,
  "L_boa",       "9999999", -23.55, -46.65,   # dentro, única -> válida
  "L_nula",      "9999999", NA_real_, NA_real_,
  "L_sentinela", "9999999", -1, -1,
  "L_fora",      "9999999", -10.0, -40.0,     # bem fora do município
  "L_rep1",      "9999999", -23.60, -46.60,   # repetida 3x -> centróide provável
  "L_rep2",      "9999999", -23.60, -46.60,
  "L_rep3",      "9999999", -23.60, -46.60,
)

resultado <- valida_coordenada_tse(locais, municipios)
print(resultado |> select(id_local, coord_valida, coord_fora_municipio, coord_repetida))

stopifnot(resultado$coord_valida[resultado$id_local == "L_boa"])
stopifnot(!resultado$coord_valida[resultado$id_local == "L_nula"])
stopifnot(!resultado$coord_valida[resultado$id_local == "L_sentinela"])
stopifnot(!resultado$coord_valida[resultado$id_local == "L_fora"])
stopifnot(!any(resultado$coord_valida[resultado$id_local %in% c("L_rep1", "L_rep2", "L_rep3")]))

cat("\nOK — todos os testes manuais de valida_coordenada_tse passaram.\n")
