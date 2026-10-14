# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What this repo is

An analysis/ETL repo (not a package): it turns TSE electoral data (polling places
and Presidential results, 2018/2022/2026) into GeoReDUS-ready layers — points
(polling places), zona × município (observed), and setores censitários 2022
(estimated via dasymetric allocation using CNEFE 2022 as ancillary data). No tests,
no linting — prioritize pipeline reproducibility and readable notebooks.

Bilingual by nature: prose and domain nouns in Portuguese, code structure in
English. See [PLAN.md](PLAN.md) for the full context, the research on how votes are
distributed across territory, and the schedule.

## Commands

R / Quarto (locais de votação + geocodificação):

```bash
Rscript -e "renv::restore()"
quarto render 00_auxiliares.qmd
quarto render 01_locais_votacao.qmd
quarto render 02_geocodificacao_locais.qmd
```

`.Rprofile` sources `renv/activate.R`.

Python (resultados, alocação, exportação, mapas) — também em `.qmd`, com o
kernel do venv; os parâmetros entram por `-P`:

```bash
venv\Scripts\activate.bat        # PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # preencher CARTO_API_KEY (ver Conventions abaixo)
export QUARTO_PYTHON="$PWD/venv/Scripts/python.exe"   # se o venv não estiver ativado
quarto render 03_pesos_alocacao.qmd -P ano:2022
quarto render 04_resultados_turno.qmd -P ano:2022 -P turno:1
quarto render 05_mudanca_votacao.qmd -P de:2018 -P para:2022
quarto render 03b_baixa_resultados.qmd -P ano:2024        # zips de resultado (R; 26 UFs nos municipais)
quarto render 04_resultados_turno.qmd -P ano:2024 -P turno:1 -P cargo:prefeito   # ou vereador
quarto render 06_exporta_visualizador.qmd    # dados do site (docs/mapas/dados/)
quarto render mapas.qmd                      # só a página, nunca o projeto inteiro
```

Nas etapas locais, `--profile local` (`_quarto-local.yml`) manda o HTML e as
`*_files` para `dados/relatorios/` em vez da raiz, ex.:
`quarto render 03_pesos_alocacao.qmd --profile local -P ano:2022`. Sem o perfil
o comportamento é o antigo (HTML na raiz).

As etapas 00b e 03–09 não fazem parte do site (`project: render:` no
`_quarto.yml` lista só as páginas publicadas): o HTML delas sai na raiz, ao lado
do `.qmd`, e é git-ignorado.

Outro ano nas etapas R: `quarto render 01_locais_votacao.qmd -P ano:2018 --output
01_locais_votacao_2018.html` (idem para 02) — o `--output` evita sobrescrever a
página de 2022 no site.

## Architecture

One numbered `.qmd` per stage in the repo root, in execution order; two
languages, parquet/gpkg as the handoff format. **The notebooks hold the
processing, step by step; `src/eleicoes/` only has functions they call** (no
CLI scripts — `scripts/` was removed on 11/10/2026). See `PLAN.md` for the full
data model (geometry files vs. result files joined by a shared id).

- **00–02 (R)**: crosswalk TSE↔IBGE, malhas, `pop18` por setor; download e chaves
  dos locais/seções; validação e geocodificação das coordenadas. `00b_insumos_brasil.qmd`
  (R) baixa a malha de setores e o CNEFE do Brasil.
- **Eleições municipais (2020, 2024, branch `eleicoes-municipais`)**: reaproveitam
  01–04 sem mudança de método; só o cargo muda (`config.CARGOS`, `-P cargo:prefeito|vereador`
  na 04/04b). Prefeito é lido por candidato (o número do votável é o do partido, nomes
  em `candidatos_prefeito_*.parquet`); Vereador é somado por partido
  (`fontes.ler_votacao_secao(por_partido=True)`). O visualizador pinta o **partido
  mais votado** (`cores.CORES_PARTIDO`, `visualizador.exporta_*_partido`). A mudança
  entre anos (05), o Folium (06b) e a GeoReDUS (07/08) seguem só com Presidente.
- **03 (Python, `03_pesos_alocacao.qmd`, roda 1× por ano)**: aloca cada
  domicílio do CNEFE ao local de votação mais próximo do mesmo município,
  constrói a matriz de pesos `local → setor` (ponderada pela população adulta) e
  gera as geometrias derivadas (`geom_areas_locais`, `geom_zona_mun`).
- **04 (Python, `04_resultados_turno.qmd`)**: lê os resultados (boletim de urna
  ou `votacao_secao`), agrega por local, por zona × município e, multiplicando
  pela matriz de pesos, por setor. `04b_resultados_municipio.qmd` agrega por
  município direto da seção (ano sem locais processados; validação cruzada).
- **05 (Python, `05_mudanca_votacao.qmd`)**: mudança de votação entre
  dois anos, no mesmo turno, por zona × município.
- **06 (Python, `06_exporta_visualizador.qmd`)**: dados do **visualizador de
  mapas do site** (`src/eleicoes/visualizador.py` → `docs/mapas/dados/*.json`,
  lidos por `mapas.qmd` + `assets/mapas.js`). Cores e classes em
  `src/eleicoes/cores.py`. Os mapas Folium (`mapas.py`, `06b_mapas_folium.qmd`)
  são só para uso local, em `dados/mapas_folium/` — não voltar a publicá-los
  (tamanho, ver abaixo).
- **07–08 (Python)**: `07_carga_georedus.qmd` gera os artefatos de carga
  (`table.sh`/`schema.sql`/`down.sql` e as views; helpers em `carga.py`) e
  `08_cadastro_georedus.qmd` as linhas de cadastro das camadas (`cadastro.py`).
- **09 (R, `09_insumos_municipio.qmd`)**: insumos de um município só, para a
  análise perfil do eleitorado × voto.

## Conventions and gotchas

- **CNEFE 2022 e malha de setores não vêm mais de repos irmãos** (05/10/2026):
  `00b_insumos_brasil.qmd` gera `dados/01_bruto/cnefe/cnefe_{cd_mun}.parquet`
  (cnefetools, 5.570 municípios, ~90,7 milhões de domicílios, `cd_setor` já sem o
  "P", lat/lon em EPSG:4674) e `dados/01_bruto/aux/setores/BR_setores_CD2022.gpkg`
  (geobr 2.1.0, 468.097 setores). É retomável e aceita `-P etapa:cnefe -P uf:35`.
  Como script, o CNEFE levava ~1 h com vários processos por UF; como `.qmd` roda
  em sequência (dois `quarto render` do mesmo arquivo ao mesmo tempo disputam os
  intermediários do knitr).
  A malha do geobr tem ~1% menos setores que a do IBGE usada em 2022.
- **CRS:** tudo em EPSG:4674 nas saídas para a GeoReDUS. Cálculos de distância usam
  EPSG:5880 (SIRGAS 2000 / Brazil Polyconic), métrico nacional.
- **geobr 2.1.0** (a restrição a 1.9.1 caiu; atualizado na branch `analise-perfil-voto`).
- **`geobr::read_municipality(..., showProgress = FALSE)` devolve `NULL` em silêncio**
  ao renderizar via `quarto render` (fora de um console interativo) — confirmado em
  25/09/2026. Deixar `showProgress` no padrão (`TRUE`, omitir o argumento), mesmo que
  isso jogue a barra de progresso no output do Quarto.
- **Ids são texto**, no formato IBGE/TSE (nunca inteiro, para preservar zeros à
  esquerda): `id_local`, `id_secao`, `id_zona_mun`, `cd_mun`, `cd_setor`.
- **Camadas observadas vs. estimadas:** `geom_locais_votacao` e
  `res_..._zona_mun` são valores publicados pelo TSE, exatos. `geom_areas_locais`,
  `geom_zona_mun` (quando `geom_estimada = TRUE`) e `res_..._setor` são estimativas
  do método de alocação — sempre expor a camada observada ao lado.
- **A matriz de pesos é cara de calcular, barata de aplicar.** `03_pesos_alocacao.qmd`
  roda uma vez por ano, antes da eleição. `04_resultados_turno.qmd`, na noite da eleição,
  só multiplica — não recalcula a matriz.
- **duckdb (Python) em `fontes.py`:** o método `con.read_csv(..., encoding="latin-1")`
  ignora o encoding e sempre exige UTF-8 nesta versão (1.5.x). Use a função SQL
  `read_csv(...)` (via `con.sql(...)`), que respeita o parâmetro — ver `_sql_read_csv()`.
- **Não há testes** (os `_teste_manual_*` foram removidos em 11/10/2026). Cada
  notebook termina numa seção "Conferência" com `assert` (pesos somando 1,
  totais iguais entre local, zona × município e município, variações sobre
  aptos somando zero). Depois de mexer em `alocacao.py`/`resultados.py`,
  renderizar 03 num município (`-P cd_mun:2211001`) e 04 num turno e ler essas
  seções.
- **`-P` nos `.qmd` em Python exige `papermill` e `pyyaml` no venv** (já no
  `requirements.txt`). O Quarto mantém um daemon do kernel por alguns minutos:
  depois de instalar pacote, usar `--execute-daemon-restart`, senão o erro
  antigo se repete. Valor de `-P` é lido como YAML (`-P cd_mun:2211001` chega
  como inteiro) — os notebooks convertem para texto logo após a célula de
  parâmetros.
- **Chamadas de rede em R (httr2, `url()`) travam com "segmentation fault" no
  sandbox onde o repositório foi montado inicialmente** (confirmado até para
  `base::url()`, sem geobr envolvido) — não é o bug documentado do geobr 2.x.
  Fora desse sandbox (a máquina real do Gil) a rede funciona normalmente; os
  `.qmd` de 00–01 já rodaram de ponta a ponta lá com dados reais de 2022 (ver
  abaixo).
- **Duas instalações de R nesta máquina** (Gil): R 4.6.0 no `PATH` (usado por
  `Rscript`/`R.exe` direto), e R 4.5.2 em `C:\Program Files\R\R-4.5.2`, que é a
  que o RStudio/VSCode-R usa de fato e onde `renv::restore()` populou o
  `renv/library/windows/R-4.5/...`. Rodar `Rscript`/`quarto render` direto do
  terminal sem apontar para a 4.5.2 falha com "não há pacote chamado X", mesmo
  com o `renv.lock` restaurado. Para renderizar por fora do RStudio:
  ```bash
  R_HOME="/c/Program Files/R/R-4.5.2" PATH="/c/Program Files/R/R-4.5.2/bin:$PATH" \
    quarto render 00_auxiliares.qmd
  ```
- **Esquema real de `eleitorado_local_votacao_{ano}.csv`** (confirmado contra o
  arquivo de 2022 em 28/09/2026 — não adivinhar de novo, olhar o cabeçalho real
  antes de mudar `01_locais_votacao.qmd`):
  - **uma linha por seção por turno** (`NR_TURNO`), não uma linha por seção —
    `(CD_MUNICIPIO, NR_ZONA, NR_SECAO)` sozinho não é chave única, precisa de
    `NR_TURNO` junto. Em 2022, o local de votação difere entre turno 1 e 2 em
    1.387 das 496.856 seções (0,28%) — `secao_local_{ano}.parquet` guarda
    `nr_turno` por isso, e `resultados.secao_para_id_local()` (Python) junta
    por `(id_secao, turno)`, não só por `id_secao`.
  - **`DS_TIPO_SECAO_AGREGADA`** tem `Principal`, `Agregada` e `Distribuída de
    ofício`. Só `Agregada` tem `NR_SECAO_PRINCIPAL` de verdade (as outras vêm
    com `-1`, sentinela). Em ~2% das linhas `Agregada` (952 de 48.326 em 2022),
    o `NR_LOCAL_VOTACAO` da própria linha **diverge** do local da seção
    principal — resolver sempre pela seção principal (mesmo `nr_turno`), nunca
    pelo campo da própria linha. Ver `01_locais_votacao.qmd` > "Seções
    agregadas".
  - **`DS_TIPO_LOCAL`** (não `DS_SITU_LOCAL_VOTACAO`, que é status
    ativo/bloqueado) tem `Convencional`, `Temporário`, `Preso provisório` e
    `Voto em trânsito`. `flag_especial` (fora da distribuição espacial) é só
    `Preso provisório` e `Voto em trânsito`; `Temporário` fica dentro.
  - `CD_MUNICIPIO` vem com **5 dígitos e zero à esquerda** (`"01120"`). O CSV
    de origem do crosswalk (`yuripassuelo/codigos_mun_tse_ibge`) guarda
    `cd_mun_tse` **sem** esse padding (`"1120"`) — sem `str_pad(..., 5, "0")`
    no crosswalk, o join da etapa 01 falha silenciosamente para ~15% dos
    locais (não só o exterior). Corrigido em `00_auxiliares.qmd`.
  - Resultado real (2022, ambos os turnos): 993.712 seções×turno, 92.393 locais,
    262 flags especiais, 0 coordenadas nulas, 181 municípios sem crosswalk —
    **todos** com `SG_UF = "ZZ"` (exterior; ex.: Katmandu), como esperado.
- **`02_geocodificacao_locais.qmd` rodou de ponta a ponta com 2022** em
  28/09/2026, depois de quatro correções (ver histórico do commit para o
  detalhe de cada uma — não repetir os erros):
  - `valida_coordenada_tse` fazia um `st_intersects` por local num loop (~90
    mil iterações, refiltrando os 5.570 municípios a cada uma) — trocado por
    uma chamada vetorizada em modo esparso. O loop chegou a rodar por mais
    de 5 minutos sem terminar; a versão vetorizada leva segundos.
  - `dplyr::if_else()` avalia os dois ramos para o vetor inteiro antes de
    escolher — uma função chamada dentro dele (aqui, `calcula_distancia_m`)
    **não pode presumir** que só recebe as linhas do ramo escolhido; tem que
    tratar `NA` sozinha.
  - `select(fonte_coord, precisao = fonte_coord)` mantém só a última
    referência quando a mesma coluna de origem é citada duas vezes com nomes
    diferentes — a primeira (`fonte_coord`, sem renomear) desaparece em
    silêncio. Usar `mutate(nova = coluna)` antes do `select()` para ter as
    duas.
  - **Esquema real do painel Hidalgo** (`geocode_br_polling_stations` v0.16,
    confirmado em 28/09/2026 — não é o que eu tinha assumido sem checar):
    colunas `cd_localidade_tse` (sem zero à esquerda), `nr_zona`, `nr_locvot`,
    `pred_lat`/`pred_long` (a coordenada geocodificada, independente da do
    TSE). **Não usar** `lat`/`long`: essas colunas já vêm misturadas com a
    coordenada do TSE de outro snapshot/ano, o que dobraria a fonte errada
    na hora de aplicar a regra dos 800 m.
  - Faltava o último degrau do fallback (centróide do município) — sem ele,
    locais não resolvidos publicavam a coordenada **inválida** original do
    TSE (o sentinela `-1,-1`) como se fosse um ponto de verdade. Só apareceu
    inspecionando os `bounds` da saída (`longitude` batendo exatamente em
    `-1.0`) — vale sempre conferir os limites geográficos do resultado, não
    só a contagem por `fonte_coord`.
  - Resultado real (2022): 92.185 pontos na camada espacial (92.393 locais
    menos os 208 do exterior, fora do mapa por design). 91,5% mantém a
    coordenada do TSE, 7,7% usa o Hidalgo, 0,33% o `geocodebr`, 0,12% é TSE
    reprovado mas confirmado a <800 m da referência, e 0,23% (208) ainda cai
    no centróide do município — fora do mapa de pontos (`flag_sem_coord`).
    Antes do passo `geocodebr` (29/09/2026) eram 0,56% no centróide.
- **Etapas 03 (Python) e 04 (`04_resultados_turno.qmd`) rodaram para o Brasil
  inteiro com 2022** em 29/09/2026 — o resultado por candidato bate
  **exatamente** com os números oficiais reais (Lula 57.259.504, Bolsonaro
  51.072.345, comparecimento 123.682.372, aptos 156.451.515), depois de
  cinco correções:
  - **Malha de setores 2022:** não gerar cópia própria — usar direto
    `SETORES_2022_GPKG` (`../process_gtfs/Dados/BR_setores_CD2022.gpkg`,
    ~1,5 GB, já processado num repo irmão). `CD_SETOR` ali tem 15 dígitos.
  - **`COD_SETOR` do CNEFE tem um sufixo `"P"` fixo** (`"...5000002P"`, 16
    caracteres) que a malha oficial não tem — remover com `.str[:-1]`. Mesmo
    removendo, **~8–10% dos domicílios do CNEFE têm `cd_setor` que não
    existe na malha 2022** em nenhum lugar (varia de 0% a 96% por
    município, mediana ~5,5% — ver `alocacao.mede_cobertura_setor` e
    `cobertura_setor_cnefe_{ano}.parquet`), cadastro de endereços numa
    safra diferente da malha de resultados. Não tratado além de medir: esses
    domicílios não puxam peso (`pop18` implícito 0), a menos que **todos**
    os domicílios da célula sejam assim, caso em que o fallback de
    distribuição igual bota peso num setor sem geometria correspondente.
  - **`shapely.from_wkb` não aceita `bytearray`**, só `bytes`/`str` — o
    duckdb devolve colunas binárias como `bytearray` (a leitura direta via
    `pd.read_parquet` dá `bytes`, então isso só aparece ao filtrar por
    município com duckdb antes de decodificar). `decodifica_wkb` agora faz
    `.map(bytes)` antes de `shapely.from_wkb`.
  - **`constroi_zona_mun` colidia em `cd_mun`** no merge `setores.merge(setor_zona, ...)`
    (as duas tabelas têm a coluna, pandas sufixa `cd_mun_x`/`cd_mun_y` e o
    `dissolve(by=[col_mun, ...])` quebra com `KeyError: 'cd_mun'`) — não
    apareceu antes porque o `setores` sintético do teste manual não tinha
    `cd_mun` (só `cd_setor`), diferente da malha real. Corrigido selecionando
    só `[col_setor, "geometry"]` de `setores` antes do merge.
  - **`cod_setor` do `cn22-pop` precisa ser lido como texto** (mesma classe
    do bug do crosswalk): sem `col_types`, o `readr` adivinha "número" para
    um código de 15 dígitos, ele vira `double` (perde precisão E quebra o
    join com `cd_setor` em silêncio — 0% de correspondência, sem erro).
  - **`qt_eleitores` nunca tinha sido extraído em `01_locais_votacao.qmd`** —
    soma de `QT_ELEITOR_SECAO` por `id_local` (turno 1), usado pelo
    diagnóstico de alocação e, depois, como `qt_aptos` em `04_resultados_turno.qmd`
    (ver abaixo). Precisa passar pela etapa 02 também (`select()` da saída).
- **`detalhe_votacao_secao` existe, mas com nome diferente do que eu tinha
  chutado.** Eu tentei `detalhe_votacao_secao_{ano}_BR.zip` (copiando o
  padrão do `votacao_secao`) e tomei 404 repetidas vezes em 29/09/2026; o
  Gil achou o arquivo certo pelo portal humano
  (`dadosabertos.tse.jus.br/organization/tse-agel`, card "Detalhe da
  apuração por seção eleitoral"): o zip **não tem `_BR` no nome**
  (`detalhe_votacao_secao/detalhe_votacao_secao_{ano}.zip`), só os CSVs de
  dentro dele têm (`detalhe_votacao_secao_{ano}_BR.csv`, uma UF por arquivo
  + `_BR.csv` já filtrado para Presidente/`TP_ABRANGENCIA='F'` + `_BRASIL.csv`
  com todos os cargos). **Padrão de nome do TSE não é uniforme entre
  datasets — sempre confirmar no portal antes de adivinhar um nome de zip.**

  Esse arquivo tem `QT_APTOS`/`QT_COMPARECIMENTO`/`QT_ABSTENCOES` reais por
  seção×turno. Conferido nas 944.150 linhas de 2022 (Presidente, ambos os
  turnos): `QT_COMPARECIMENTO` bate **sem exceção** com a soma de
  `QT_VOTOS_NOMINAIS+BRANCOS+NULOS+LEGENDA+ANULADOS_APU_SEP` — confirma que
  o cálculo por soma de `qt_votos` (que já não dependia deste arquivo) está
  certo. O ganho real é `QT_APTOS`: a soma nacional do arquivo (156.454.011)
  bate bem mais perto do valor por seção do que a aproximação por
  `locais.qt_eleitores` (156.451.515, um `QT_ELEITOR_SECAO` só do turno 1,
  por local) — usar o arquivo derrubou a diferença de 2.496 para 657
  (~74%). `fontes.ler_votacao_secao()` aceita `caminho_detalhe` opcional: se
  informado, faz `LEFT JOIN` por `(cd_mun_tse, nr_zona, nr_secao, turno)` e
  usa `QT_APTOS`/`QT_COMPARECIMENTO` do arquivo, com `COALESCE` para a soma
  de `qt_votos` nas poucas seções sem match (~5%, provavelmente seções
  canceladas/não instaladas, que saem do `detalhe` mas ficam no
  `eleitorado_local_votacao`). `resultados.agrega_por_local()`/
  `agrega_por_zona_mun()` preferem esse `qt_aptos` por seção quando
  presente, caindo em `locais.qt_eleitores` só onde ele faltar. O arquivo é
  grande (243 MB) e opcional — `04_resultados_turno.qmd` só usa se já estiver em
  `dados/01_bruto/{ano}/detalhe_votacao_secao_{ano}.zip`, não baixa sozinho.
- **`CD_MUNICIPIO` não tem o mesmo padding em todos os arquivos do TSE.**
  Confirmado real: `eleitorado_local_votacao` sempre tem 5 dígitos
  (`"01120"`); `votacao_secao` **não** — varia de 2 a 5 dígitos (`"1120"`).
  Sem `lpad(cd_municipio, 5, '0')` em `fontes.ler_votacao_secao()`, ~10% das
  linhas não encontravam `id_local` (todo município com zero à esquerda no
  código). Mesma cautela em `ler_bweb` (especulativo, nunca testado contra
  um arquivo real — `.zfill(5)` defensivo).
- **`pandas.Series.astype(str)` numa coluna de texto nula (`pd.NA`) devolve
  `NaN` de verdade, não a string `"nan"`**, e a concatenação (`+`) propaga
  isso. `agrega_por_zona_mun()` construía `id_zona_mun` a partir de `cd_mun`
  (nulo para o exterior, ~208 locais) sem tratar isso — o `id_zona_mun`
  virava `NaN` para essas linhas, e `pivot_table()` **descarta grupos com
  índice `NaN` por padrão** (`dropna=True`). Resultado: ~294 mil votos do
  exterior sumiam do `res_..._zona_mun`, silenciosamente, sem erro — só
  visível comparando o total com `res_..._local` (que não tem esse problema,
  porque agrega por `id_local`, nunca nulo). Corrigido com
  `resultados.SENTINELA_EXTERIOR = "EXTERIOR"` (`.fillna()` antes da
  concatenação). **Sempre que uma chave de agregação for montada por
  concatenação de string, checar se alguma coluna de entrada pode ser nula
  — o pivot não avisa, só encolhe o total.**
- **`res_..._setor` tem menos votos que `res_..._local`/`res_..._zona_mun` por
  desenho, não por bug**: locais especiais (voto em trânsito, preso
  provisório) e locais sem nenhum domicílio do CNEFE alocado não entram na
  matriz de pesos, então seus votos não aparecem no setor (ver
  `resultados.agrega_por_setor`). Em 2022: 0,81% dos votos válidos+brancos+
  nulos ficam de fora do setor (122.682.541 vs. 123.682.372 no zona_mun) —
  ordem de grandeza esperada, dado que ~262 locais são especiais e ~2.062
  ficaram sem nenhum domicílio alocado (de 91.923).
- **Etapa 05, mapas (`src/eleicoes/mapas.py`, `06b_mapas_folium.qmd`),
  rodou pela primeira vez em 29/09/2026** — 27 mapas por UF (setor,
  `monta_setor_uf`), 1 nacional por município (`monta_municipios`) e 1
  nacional por zona×município (`monta_zona_mun`, a camada mais fiel — ver
  PLAN.md). ~352 MB no total, maior arquivo SP com 53,5 MB; zona×município
  (33,4 MB) e município (29,2 MB) cabem num arquivo nacional único porque
  têm muito menos polígonos que setor (6.102 e 5.570, contra 472.780).
  `geom_zona_mun_{ano}.gpkg` (saída da etapa 03) não vem simplificada — 136,5
  MB de GeoJSON bruto — mesma receita de `simplifica_geometria` resolve.
  Conferência de conservação: o mapa de zona×município tem 304.032 votos a
  menos que o arquivo de resultados — exatamente o total do `EXTERIOR0001`
  (o exterior, sem geometria mapeável, mesmo tratamento já dado em
  `monta_municipios`), não uma perda real. Achados reais no caminho:
  - **Um mapa nacional único em setor não cabe**: testado de verdade —
    Brasil inteiro simplificado (~20 m) dá **378,7 MB só de geometria**
    (GPKG binário; GeoJSON, o que o Folium usa, seria maior ainda). Por isso
    a divisão por UF (setor) + nacional por município (camada bem menor,
    5.570 polígonos).
  - **A malha de setores (`SETORES_2022_GPKG`) não tem sigla de 2 letras** —
    só `CD_UF` (código IBGE numérico) e `NM_UF`. Tentar filtrar por
    `SG_UF = 'SP'` dá `no such column: SG_UF`. Crosswalk sigla→código em
    `config.CD_UF_POR_SG_UF`.
  - **`gpd.read_file(..., where=..., columns=[...])` do pyogrio/GDAL ignora
    uma coluna do `where` se ela não estiver em `columns`, mesmo que pareça
    estar** — testado com `columns=["CD_SETOR", "SG_UF"]` (o nome errado,
    ver acima) e o SQL gerado só tinha `CD_SETOR`; o campo referenciado pelo
    `where` **tem** que estar em `columns` com o nome exato do schema, senão
    "no such column" mesmo constando na lista.
  - **Nunca usar um `folium.GeoJson`/`Popup` por feição num loop** — para
    dezenas de milhares de setores por UF, incha o HTML e trava o
    navegador. Uma única camada `folium.GeoJson(gdf_inteiro, style_function=...,
    tooltip=folium.GeoJsonTooltip(fields=[...]), popup=folium.GeoJsonPopup(fields=[...]))`
    é o padrão certo — `style_function` lê a propriedade de cada feição do
    próprio GeoJSON; os campos do tooltip/popup são colunas **já formatadas
    como texto** no GeoDataFrame antes de gerar o GeoJSON (Folium não
    formata valor por valor).
  - **CartoDB Basemaps agora exige API key** mesmo para o preset `"CartoDB
    positron"` do folium — sem ela, os tiles de fundo não carregam (só um
    warning, sem erro). Chave em `.env` (git-ignorado, ver `.env.example`),
    lida em `config.CARTO_API_KEY` via `python-dotenv`; `mapas.gera_mapa()`
    falha com mensagem clara se estiver ausente. **Nunca hardcodar a chave
    no código-fonte**, mesmo sendo de uso client-side (não é segredo de
    servidor, mas não deve ficar commitada).
  - **Tamanho do GeoJSON: o arredondamento de precisão pesa mais que a
    simplificação de forma.** Testado em SP (103.620 setores, o pior caso):
    só `shapely.simplify()` (tolerância 0,0002°) já dava 91 MB; simplificar
    + `shapely.set_precision(grid_size=0,0001°)` (arredonda coordenadas pra
    grade de ~10 m, os setores do IBGE vêm em float64 com ~15 dígitos
    significativos, muito além do que qualquer mapa mostra) caiu pra ~30 MB
    de geometria / 53,5 MB de HTML final. Uma fração pequena dos setores
    (0,24% em SP, tipicamente <0,2% nas outras UFs) vira polígono vazio
    nesse processo — `mapas.monta_setor_uf`/`monta_municipios` descartam
    essas linhas e logam quantas foram, não silenciam.
  - **`quarto render` do projeto inteiro (sem apontar um `.qmd`) limpa
    `docs/` por padrão antes de renderizar** — isso **apagou os 318 MB de
    mapas** na primeira tentativa, apesar de `project: resources: [mapas]`
    no `_quarto.yml` (isso só copia arquivos do projeto para o output-dir;
    não impede a limpeza de conteúdo que já está lá). O fix é `--no-clean`
    no `quarto render` do projeto inteiro, ou simplesmente gerar os mapas
    **depois** do render, nunca antes — ver README.md.
- **`geocodebr` na etapa 02** (0.6.1, mesmo pin do `locais-nova-rfb-geocoding`).
  Instalar pelo renv: o `duckdb` mais recente do CRAN tenta compilar do fonte
  e falha no Windows — usar a versão que já está no cache do renv
  (`renv::install("duckdb@1.5.4.3")`, binário linkado). Os dados do CNEFE
  padronizado ficam em `geocodebr::listar_pasta_cache()` (já baixados nesta
  máquina). Para registrar pacotes novos no `renv.lock` sem mexer no resto,
  `renv::record("pkg@versao")` — um `renv::snapshot()` completo tropeça no
  `shiny` (usado, não instalado).
- **`Rscript -e` com várias linhas falha em silêncio** no Git Bash desta
  máquina (nenhuma saída, exit 0). Escrever o R num arquivo e rodar
  `Rscript arquivo.R`.
- **Limite de 1 GB do GitHub Pages** (29/09/2026): os HTML Folium publicados
  já somavam ~573 MB só com 2022, e cada mapa novo custava ~30 MB. O site passou
  a usar um visualizador único com JSON compactos (~60 MB para 2018 + 2022).
  Mapas novos entram como dados do visualizador, não como HTML Folium.
- **Zona × município estimada tem de cobrir o município inteiro.** Setores
  sem adulto alocado (parques, represas, área rural) não aparecem na matriz de
  pesos; sem a regra do local mais próximo em `constroi_zona_mun`, ~24% da
  área dos municípios com várias zonas virava buraco (corrigido em
  29/09/2026). Ao mexer nessa função, conferir a área descoberta por
  município, não só a contagem de polígonos.
- **Continuidade das zonas estimadas** (`alocacao._torna_zonas_continuas`,
  30/09/2026). "Ilha" de zona vem da regra de maioria por setor, não de
  coordenada errada. Ao mexer nessa função, recontar as partes por zona com
  `notebooks/diagnostico_ilhas_zona_mun.ipynb` (categorias: sem local, sem zona
  vizinha, 1 local, 2+ locais) — não basta olhar o log. As três travas das
  pontes (local de outra zona, setor já movido, não partir outra zona) foram
  todas necessárias; tirar qualquer uma faz reaparecer oscilação ou exclave novo.
- **Eleições municipais, achados nos arquivos reais (11/10/2026):**
  - **Não existe `votacao_secao_{ano}_BR.zip`** em 2020/2024 (404): é um zip por UF,
    sem DF (`03b_baixa_resultados.qmd`). Cada CSV vira parquet só com as colunas
    usadas (`fontes.converte_zip_em_parquet`): são dezenas de GB descompactados.
  - **Os arquivos trazem as eleições suplementares** (`CD_TIPO_ELEICAO = 1`, 37 em
    2024, também com `NR_TURNO = 1`) junto da ordinária (`= 2`). Sem filtrar
    (`fontes.TIPO_ELEICAO_ORDINARIA`), o município que repetiu a eleição soma as duas.
    Vale também para `consulta_cand` (siglas) e `detalhe_votacao_secao`.
  - **O `detalhe_votacao_secao` tem uma linha por seção por cargo**: o join por seção
    precisa filtrar `cd_cargo`, senão duplica os votos.
  - **O número do partido muda de dono**: 25 era DEM em 2020 e é PRD em 2024; as
    siglas vêm do `consulta_cand_{ano}`, nunca de uma tabela fixa.
  - **Boa Esperança do Norte (MT, código TSE 73709)** foi emancipada depois da malha
    do IBGE de 2022: sem `cd_mun` no crosswalk, seus 3 locais ficam fora do mapa e
    os votos caem na linha `EXTERIOR` (conservam o total, não aparecem em mapa).
  - **Conferido contra o TSE:** Prefeito de São Paulo 2024 (Nunes 1.801.139, Boulos
    1.776.127, Marçal 1.719.274 no 1º turno; 3.393.110 × 2.323.901 no 2º) e o
    mais votado = eleito em todos os 5.477 municípios decididos no 1º turno.
- **Etapa 03 sem GeoDataFrame de domicílios (12/10/2026).** Montar um ponto shapely
  por domicílio (90,7 milhões) estourava a RAM do PC. `alocacao.conta_domicilios_por_local_setor`
  processa um município por vez com arrays numpy e guarda só as contagens por
  (local, setor): Brasil inteiro em ~4 min com pico de ~2,2 GB (antes ~44 min na
  alocação). A malha de setores só é lida para os municípios com mais de uma zona.
  Saída idêntica à anterior (conferida em Teresina).
- **Zona pelo tempo na rede (03c/03d, 12/10/2026, piloto).** `03c_recorta_osm.qmd`
  (Python, `osm.py`) baixa o OSM da região no Geofabrik e recorta as vias por
  município com várias zonas; `03d_tempos_rede.qmd` (R, r5r 2.4.0) calcula o tempo
  a pé do `point_on_surface` de cada setor até os locais do município (fallback de
  carro se >1% dos setores não alcançar nenhum); `03_pesos_alocacao.qmd -P usa_rede:true`
  traça a zona de cada setor pelo local de menor tempo e grava só
  `geom_zona_mun_{ano}_rede.gpkg` e `setor_zona_{ano}{sufixo}_rede.parquet` (pesos e
  04 não mudam). `-P rotulo:nome` troca a lista de códigos no sufixo dos arquivos
  (com dezenas de municípios o caminho estoura os 260 caracteres do Windows).
  `notebooks/compara_zonas_cem.py` compara com a divisão de zonas do CEM
  (`dados/01_bruto/cem/`: `EL2022_ZE_RMBRA_CEM_V1.zip`, 16 RMs; `EL2024_ZE_RMSP_CEM.zip`).
  Piloto 2022 contra o CEM (setores na mesma zona): Rio 91,0% (linha reta) → 91,6%
  (rede); São Paulo 92,9% → 93,2%. Ganho pequeno; pedaços secundários de zona não
  diminuem de forma clara no Rio (15 → 19, 6 deles de 1 local). Dos 189 municípios
  com várias zonas só 73 estão no arquivo do CEM (36,7% dos eleitores desses ficam de
  fora); lote `rms_cem_2022` = os 71 sem Rio e São Paulo
  (`dados/02_processado/aux/rms_cem_2022.txt`). Achados:
  - **O r5r descarta `options(java.parameters)`**: chama `rJava::.jinit()` com
    parâmetros próprios e o teto vira 1/4 da RAM (~8 GB aqui). Iniciar a JVM antes,
    com `rJava::.jinit(parameters = "-Xmx6g")`, e só então `library(r5r)`.
  - **Poda de pares, exata a pé:** em `max_min` minutos ninguém anda além de
    `max_min/60 × 3,6 km` em linha reta; cada bloco de setores vizinhos só recebe os
    locais dentro do retângulo + esse raio (1° de longitude = 111 km × cos(lat)).
    São Paulo foi de >30 min sem terminar para 7 min (pico de RAM 82%), e Teresina
    dá resultado idêntico com e sem a poda.
  - **Recorte do OSM: usar o `osmium-tool`, não o `pyosmium`.** O `BackReferenceWriter`
    do pyosmium passou de 12 GB só no sudeste (MemoryError). O `osmium extract` com
    `--config` e vários recortes também passou de 15 GB, mesmo com 5 municípios; um
    `osmium extract -b` por município leva ~3 s e quase não usa RAM (71 municípios em
    4,5 min). `tags-filter w/highway` da região leva ~16 s. Instalação (Windows):
    `micromamba create -n osm -c conda-forge osmium-tool` e `OSMIUM_BIN` no `.env`
    (ver `.env.example`).
  - **`Get-Counter '\Memory\Available MBytes'` não existe no Windows em português**
    (devolve 0 e parece 100% de RAM). Para vigiar RAM, usar `FreePhysicalMemory` do
    `Win32_OperatingSystem`.
  - O r5r devolve minutos inteiros: empates entre locais caem no menor `id_local`.
  - Experimento da limpeza de continuidade (`-P limite_ponte:0.15 -P permite_cortar:true`,
    `alocacao.PERMITE_PONTE_CORTAR_ZONA`): no Rio piora o encaixe com o CEM (área igual
    88,5% → 86,6%); em São Paulo não muda nada. Manter a limpeza padrão.
  - **Lote `rms_cem_2022` rodado (12/10/2026)**: 71 municípios, 03d com `memoria_gb:24`
    `bloco:2000` `max_min:60`, sem estouro de RAM (64 GB). Concordância com o CEM
    (setores na mesma zona, sem Rio e São Paulo capitais, cujos `setor_zona_*` próprios
    só existem na máquina que os rodou): 94,75% (linha reta) e 94,88% (rede); por RM de
    90,9% (Salvador) a 98,1% (Baixada Santista). Rede não ajuda de forma consistente
    (Curitiba e Recife pioram 0,0–0,4 p.p.).
  - **r5r pode devolver um bloco com `from_id`/`to_id` trocados** (visto em Belém,
    60 linhas: "setor" da saída era um local). `menores()` desfaz a troca antes de
    escolher os 5 menores; sem isso a conferência `id_local %in% locais` falha.
  - **As zonas do CEM respeitam os setores 2022 só em boa parte** (73 municípios, 125.641
    setores): 96,4% dos setores têm ≥95% da área numa zona, 2,5% são cortados (>5% em
    2+ zonas), e só 60% da fronteira interna fica a ≤15 m de uma borda de setor
    (São Paulo 82%, Brasília 40%, Viamão 11%). Tirar os setores divididos sobe a
    concordância só 0,8 p.p. (94,75% → 95,53%), então isso **não** explica a divergência
    (`notebooks/teste_concordancia_setores_inteiros.py`).
  - **Padrão das discordâncias** (Salvador, Belém, Curitiba; só setores inteiros;
    `notebooks/inspeciona_discordancias_cem.py`): ~80% ficam a <300 m da fronteira CEM
    (taxa de discordância 25–36% a <100 m, <2% a >300 m); em Salvador 55% estão em
    bolsões de >10 setores, com pares repetidos (zona 2 → 13, 8 → 13). Em ~72% dos casos o
    local da nossa zona está mais perto que o da zona CEM, ou seja, a fronteira real não
    é Voronoi dos locais (segue vias, bairros ou limites administrativos). As
    coordenadas dos locais estão certas (98–99% dentro da zona CEM do próprio local;
    Hidalgo em Salvador 96%). O teto do critério "local mais próximo" é ~95%; passar
    disso exige informação de fronteira (bairro, distrito) ou tratar a faixa de ~300 m
    como incerta.
  - **Ambiente (Windows)**: `quarto render` de `.qmd` Python com `-P` pede `papermill` no
    venv e `QUARTO_PYTHON` em formato Windows (`C:\...\venv\Scripts\python.exe`); mesmo
    assim a 03c falhou com `-P` e foi rodada com os parâmetros embutidos numa cópia
    temporária. `compara_zonas_cem.py` precisa de `dados/mapas_comparacao/` já criada.
    `osmium-tool` via Miniforge (`conda create -n osm -c conda-forge osmium-tool`).
- **`SETORES_2022_GPKG` tem 914 códigos de setor repetidos** (um setor em mais
  de um polígono, em 267 municípios). Qualquer merge por `cd_setor` com a malha
  precisa juntar os pedaços antes (`dissolve`), senão cruza linhas em silêncio.
- **`.gitignore`: `/dados/` ancorado na raiz.** Sem a barra inicial, a regra
  também pegava `docs/mapas/dados/` e o visualizador ia ao ar sem dados.
- **A chave do CARTO não tem efeito** (testado em 29/09/2026): o tile volta
  idêntico, com o aviso "API KEY REQUIRED", com ou sem `?api_key=`, de
  qualquer referer. O visualizador usa o Esri World Light Gray
  (`config.TILES_VISUALIZADOR`, sem chave). Os Folium locais continuam com
  CARTO e mostram o aviso.
- **2018 (29/09/2026):** `eleitorado_local_votacao_2018` tem o mesmo esquema
  de 2022 nas colunas usadas pela etapa 01 (baixa via R/httr2; o Python toma
  403 do CDN). O `votacao_secao_2018` tem o código **97 ("voto anulado e
  apurado em separado")** no 1º turno: não é candidato. Todo filtro de
  candidato usa `config.CODIGOS_NAO_CANDIDATO`, nunca `(95, 96)` escrito à mão —
  sem isso os 746 votos entram como válidos.
- **Bolsonaro muda de número entre eleições** (17 em 2018, 22 em 2022 —
  `config.NR_BOLSONARO_POR_ANO`). Nunca comparar `nr_vencedor` entre anos
  diretamente: o primeiro `mudou_vencedor` fazia isso e marcava como virada
  todo lugar que ele venceu duas vezes.
- **Mudança de votação tem duas bases (05/10/2026).** `var_pc_validos_pt` é o
  swing de Butler: com dois candidatos mostra quem ganhou terreno relativo, mas
  não vê mudança no tamanho do bolo (30×20 → 50×50 dá −10 p.p. embora Lula/Haddad tenha
  +66,7% de votos). As colunas sobre o **eleitorado apto** (`pc_aptos_*`,
  `var_pc_aptos_*`, `tipo_mudanca`; Lula/Haddad + Bolsonaro + não-voto = 100%, variações
  somam 0) mostram de onde veio o movimento. Os dois mapas ficam no site
  (`mudanca` e `mudanca_tipo`); a GeoReDUS ainda só tem a classe do swing.
  `tipo_mudanca` descreve o saldo agregado da zona, nunca eleitores que trocaram
  de lado. `mudanca_votacao` só gera as colunas novas se receber
  `nr_bolsonaro_antes/depois`.
- **A cor do mapa de mudança é de quem cresceu mais nos válidos (06/10/2026),
  não a variação de Lula/Haddad.** No 1º turno `var_pc_validos_pt` não espelha o
  bolsonarista: em 2022→2026, 112 zonas saíam vermelhas com Flávio crescendo mais
  (ex.: Osasco 213, Lula +0,6 p.p., Flávio +3,1). Regra (`cresceu_mais_validos`,
  `var_pc_validos_maior`, `cores.classifica_crescimento`): cor de quem cresceu
  mais, tom = o crescimento dele; se os dois caíram (76 zonas), vai para o 3º
  lugar (amarelo/ocre, corte em 5 p.p.) — o mais votado fora os dois em cada ano,
  mesmo não sendo a mesma pessoa. Legenda sempre "X ganhou", nunca "perdeu". No 2º
  turno o mapa é idêntico ao anterior.
- **Zonas 2018 × 2022 quase idênticas** (medido em 29/09/2026): mesmo
  conjunto de zonas nos 189 municípios com várias; 99,84% dos locais na mesma
  zona. Só 36 municípios de zona única trocaram o número —
  `resultados.alinha_zona_mun` casa esses antes de comparar anos.
- **2026, 1º turno (05/10/2026):** o TSE já publicou `eleitorado_local_votacao`,
  `votacao_secao` (só Presidente, T1) e `detalhe_votacao_secao`. Diferenças reais
  em relação a 2022: o zip do eleitorado traz um CSV por UF + `_BRASIL.csv`
  (a etapa 01 lê o consolidado); `NR_LATITUDE/NR_LONGITUDE` usam **vírgula
  decimal** (`"-22,7449552"`) e `as.numeric()` devolve NA em silêncio — sem o
  `sub(",", ".")` a etapa 02 caía 42% no centróide. O "bolsonarista" é Flávio
  (22). Etapas 01–04 de 2026 rodaram aqui (T1: Lula 53.879.538, Flávio
  56.104.503, bate com o `votacao_secao`; 98,4% das coordenadas do TSE; 03 leva
  ~55 min, 44 só na alocação dos domicílios). `06_mudanca_votacao.py` aceita
  `--turno` (2022→2026 no 1º turno: Lula × bolsonarista, demais candidatos no
  "não-voto" da base dos aptos).
