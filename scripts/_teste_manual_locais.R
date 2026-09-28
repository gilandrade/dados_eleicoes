# Teste manual (dados sintéticos) da lógica de 01_locais_votacao.qmd:
# resolução de seção agregada pela seção principal (inclusive quando o local
# da própria linha "Agregada" diverge do local da principal — caso real,
# confirmado em ~2% das 48.326 linhas "Agregada" do arquivo de 2022), chave
# (id_secao, nr_turno), e flags a partir de DS_TIPO_LOCAL.
#
# Roda com: Rscript scripts/_teste_manual_locais.R

suppressMessages({
  library(dplyr)
  library(stringr)
})

CRESCE_ZONA <- 4
CRESCE_NUM <- 4

# Município fictício, zona 1, turno 1 e 2:
#   - secao 1: Principal, local 10 (caso comum)
#   - secao 2: Agregada de secao 1, PRÓPRIO local também é 10 (concorda com a
#     principal — o caso majoritário, ~98%)
#   - secao 3: Agregada de secao 1, mas o PRÓPRIO local é 20 (diverge da
#     principal — o caso raro que precisa ser resolvido via NR_SECAO_PRINCIPAL,
#     não pelo local da própria linha)
#   - secao 4: Distribuída de ofício, NR_SECAO_PRINCIPAL = -1, local 30
#   - secao 5: Voto em trânsito (DS_TIPO_LOCAL), local 40
#   - secao 6: Preso provisório (DS_TIPO_LOCAL), local 50
# No turno 2, a secao 1 muda de local (10 -> 11) — o caso real de 0,28% dos
# casos em que o local de uma seção difere entre turnos.
linhas <- tibble::tribble(
  ~cd_municipio, ~nr_zona, ~nr_secao, ~nr_turno, ~nr_local_votacao, ~nr_secao_principal, ~ds_tipo_secao_agregada, ~ds_tipo_local, ~nm_local_votacao,
  "88001", 1, 1, 1, 10, "-1", "Principal", "Convencional", "ESCOLA A",
  "88001", 1, 2, 1, 10, "1",  "Agregada",  "Convencional", "ESCOLA A",
  "88001", 1, 3, 1, 20, "1",  "Agregada",  "Convencional", "ESCOLA B",  # diverge!
  "88001", 1, 4, 1, 30, "-1", "Distribuída de ofício", "Convencional", "ESCOLA C",
  "88001", 1, 5, 1, 40, "-1", "Principal", "Voto em trânsito", "POSTO TRANSITO",
  "88001", 1, 6, 1, 50, "-1", "Principal", "Preso provisório", "PRESIDIO CENTRAL",
  "88001", 1, 1, 2, 11, "-1", "Principal", "Convencional", "ESCOLA A",  # local mudou no turno 2
  "88001", 1, 2, 2, 11, "1",  "Agregada",  "Convencional", "ESCOLA A",
)

locais_com_chave <- linhas |>
  mutate(
    id_local_proprio = str_c(cd_municipio, str_pad(nr_zona, CRESCE_ZONA, pad = "0"), str_pad(nr_local_votacao, CRESCE_NUM, pad = "0")),
    id_secao = str_c(cd_municipio, str_pad(nr_zona, CRESCE_ZONA, pad = "0"), str_pad(nr_secao, CRESCE_NUM, pad = "0")),
    id_secao_principal = if_else(
      nr_secao_principal != "-1",
      str_c(cd_municipio, str_pad(nr_zona, CRESCE_ZONA, pad = "0"), str_pad(nr_secao_principal, CRESCE_NUM, pad = "0")),
      NA_character_
    )
  )

local_por_secao <- locais_com_chave |>
  distinct(id_secao, nr_turno, .keep_all = TRUE) |>
  select(id_secao, nr_turno, id_local_da_secao = id_local_proprio)

locais_com_chave <- locais_com_chave |>
  left_join(local_por_secao, by = c("id_secao_principal" = "id_secao", "nr_turno" = "nr_turno")) |>
  mutate(id_local = coalesce(id_local_da_secao, id_local_proprio))

resultado <- locais_com_chave |> select(nr_secao, nr_turno, id_local_proprio, id_local)
print(resultado)

# secao 2 (agregada, concorda): id_local deve ser o da secao 1 (local 10)
stopifnot(resultado$id_local[resultado$nr_secao == 2 & resultado$nr_turno == 1] == "8800100010010")
# secao 3 (agregada, DIVERGE): id_local deve ser o da PRINCIPAL (10), não o da própria linha (20)
stopifnot(resultado$id_local[resultado$nr_secao == 3 & resultado$nr_turno == 1] == "8800100010010")
# secao 4 (distribuída de ofício): usa o próprio local (30), sem principal
stopifnot(resultado$id_local[resultado$nr_secao == 4 & resultado$nr_turno == 1] == "8800100010030")
# secao 1 no turno 2: local mudou para 11 -- a resolução deve respeitar o turno
stopifnot(resultado$id_local[resultado$nr_secao == 1 & resultado$nr_turno == 2] == "8800100010011")
# secao 2 no turno 2: agregada da secao 1 DO MESMO TURNO -> deve ir para 11, não para o 10 do turno 1
stopifnot(resultado$id_local[resultado$nr_secao == 2 & resultado$nr_turno == 2] == "8800100010011")

cat("OK: resolução de seção agregada (concordante, divergente, oficio) e por turno.\n")

# ---- flags a partir de DS_TIPO_LOCAL ---------------------------------------

locais_flag <- locais_com_chave |>
  mutate(
    flag_transito = ds_tipo_local == "Voto em trânsito",
    flag_presidio = ds_tipo_local == "Preso provisório",
    flag_especial = flag_transito | flag_presidio
  )

stopifnot(sum(locais_flag$flag_transito) == 1, sum(locais_flag$flag_presidio) == 1)
stopifnot(sum(locais_flag$flag_especial) == 2)
cat("OK: flags de voto em trânsito (1) e preso provisório (1), 2 especiais no total.\n")

# ---- secao_local final: uma linha por (id_secao, nr_turno) ------------------

secao_local <- locais_flag |> transmute(id_secao, nr_turno, id_local) |> distinct(id_secao, nr_turno, .keep_all = TRUE)
stopifnot(nrow(secao_local) == 8)  # 6 seções no turno 1 + 2 no turno 2, todas distintas por (id_secao, nr_turno)
stopifnot(!anyDuplicated(secao_local[c("id_secao", "nr_turno")]))
cat("OK: secao_local com", nrow(secao_local), "linhas, chave (id_secao, nr_turno) sem duplicatas.\n")

cat("\nOK — todos os testes manuais de 01_locais_votacao.qmd (versão corrigida) passaram.\n")
