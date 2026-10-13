"""Concordância com o CEM só nos setores que o CEM não divide.

Pergunta: a divergência com o CEM some quando se tiram os setores que a zona do
CEM corta ao meio (as zonas do CEM não respeitam exatamente a malha 2022)?
Para cada setor: zona CEM dominante (maior área) e fração da área nela.
  - todos: concordância usando a zona dominante (sem depender do ponto representativo)
  - inteiros: só setores com >= LIMITE_INTEIRO da área numa zona do CEM
Uso: python notebooks/teste_concordancia_setores_inteiros.py
"""
import geopandas as gpd
import pandas as pd
import shapely
from pathlib import Path

CRS_METRICO_BR = 5880
RES = "dados/03_georedus/resultados/"
OUT = "dados/mapas_comparacao/"
CEM_2022 = "dados/01_bruto/cem/ze2022_rmbra/EL2022_ZE_RMBRA_CEM_V1.shp"
ROTULO_LOTE = "rms_cem_2022"
SOLO = ("3304557", "3550308")
LIMITE_INTEIRO = 0.95
SUFIXO = {"Linha reta": "", "Tempo na rede": "_rede"}

cem = gpd.read_file(CEM_2022, columns=["ZE_NUM", "CD_MUN_I", "RME_NOME"]).to_crs(CRS_METRICO_BR)
cem["cd_mun"] = cem["CD_MUN_I"].astype(int).astype(str)
cem["geometry"] = shapely.make_valid(cem.geometry)
nz = cem.groupby("cd_mun")["ZE_NUM"].nunique()
pop18 = pd.read_parquet("dados/02_processado/aux/pop18_setor.parquet")


def le_setor_zona(cd, suf):
    solo = f"{RES}setor_zona_2022_{cd}{suf}.parquet"
    lote = f"{RES}setor_zona_2022_{ROTULO_LOTE}{suf}.parquet"
    if cd in SOLO and Path(solo).exists():
        return pd.read_parquet(solo)
    t = pd.read_parquet(lote)
    return t.loc[t["cd_mun"] == cd]


linhas = []
for cd in sorted(nz[nz > 1].index):
    rm = cem.loc[cem.cd_mun == cd, "RME_NOME"].iloc[0].title()
    s = gpd.read_file("dados/01_bruto/aux/setores/BR_setores_CD2022.gpkg",
                      where=f"CD_MUN = '{cd}'", columns=["CD_SETOR", "CD_MUN"])
    s = s.dissolve(by="CD_SETOR", as_index=False).to_crs(CRS_METRICO_BR)
    s["geometry"] = shapely.make_valid(s.geometry)
    s["area"] = s.area
    i = gpd.overlay(s[["CD_SETOR", "geometry"]], cem.loc[cem.cd_mun == cd, ["ZE_NUM", "geometry"]],
                    how="intersection", keep_geom_type=True)
    i["a"] = i.area
    dom = i.sort_values("a", ascending=False).drop_duplicates("CD_SETOR").set_index("CD_SETOR")
    s = s.merge(dom[["ZE_NUM", "a"]].rename(columns={"ZE_NUM": "z_cem", "a": "a_dom"}), on="CD_SETOR", how="left")
    s["frac"] = s["a_dom"] / s["area"]
    s = s.merge(pop18, left_on="CD_SETOR", right_on="cd_setor", how="left")
    s["pop18"] = s["pop18"].fillna(0)
    for ab, suf in SUFIXO.items():
        sz = le_setor_zona(cd, suf)
        if sz.empty:  # Rio e São Paulo: arquivo próprio, só existe na máquina que os rodou
            continue
        sz = sz.rename(columns={"cd_setor": "CD_SETOR", "nr_zona": "z"})
        t = s.merge(sz[["CD_SETOR", "z"]], on="CD_SETOR", how="left")
        t = t[t["z_cem"].notna()]
        t["ig"] = t["z"] == t["z_cem"]
        for grupo, d in (("todos", t), ("inteiros", t[t["frac"] >= LIMITE_INTEIRO]), ("divididos", t[t["frac"] < LIMITE_INTEIRO])):
            linhas.append(dict(cd_mun=cd, rm=rm, abordagem=ab, grupo=grupo, n=len(d), n_ig=int(d["ig"].sum()),
                               pop=d["pop18"].sum(), pop_ig=d.loc[d["ig"], "pop18"].sum()))
    print(cd, rm, len(s), flush=True)

r = pd.DataFrame(linhas)
r.to_csv(f"{OUT}concordancia_setores_inteiros_municipios.csv", index=False)
g = r.groupby(["rm", "abordagem", "grupo"])[["n", "n_ig", "pop", "pop_ig"]].sum()
g["setores_%"] = 100 * g["n_ig"] / g["n"]
g["pop18_%"] = 100 * g["pop_ig"] / g["pop"]
w = g["setores_%"].unstack("grupo")[["todos", "inteiros", "divididos"]].round(1)
w["n_div_%"] = (100 * g["n"].unstack("grupo")["divididos"] / g["n"].unstack("grupo")["todos"]).round(1)
w.to_csv(f"{OUT}concordancia_setores_inteiros_por_rm.csv")
tot = r.groupby(["abordagem", "grupo"])[["n", "n_ig", "pop", "pop_ig"]].sum()
tot["setores_%"] = (100 * tot["n_ig"] / tot["n"]).round(2)
tot["pop18_%"] = (100 * tot["pop_ig"] / tot["pop"]).round(2)
pd.set_option("display.width", 250)
print(w.to_string()); print(tot[["n", "setores_%", "pop18_%"]].to_string())
