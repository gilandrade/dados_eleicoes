"""Inferência ecológica bayesiana multinível, com pós-estratificação.

Problema: de cada zona eleitoral se conhece a composição do eleitorado (parte de
cada grupo escolaridade × renda) e o voto total em cada opção, mas não o voto de
cada grupo. Estima-se, por modelo hierárquico, a taxa de voto de cada grupo e,
em seguida, pós-estratifica: a taxa de um grupo num escopo (UF, região, país) é a
média das taxas estimadas nas zonas, ponderada pelos eleitores do grupo em cada zona.

**O que isto estima.** São *estimativas ecológicas*: associações condicionais entre
a composição das zonas e o voto, sob as hipóteses do modelo. Não são efeitos
causais da escolaridade nem medidas diretas do comportamento individual — os dados
sozinhos deixam as taxas dos grupos quase indeterminadas (ver `limites_deterministicos`).

Modelo (opções `c`, com a última como referência; grupos `g = escolaridade × renda`):

    η[z, g, c] = α[c] + a[e(g), c] + b[r(g), c] + ab[e(g), r(g), c]
                 + u[uf(z), c] + v[uf(z), e(g), c]
    θ[z, g, :] = softmax(η[z, g, :])                  voto do grupo g na zona z
    p[z, :]    = Σ_g W[z, g] θ[z, g, :]               voto esperado da zona
    Y[z, :]   ~ DirichletMultinomial(φ · p[z, :], N[z])

`a`, `b`, `ab`, `u` e `v` têm priors normais com desvio-padrão hierárquico
(encolhimento para zero), em parametrização não centrada. A concentração `φ`
(aprendida) dá a superdispersão: com contagens de milhares de eleitores por zona, uma
multinomial pura trataria o voto como determinístico e forçaria os efeitos dos grupos
a extremos. Nenhum termo captura heterogeneidade *sistemática* da zona (por exemplo, a
taxa de um grupo depender do contexto em que vive): essa é a hipótese central do
modelo, e a simulação de `_teste_manual_ei.py` mostra o que acontece quando ela falha.
"""

from __future__ import annotations

from typing import Any

import jax
import jax.numpy as jnp
import numpy as np
import numpyro
import numpyro.distributions as dist
from numpyro.infer import SVI, Trace_ELBO, init_to_median
from numpyro.infer.autoguide import AutoNormal
from numpyro.optim import Adam


def limites_deterministicos(
    composicao: np.ndarray, votos: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Limites do método dos limites (Duncan e Davis) para a taxa de cada grupo.

    Para cada grupo `g` e opção `c`, o voto do grupo na zona está entre
    `max(0, Y_c − (N − N_g))` e `min(N_g, Y_c)`. Somando nas zonas e dividindo
    pelos eleitores do grupo, saem os limites da taxa agregada. Mostram o quanto
    os dados sozinhos restringem a resposta — em geral, quase nada.

    Args:
        composicao: contagens de eleitores por zona e grupo, forma `(Z, G)`.
        votos: contagens por zona e opção, forma `(Z, C)`; cada linha soma `N`.

    Returns:
        `(inferior, superior)`, cada um com forma `(G, C)`, em fração.
    """
    n_zona = composicao.sum(axis=1)
    resto = (n_zona[:, None] - composicao)[:, :, None]
    y = votos[:, None, :]
    inferior = np.maximum(0.0, y - resto).sum(axis=0)
    superior = np.minimum(composicao[:, :, None], y).sum(axis=0)
    n_grupo = composicao.sum(axis=0)[:, None]
    return inferior / n_grupo, superior / n_grupo


def _bloco(nome: str, forma: tuple[int, ...], escala: jnp.ndarray) -> jnp.ndarray:
    """Efeito normal não centrado, com desvio-padrão `escala` já amostrado."""
    return escala * numpyro.sample(f"{nome}_z", dist.Normal(0.0, 1.0).expand(forma).to_event(len(forma)))


def modelo(
    peso: jnp.ndarray,
    votos: jnp.ndarray | None,
    n_zona: jnp.ndarray,
    uf: jnp.ndarray,
    n_uf: int,
    n_esc: int,
    n_renda: int,
    interacao: bool = True,
    contexto: jnp.ndarray | None = None,
) -> None:
    """Modelo hierárquico (ver o módulo). Grupo `g = e * n_renda + r`.

    Args:
        peso: composição `W`, forma `(Z, G)`, cada linha soma 1.
        votos: contagens `Y`, forma `(Z, C)`; `None` para simular do prior.
        n_zona: eleitores por zona `N`, forma `(Z,)`.
        uf: índice da UF de cada zona, forma `(Z,)`, em `0..n_uf-1`.
        n_uf: número de UFs.
        n_esc: número de faixas de escolaridade.
        n_renda: número de faixas de renda.
        interacao: inclui o termo escolaridade × renda (`ab`). Sem ele, os efeitos
            de escolaridade e renda são aditivos no logit.
        contexto: covariáveis da zona (padronizadas), forma `(Z, K)`, ou `None`. Entram
            como um deslocamento do logit comum a todos os grupos da zona
            (`η += contexto @ γ`). É a defesa contra o viés ecológico mais comum: o
            contexto em que a zona vive (renda média da área, por exemplo) altera o voto
            de todos os grupos e também a composição, e sem ele o modelo atribui esse
            efeito aos grupos.
    """
    n_zonas, n_grupos = peso.shape
    n_op = votos.shape[1] if votos is not None else 5
    k = n_op - 1  # a última opção é a referência

    esc = jnp.repeat(jnp.arange(n_esc), n_renda)
    renda = jnp.tile(jnp.arange(n_renda), n_esc)

    alfa = numpyro.sample("alfa", dist.Normal(0.0, 2.0).expand((k,)).to_event(1))
    s_esc = numpyro.sample("s_esc", dist.HalfNormal(1.0).expand((k,)).to_event(1))
    s_renda = numpyro.sample("s_renda", dist.HalfNormal(1.0).expand((k,)).to_event(1))
    s_uf = numpyro.sample("s_uf", dist.HalfNormal(0.5).expand((k,)).to_event(1))
    s_uf_esc = numpyro.sample("s_uf_esc", dist.HalfNormal(0.3).expand((k,)).to_event(1))
    log_phi = numpyro.sample("log_phi", dist.Normal(np.log(200.0), 1.5))

    a = _bloco("a", (n_esc, k), s_esc)
    b = _bloco("b", (n_renda, k), s_renda)
    u = _bloco("u", (n_uf, k), s_uf)
    v = _bloco("v", (n_uf, n_esc, k), s_uf_esc)

    eta = (
        alfa
        + a[esc]                      # (G, k)
        + b[renda]
    )[None, :, :] + u[uf][:, None, :] + v[uf][:, esc, :]
    if contexto is not None:
        gama = numpyro.sample("gama", dist.Normal(0.0, 1.0).expand((contexto.shape[1], k)).to_event(2))
        eta = eta + (contexto @ gama)[:, None, :]
    if interacao:
        s_int = numpyro.sample("s_int", dist.HalfNormal(0.3).expand((k,)).to_event(1))
        ab = _bloco("ab", (n_esc, n_renda, k), s_int)
        eta = eta + ab[esc, renda][None, :, :]
    numpyro.deterministic("eta", eta) if votos is None else None

    eta_completo = jnp.concatenate([eta, jnp.zeros((n_zonas, n_grupos, 1))], axis=-1)
    theta = jax.nn.softmax(eta_completo, axis=-1)            # (Z, G, C)
    p = jnp.einsum("zg,zgc->zc", peso, theta)                # (Z, C)

    phi = jnp.exp(log_phi)
    with numpyro.plate("zonas", n_zonas):
        numpyro.sample(
            "y",
            dist.DirichletMultinomial(concentration=phi * p + 1e-3, total_count=jnp.asarray(n_zona)),
            obs=votos,
        )


def ajusta_svi(
    peso: np.ndarray,
    votos: np.ndarray,
    uf: np.ndarray,
    n_esc: int,
    n_renda: int,
    *,
    interacao: bool = True,
    contexto: np.ndarray | None = None,
    passos: int = 4000,
    taxa: float = 0.02,
    semente: int = 0,
) -> dict[str, Any]:
    """Ajusta o modelo por inferência variacional (guia normal com campo médio).

    O guia `AutoNormal` subestima a incerteza (ignora a correlação entre
    parâmetros); por isso os intervalos daqui são otimistas e o resultado deve ser
    conferido com NUTS numa região (ver o plano) antes de ser interpretado.

    Returns:
        Dicionário com `guia`, `parametros`, `perda` (ELBO por passo) e os dados
        usados, para `amostra_posterior`.
    """
    n_zona = votos.sum(axis=1)
    dados = dict(
        peso=jnp.asarray(peso, dtype=jnp.float32),
        votos=jnp.asarray(votos, dtype=jnp.float32),
        n_zona=jnp.asarray(n_zona, dtype=jnp.float32),
        uf=jnp.asarray(uf, dtype=jnp.int32),
        n_uf=int(uf.max()) + 1,
        n_esc=n_esc,
        n_renda=n_renda,
        interacao=interacao,
        contexto=None if contexto is None else jnp.asarray(contexto, dtype=jnp.float32),
    )
    # começa com todos os efeitos em zero (mediana do prior): a inicialização aleatória
    # padrão cai em soluções extremas, em que cada grupo vota 0% ou 100%
    guia = AutoNormal(modelo, init_loc_fn=init_to_median(num_samples=15), init_scale=0.05)
    svi = SVI(modelo, guia, Adam(taxa), Trace_ELBO())
    estado = svi.init(jax.random.PRNGKey(semente), **dados)

    def passo(estado, _):
        estado, perda = svi.update(estado, **dados)
        return estado, perda

    estado, perda = jax.lax.scan(passo, estado, None, length=passos)
    return {
        "guia": guia,
        "parametros": svi.get_params(estado),
        "perda": np.asarray(perda),
        "dados": dados,
    }


def amostra_posterior(ajuste: dict[str, Any], n: int = 200, semente: int = 1) -> dict[str, jnp.ndarray]:
    """Amostras da posterior aproximada (parâmetros do modelo, sem `y`)."""
    return ajuste["guia"].sample_posterior(
        jax.random.PRNGKey(semente), ajuste["parametros"], sample_shape=(n,)
    )


def taxas_por_grupo(
    ajuste: dict[str, Any],
    amostras: dict[str, jnp.ndarray],
    escopos: dict[str, np.ndarray],
    *,
    bloco: int = 20,
) -> dict[str, np.ndarray]:
    """Pós-estratificação: taxa de voto de cada grupo em cada escopo.

    A taxa do grupo `g` na opção `c`, no escopo `s`, é
    `Σ_z N_z W_zg θ_zgc / Σ_z N_z W_zg`, somando as zonas do escopo.

    Args:
        ajuste: saída de `ajusta_svi`.
        amostras: saída de `amostra_posterior`.
        escopos: nome -> índices (ou máscara booleana) das zonas do escopo.
        bloco: quantas amostras da posterior processar por vez (memória).

    Returns:
        nome do escopo -> array `(amostras, G, C)` de taxas (frações; cada linha
        `(g, :)` soma 1).
    """
    d = ajuste["dados"]
    n_amostras = next(iter(amostras.values())).shape[0]
    peso, n_zona = d["peso"], d["n_zona"]
    n_grupos = peso.shape[1]
    saida = {nome: [] for nome in escopos}
    massa = (peso * n_zona[:, None])  # eleitores do grupo em cada zona (Z, G)

    def theta_bloco(amostras_bloco):
        def uma(am):
            sem_y = {k: v for k, v in am.items() if k != "eta"}
            traco = numpyro.handlers.trace(numpyro.handlers.substitute(
                numpyro.handlers.seed(modelo, 0), data=sem_y)).get_trace(
                    d["peso"], None, d["n_zona"], d["uf"], d["n_uf"], d["n_esc"], d["n_renda"], d["interacao"], d["contexto"])
            eta = traco["eta"]["value"]
            eta = jnp.concatenate([eta, jnp.zeros((*eta.shape[:2], 1))], axis=-1)
            return jax.nn.softmax(eta, axis=-1)
        return jax.vmap(uma)(amostras_bloco)

    for ini in range(0, n_amostras, bloco):
        parte = {k: v[ini:ini + bloco] for k, v in amostras.items()}
        theta = theta_bloco(parte)                                    # (b, Z, G, C)
        for nome, idx in escopos.items():
            m = massa[idx]                                            # (z, G)
            num = jnp.einsum("zg,bzgc->bgc", m, theta[:, idx])
            saida[nome].append(np.asarray(num / m.sum(axis=0)[None, :, None]))
    return {nome: np.concatenate(partes, axis=0) for nome, partes in saida.items()}


def resume(taxas: np.ndarray, nivel: float = 0.9) -> dict[str, np.ndarray]:
    """Média e intervalo de credibilidade das taxas `(amostras, G, C)`."""
    cauda = 100 * (1 - nivel) / 2
    return {
        "media": taxas.mean(axis=0),
        "inferior": np.percentile(taxas, cauda, axis=0),
        "superior": np.percentile(taxas, 100 - cauda, axis=0),
    }
