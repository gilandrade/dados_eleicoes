#!/usr/bin/env python
"""Etapa 12 — células e renda por área de ponderação, dos microdados da amostra do Censo 2022.

Lê o zip de acesso controlado (uma pasta por UF, com `Pessoas` e `Domicilios` ligadas
por `Controle`) e grava **só contagens agregadas por área de ponderação**, sem
nenhum registro de pessoa ou domicílio:

  - `celulas_censo_ap.parquet`: pessoas em idade de votar por área de ponderação ×
    escolaridade (4 faixas) × renda domiciliar per capita (3 faixas);
  - `renda_ap.parquet`: por área de ponderação, a renda domiciliar per capita média e
    mediana (ponderadas) e as proporções por faixa de renda.

População: 16 anos ou mais (idade mínima para votar) e não estrangeiros (`P0520 != 3`).
Os pesos são os da versão controlada (`P0111`). Pessoas sem renda per capita
(domicílio sem declaração) ficam fora das células e são contadas em `pop_sem_renda`.

Cada UF é extraída para uma pasta temporária e apagada logo depois de lida: o CSV
descompactado dos microdados controlados não fica em disco.

As contagens por célula são ponderadas e vêm com `n_amostra` (pessoas da amostra). Células
pequenas têm erro amostral grande; as regras da sala de acesso podem exigir revisão ou
supressão antes de a saída ser usada fora da máquina autorizada.

Uso:
    python scripts/12_celulas_censo_amostra.py
    python scripts/12_celulas_censo_amostra.py --zip <caminho> --tmp-dir <pasta>
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
import pandas as pd

from eleicoes.config import (
    CORTES_RENDA_PC_SM,
    ESCOLARIDADE_CENSO_PARA_FAIXA,
    MICRODADOS_CENSO_ZIP,
    PROCESSADO_DIR,
    SALARIO_MINIMO_2022,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IDADE_MINIMA_VOTO = 16


def _csv(caminho: Path) -> str:
    """`read_csv` do DuckDB para um CSV do Censo: `;`, latin-1, tudo texto."""
    return (
        f"read_csv('{str(caminho).replace(chr(92), '/')}', delim=';', header=true, "
        "all_varchar=true, encoding='latin-1')"
    )


def _expr_faixa_escolaridade() -> str:
    casos = " ".join(f"when {k} then {v}" for k, v in ESCOLARIDADE_CENSO_PARA_FAIXA.items())
    return f"case try_cast(p.P0760 as int) {casos} end"


def processa_uf(con: duckdb.DuckDBPyConnection, pessoas: Path, domicilios: Path) -> dict[str, pd.DataFrame]:
    """Células e estatísticas por área de ponderação de uma UF.

    Returns:
        `celulas` (área × escolaridade × renda), `renda` (por área) e `controle` (totais
        para conferência: população total ponderada e descartes).
    """
    corte_baixo = CORTES_RENDA_PC_SM[0] * SALARIO_MINIMO_2022
    corte_alto = CORTES_RENDA_PC_SM[1] * SALARIO_MINIMO_2022

    con.sql(f"""
        create or replace temp table base as
        select
            p.P0090 as cd_ap,
            p.P0080 as cd_mun,
            try_cast(p.P0181 as int) as idade,
            p.P0520 as nacionalidade,
            {_expr_faixa_escolaridade()} as esc,
            try_cast(d.D0360 as double) as renda_pc,
            try_cast(p.P0111 as double) as peso
        from {_csv(pessoas)} p
        join {_csv(domicilios)} d on p.P0100 = d.D0100
    """)

    controle = con.sql("""
        select
            sum(peso) as pop_total,
            sum(case when idade >= {i} and nacionalidade <> '3' then peso else 0 end) as pop_elegivel,
            sum(case when idade >= {i} and nacionalidade <> '3' and esc is null then peso else 0 end) as pop_sem_instrucao,
            sum(case when idade >= {i} and nacionalidade <> '3' and renda_pc is null then peso else 0 end) as pop_sem_renda,
            count(*) as n_amostra
        from base
    """.format(i=IDADE_MINIMA_VOTO)).df()

    con.sql(f"""
        create or replace temp table elegiveis as
        select cd_ap, cd_mun, esc, renda_pc, peso,
               case when renda_pc <= {corte_baixo} then 1
                    when renda_pc <= {corte_alto} then 2
                    else 3 end as faixa_renda
        from base
        where idade >= {IDADE_MINIMA_VOTO} and nacionalidade <> '3'
          and esc is not null and renda_pc is not null
    """)

    celulas = con.sql("""
        select cd_ap, any_value(cd_mun) as cd_mun, esc, faixa_renda as renda,
               sum(peso) as n_pond, count(*) as n_amostra
        from elegiveis group by cd_ap, esc, faixa_renda
    """).df()

    renda = con.sql("""
        with acum as (
            select cd_ap, renda_pc, peso,
                   sum(peso) over (partition by cd_ap order by renda_pc) as cum,
                   sum(peso) over (partition by cd_ap) as tot
            from elegiveis
        ),
        mediana as (
            select cd_ap, min(renda_pc) filter (where cum >= 0.5 * tot) as renda_pc_mediana
            from acum group by cd_ap
        ),
        resumo as (
            select cd_ap, any_value(cd_mun) as cd_mun,
                   sum(peso) as pop16_pond, count(*) as n_amostra,
                   sum(peso * renda_pc) / sum(peso) as renda_pc_media,
                   sum(case when faixa_renda = 1 then peso else 0 end) / sum(peso) as pc_renda_baixa,
                   sum(case when faixa_renda = 3 then peso else 0 end) / sum(peso) as pc_renda_alta
            from elegiveis group by cd_ap
        )
        select r.*, m.renda_pc_mediana from resumo r join mediana m using (cd_ap)
    """).df()
    return {"celulas": celulas, "renda": renda, "controle": controle}


def main(caminho_zip: Path, tmp_dir: Path) -> None:
    inicio = time.time()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    celulas, renda, controle = [], [], []

    with zipfile.ZipFile(caminho_zip) as z:
        ufs = sorted({n.split("/")[0] for n in z.namelist() if n.endswith("Pessoas_" + n.split("/")[0] + "_controlado.csv")})
        for uf in ufs:
            extraidos = []
            try:
                for tabela in ("Pessoas", "Domicilios"):
                    nome = f"{uf}/{tabela}_{uf}_controlado.csv"
                    destino = tmp_dir / Path(nome).name
                    with z.open(nome) as origem, open(destino, "wb") as saida:
                        shutil.copyfileobj(origem, saida, length=1 << 24)
                    extraidos.append(destino)
                res = processa_uf(con, extraidos[0], extraidos[1])
            finally:
                for arquivo in extraidos:
                    arquivo.unlink(missing_ok=True)
            celulas.append(res["celulas"])
            renda.append(res["renda"])
            controle.append(res["controle"].assign(uf=uf))
            c = res["controle"].iloc[0]
            logger.info(
                "[%5.1f min] UF %s: pop %.0f, 16+ não estrangeira %.0f, %d áreas de ponderação",
                (time.time() - inicio) / 60, uf, c["pop_total"], c["pop_elegivel"], len(res["renda"]),
            )

    celulas = pd.concat(celulas, ignore_index=True)
    renda = pd.concat(renda, ignore_index=True)
    controle = pd.concat(controle, ignore_index=True)

    aux = PROCESSADO_DIR / "aux"
    aux.mkdir(parents=True, exist_ok=True)
    celulas.to_parquet(aux / "celulas_censo_ap.parquet", index=False)
    renda.to_parquet(aux / "renda_ap.parquet", index=False)

    total = controle[["pop_total", "pop_elegivel", "pop_sem_instrucao", "pop_sem_renda", "n_amostra"]].sum()
    logger.info("População total ponderada: %.0f (Censo 2022: 203.080.756)", total["pop_total"])
    logger.info(
        "16+ não estrangeira: %.0f | sem instrução válida: %.0f | sem renda per capita: %.0f (%.2f%%)",
        total["pop_elegivel"], total["pop_sem_instrucao"], total["pop_sem_renda"],
        100 * total["pop_sem_renda"] / total["pop_elegivel"],
    )
    pequenas = (celulas["n_amostra"] < 30).mean()
    logger.info(
        "%d áreas de ponderação, %d células; %.1f%% das células com menos de 30 pessoas na amostra.",
        celulas["cd_ap"].nunique(), len(celulas), 100 * pequenas,
    )
    logger.info("[%5.1f min] Concluído.", (time.time() - inicio) / 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, default=MICRODADOS_CENSO_ZIP, dest="caminho_zip")
    parser.add_argument(
        "--tmp-dir", type=Path, default=Path(tempfile.gettempdir()) / "censo_amostra_tmp",
        help="pasta temporária para o CSV de uma UF por vez (apagado depois de lido)",
    )
    args = parser.parse_args()
    main(args.caminho_zip, args.tmp_dir)
