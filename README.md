# dados_eleicoes

Pipeline ETL de dados eleitorais do TSE (locais de votação e resultados) para a
[GeoReDUS](https://www.redus.org.br/georedus), com série histórica 2018–2022–2026 e
foco na eleição presidencial.

Veja o [PLAN.md](PLAN.md) para o contexto completo, a pesquisa sobre como distribuir
os resultados no território (locais → setores censitários) e o cronograma.

## O que este repositório produz

- **Locais de votação georreferenciados** (ponto), com validação e fallback de
  coordenada.
- **Resultados de Presidente** em quatro agregações, ligadas por id compartilhado à
  respectiva geometria:
  - por **local de votação** (observado);
  - por **zona eleitoral × município** (observado, mesma agregação do TSE em
    `votacao_candidato_munzona`);
  - por **município** (observado, soma da zona×município);
  - por **setor censitário 2022** (estimado, por distribuição dasimétrica usando o
    CNEFE como dado auxiliar).
- **Mudança de votação entre anos** (2º turno, Δ p.p. de Lula/Haddad + virada), por zona ×
  município.
- **Visualizador de mapas** no site (página `mapas.qmd`): pontos por local e
  zona × município, com legenda e seletor de mapa/ano/turno.
- Artefatos de carga para o banco da GeoReDUS.

## Stack

- **R + Quarto + renv** — locais de votação e geocodificação (`00_auxiliares.qmd`,
  `01_locais_votacao.qmd`, `02_geocodificacao_locais.qmd`).
- **Python + Quarto** — resultados, distribuição espacial e exportação
  (`03_*.qmd` a `08_*.qmd`; as funções ficam em `src/eleicoes/`).

## Organização

Um notebook Quarto por etapa, na raiz, numerado na ordem de execução. O
processamento fica nos notebooks; `src/eleicoes/` só tem as funções que eles
chamam.

| Notebook | Linguagem | O que faz | Parâmetros (`-P`) |
|---|---|---|---|
| `00_auxiliares.qmd` | R | crosswalk TSE↔IBGE, municípios, `pop18` por setor | — |
| `00b_insumos_brasil.qmd` | R | malha de setores e CNEFE do Brasil | `etapa`, `uf` |
| `01_locais_votacao.qmd` | R | locais e seções, chaves | `ano` |
| `02_geocodificacao_locais.qmd` | R | validação e fallback de coordenada | `ano` |
| `03_pesos_alocacao.qmd` | Python | matriz de pesos local → setor, zona × município | `ano`, `cd_mun` |
| `04_resultados_turno.qmd` | Python | resultados por local, zona × município, município e setor | `ano`, `turno`, `fonte`, `baixar` |
| `04b_resultados_municipio.qmd` | Python | resultado por município direto da seção | `ano`, `turno`, `fonte`, `baixar` |
| `05_mudanca_votacao.qmd` | Python | mudança de votação entre dois anos | `de`, `para`, `turno` |
| `06_exporta_visualizador.qmd` | Python | dados do visualizador do site | — |
| `06b_mapas_folium.qmd` | Python | mapas Folium, só uso local | `ano`, `turno`, `uf`, `camadas` |
| `07_carga_georedus.qmd` | Python | artefatos de carga no banco da GeoReDUS | `anos`, `turnos`, `saida` |
| `08_cadastro_georedus.qmd` | Python | linhas de cadastro das camadas | `anos`, `turnos`, `saida` |
| `09_insumos_municipio.qmd` | R | insumos de um município (perfil × voto) | `cd_mun`, `ano` |

Só `index.qmd`, `00`, `01`, `02` e `mapas.qmd` são páginas do site (`docs/`). Os
demais são de execução local: o HTML sai na raiz, ao lado do `.qmd`, e é
git-ignorado. Cada um termina numa seção "Conferência", com as checagens
daquela etapa.

## Reprodutibilidade

```bash
# R (00 uma vez; 01 e 02 por ano — o ano padrão é 2022)
Rscript -e "renv::restore()"
quarto render 00_auxiliares.qmd
quarto render 01_locais_votacao.qmd
quarto render 02_geocodificacao_locais.qmd
# outro ano: saída com outro nome, para não sobrescrever a página de 2022
quarto render 01_locais_votacao.qmd -P ano:2018 --output 01_locais_votacao_2018.html
quarto render 02_geocodificacao_locais.qmd -P ano:2018 --output 02_geocodificacao_locais_2018.html

# Python (por ano). O Quarto usa o kernel do venv: ative-o antes, ou aponte
# QUARTO_PYTHON para venv/Scripts/python.exe
python -m venv venv
venv\Scripts\activate.bat        # PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # preencher CARTO_API_KEY antes de gerar os mapas Folium
quarto render 03_pesos_alocacao.qmd -P ano:2022
quarto render 04_resultados_turno.qmd -P ano:2022 -P turno:1
quarto render 04_resultados_turno.qmd -P ano:2022 -P turno:2

# Eleições municipais (2020, 2024): os zips de resultado vêm da 03b (um por UF);
# a 04 roda por cargo (prefeito, vereador) e turno
quarto render 03b_baixa_resultados.qmd -P ano:2024
quarto render 04_resultados_turno.qmd -P ano:2024 -P turno:1 -P cargo:prefeito
quarto render 04_resultados_turno.qmd -P ano:2024 -P turno:2 -P cargo:prefeito
quarto render 04_resultados_turno.qmd -P ano:2024 -P turno:1 -P cargo:vereador

# Mudança de votação entre anos (zona × município; turno 2 é o padrão)
quarto render 05_mudanca_votacao.qmd -P de:2018 -P para:2022

# Dados do visualizador do site (docs/mapas/dados/) e a página
quarto render 06_exporta_visualizador.qmd
quarto render mapas.qmd

# Opcional, só local: mapas Folium por setor/município/zona (dados/mapas_folium/)
quarto render 06b_mapas_folium.qmd -P ano:2022 -P turno:1

# Carga e cadastro na GeoReDUS
quarto render 07_carga_georedus.qmd -P anos:2018,2022,2026
quarto render 08_cadastro_georedus.qmd -P anos:2018,2022,2026
```

`04b_resultados_municipio.qmd` calcula o resultado por município direto da seção,
sem precisar das etapas 01–03 — útil para um ano ainda não processado e como
validação cruzada.

`dados/`, `venv/` e `renv/library/` são git-ignorados — ver `PLAN.md` para as fontes
e como baixá-los. `.env` (a chave do CARTO) também é git-ignorado — ver `.env.example`.

**Atenção ao rodar `quarto render` do projeto inteiro (sem apontar um `.qmd`
específico):** ele só renderiza as páginas do site, mas por padrão limpa `docs/`
antes, e isso apaga `docs/mapas/dados/` (gerado por
`06_exporta_visualizador.qmd`). Renderize só os `.qmd` alterados, passe
`--no-clean`, ou rode o exportador de novo depois do render.

## Agradecimentos

Este trabalho é desenvolvido no Centro de Estudos da Metrópole (CEM/USP e CEBRAP),
para alimentar a plataforma GeoReDUS, com apoio da FAPESP (processo 2025/15643-1),
do Instituto ORI:ORO, da GIZ e da Frente Nacional de Prefeitas e Prefeitos (FNP).
