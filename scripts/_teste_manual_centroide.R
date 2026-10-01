# Teste manual do fallback de centróide de município (02_geocodificacao_locais.qmd):
# local sem nenhuma fonte de coordenada usa o centróide do próprio município;
# local sem cd_mun (exterior) fica sem_coord de verdade, sem geometria.
#
# Achado real ao rodar contra 2022: sem esse passo, locais não resolvidos
# ficavam publicados com a coordenada INVÁLIDA original do TSE (ex.: o
# sentinela -1,-1), em vez de cair no centróide ou ficar de fora do mapa.
#
# Roda com: Rscript scripts/_teste_manual_centroide.R

suppressMessages({
  library(dplyr)
  library(sf)
})

municipios <- st_sf(
  cd_mun = c("111", "222"),
  geometry = st_sfc(
    st_polygon(list(rbind(c(0, 0), c(2, 0), c(2, 2), c(0, 2), c(0, 0)))),
    st_polygon(list(rbind(c(10, 10), c(12, 10), c(12, 12), c(10, 12), c(10, 10))))
  ),
  crs = 4674
)

locais_final <- tibble::tribble(
  ~id_local, ~cd_mun, ~fonte_coord, ~latitude, ~longitude,
  "L1", "111", "tse", -0.5, 0.5,                # já resolvido, não mexe
  "L2", "111", NA_character_, -1, -1,           # sem fonte -> centróide do 111
  "L3", NA_character_, NA_character_, -1, -1,   # exterior, sem cd_mun -> sem_coord de verdade
)

centro_municipios <- st_centroid(municipios) |> st_transform(4674)
coords_centro <- st_coordinates(centro_municipios)
centroides <- centro_municipios |>
  st_drop_geometry() |>
  transmute(cd_mun, latitude_centroide = coords_centro[, 2], longitude_centroide = coords_centro[, 1])

locais_final <- locais_final |>
  left_join(centroides, by = "cd_mun") |>
  mutate(
    usa_centroide = is.na(fonte_coord) & !is.na(latitude_centroide),
    latitude = if_else(usa_centroide, latitude_centroide, latitude),
    longitude = if_else(usa_centroide, longitude_centroide, longitude),
    fonte_coord = case_when(
      !is.na(fonte_coord) ~ fonte_coord,
      usa_centroide ~ "centroide_municipio",
      TRUE ~ NA_character_
    )
  ) |>
  select(-latitude_centroide, -longitude_centroide, -usa_centroide) |>
  mutate(
    fonte_coord = coalesce(fonte_coord, "sem_coord"),
    flag_sem_coord = fonte_coord %in% c("sem_coord", "centroide_municipio"),
    latitude = if_else(fonte_coord == "sem_coord", NA_real_, latitude),
    longitude = if_else(fonte_coord == "sem_coord", NA_real_, longitude)
  )

print(locais_final)

stopifnot(locais_final$fonte_coord[locais_final$id_local == "L1"] == "tse")
stopifnot(locais_final$fonte_coord[locais_final$id_local == "L2"] == "centroide_municipio")
stopifnot(abs(locais_final$latitude[locais_final$id_local == "L2"] - 1) < 1e-3)  # centro do quadrado 0-2 -> 1
stopifnot(locais_final$fonte_coord[locais_final$id_local == "L3"] == "sem_coord")
stopifnot(is.na(locais_final$latitude[locais_final$id_local == "L3"]))

geom <- locais_final |>
  filter(fonte_coord != "sem_coord") |>
  st_as_sf(coords = c("longitude", "latitude"), crs = 4674, remove = FALSE)
stopifnot(nrow(geom) == 2)

cat("OK — fallback de centróide de município e exclusão do exterior sem coordenada.\n")
