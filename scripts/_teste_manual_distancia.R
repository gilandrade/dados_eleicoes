# Teste manual de calcula_distancia_m (02_geocodificacao_locais.qmd) com NA
# em posições variadas -- o bug real era o dplyr::if_else() avaliando os dois
# ramos para o vetor inteiro, então a função precisa aguentar NA sozinha.
#
# Roda com: Rscript scripts/_teste_manual_distancia.R

suppressMessages({
  library(sf)
})

calcula_distancia_m <- function(lat1, lon1, lat2, lon2) {
  completo <- !is.na(lat1) & !is.na(lon1) & !is.na(lat2) & !is.na(lon2)
  dist <- rep(NA_real_, length(lat1))
  if (any(completo)) {
    p1 <- st_as_sf(data.frame(lat = lat1[completo], lon = lon1[completo]), coords = c("lon", "lat"), crs = 4674)
    p2 <- st_as_sf(data.frame(lat = lat2[completo], lon = lon2[completo]), coords = c("lon", "lat"), crs = 4674)
    dist[completo] <- as.numeric(st_distance(st_transform(p1, 5880), st_transform(p2, 5880), by_element = TRUE))
  }
  dist
}

lat1 <- c(-23.5, NA, -23.6, -23.5)
lon1 <- c(-46.6, -46.6, NA, -46.6)
lat2 <- c(-23.5, -23.5, -23.6, NA)
lon2 <- c(-46.6, -46.6, -46.6, -46.6)

resultado <- calcula_distancia_m(lat1, lon1, lat2, lon2)
print(resultado)

stopifnot(is.na(resultado[2]), is.na(resultado[3]), is.na(resultado[4]))
stopifnot(!is.na(resultado[1]), resultado[1] < 1)  # mesmo ponto, distância ~0

cat("OK — calcula_distancia_m trata NA em qualquer posição sem quebrar.\n")
