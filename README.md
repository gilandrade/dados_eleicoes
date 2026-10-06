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
  zona × município, 2018 e 2022, com legenda e seletor de mapa/ano/turno.
- Artefatos de carga para o banco da GeoReDUS.

## Stack

- **R + Quarto + renv** — locais de votação e geocodificação (`00_auxiliares.qmd`,
  `01_locais_votacao.qmd`, `02_geocodificacao_locais.qmd`).
- **Python** — resultados, distribuição espacial e exportação
  (`src/eleicoes/`, `scripts/`).

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

# Python (por ano)
python -m venv venv
venv\Scripts\activate.bat        # PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # preencher CARTO_API_KEY antes de exportar os mapas
python scripts/03_pesos_alocacao.py --ano 2022
python scripts/processa_turno.py --ano 2022 --turno 1 --fonte votacao_secao
python scripts/processa_turno.py --ano 2022 --turno 2 --fonte votacao_secao

# Mudança de votação entre anos (2º turno, zona × município)
python scripts/06_mudanca_votacao.py --de 2018 --para 2022

# Dados do visualizador do site (docs/mapas/dados/) e a página
python scripts/05_exporta_visualizador.py
quarto render mapas.qmd

# Opcional, só local: mapas Folium por setor/município/zona (dados/mapas_folium/)
python scripts/05_gera_mapas.py --ano 2022 --turno 1
```

`scripts/processa_municipio.py` calcula o resultado por município direto da seção,
sem precisar das etapas 01–03 — útil para um ano ainda não processado e como
validação cruzada.

`dados/`, `venv/` e `renv/library/` são git-ignorados — ver `PLAN.md` para as fontes
e como baixá-los. `.env` (a chave do CARTO) também é git-ignorado — ver `.env.example`.

**Atenção ao rodar `quarto render` do projeto inteiro (sem apontar um `.qmd`
específico):** por padrão ele limpa `docs/` antes de renderizar, e isso apaga
`docs/mapas/dados/` (gerado por `scripts/05_exporta_visualizador.py`, fora do
motor do Quarto). Renderize só os `.qmd` alterados, passe `--no-clean`, ou rode o
exportador de novo depois do render.

## Agradecimentos

Este trabalho é desenvolvido no Centro de Estudos da Metrópole (CEM/USP e CEBRAP),
para alimentar a plataforma GeoReDUS, com apoio da FAPESP (processo 2025/15643-1),
do Instituto ORI:ORO, da GIZ e da Frente Nacional de Prefeitas e Prefeitos (FNP).
