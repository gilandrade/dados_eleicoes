# dados_eleicoes

Pipeline ETL de dados eleitorais do TSE (locais de votação e resultados) para a
[GeoReDUS](https://www.redus.org.br/georedus), com série histórica 2018–2022–2026 e
foco na eleição presidencial.

Veja o [PLAN.md](PLAN.md) para o contexto completo, a pesquisa sobre como distribuir
os resultados no território (locais → setores censitários) e o cronograma.

## O que este repositório produz

- **Locais de votação georreferenciados** (ponto), com validação e fallback de
  coordenada.
- **Resultados de Presidente** em três agregações, ligadas por id compartilhado à
  respectiva geometria:
  - por **local de votação** (observado);
  - por **zona eleitoral × município** (observado, mesma agregação do TSE em
    `votacao_candidato_munzona`);
  - por **setor censitário 2022** (estimado, por distribuição dasimétrica usando o
    CNEFE como dado auxiliar).
- Artefatos de carga para o banco da GeoReDUS e mapas Folium de prévia.

## Stack

- **R + Quarto + renv** — locais de votação e geocodificação (`00_auxiliares.qmd`,
  `01_locais_votacao.qmd`, `02_geocodificacao_locais.qmd`).
- **Python** — resultados, distribuição espacial e exportação
  (`src/eleicoes/`, `scripts/`).

## Reprodutibilidade

```bash
# R
Rscript -e "renv::restore()"
quarto render 00_auxiliares.qmd
quarto render 01_locais_votacao.qmd
quarto render 02_geocodificacao_locais.qmd

# Python
python -m venv venv
venv\Scripts\activate.bat        # PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/03_pesos_alocacao.py --ano 2022
python scripts/processa_turno.py --ano 2022 --turno 1 --fonte votacao_secao
```

`dados/`, `venv/` e `renv/library/` são git-ignorados — ver `PLAN.md` para as fontes
e como baixá-los.

## Agradecimentos

Este trabalho é desenvolvido no Centro de Estudos da Metrópole (CEM/USP e CEBRAP),
para alimentar a plataforma GeoReDUS, com apoio da FAPESP (processo 2025/15643-1),
do Instituto ORI:ORO, da GIZ e da Frente Nacional de Prefeitas e Prefeitos (FNP).
