// Visualizador de mapas da página mapas.qmd.
// Lê os JSON gerados por scripts/05_exporta_visualizador.py (docs/mapas/dados/).
// A classe de cor de cada feição já vem calculada em Python (src/eleicoes/cores.py);
// aqui só se pinta, monta a legenda e o tooltip.
(function () {
  "use strict";

  const BASE = "mapas/dados/";

  // `tamanho`: coluna que define a área da bolinha — votos válidos (Presidente),
  // votos totais (brancos e nulos) e inscritos (abstenção), o denominador de cada %.
  const MAPAS = {
    pontos_presidente: {
      tipo: "pontos", classe: "c_pres", cores: "div", legendaPorAno: true,
      tamanho: "val", rotuloTamanho: "votos válidos",
    },
    pontos_branco_nulo: {
      tipo: "pontos", classe: "c_bn", cores: "seq", tamanho: "comp", rotuloTamanho: "votos (total)",
    },
    pontos_abstencao: {
      tipo: "pontos", classe: "c_abst", cores: "seq", tamanho: "aptos", rotuloTamanho: "inscritos",
    },
    zona_mun_presidente: { tipo: "zona_mun", cores: "div", legendaPorAno: true },
    mudanca: { tipo: "mudanca", cores: "div" },
    mudanca_tipo: { tipo: "mudanca", cores: "tipo", posClasse: 5 },
  };
  const ehMapaMudanca = (chave) => MAPAS[chave].tipo === "mudanca";

  const el = (id) => document.getElementById(id);
  const cache = {};
  const carrega = (arquivo) =>
    (cache[arquivo] ??= fetch(BASE + arquivo).then((r) => {
      if (!r.ok) throw new Error(`${arquivo}: HTTP ${r.status}`);
      return r.json();
    }));

  const fmtPct = (v) =>
    v == null ? "—" : (v / 10).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
  const fmtPp = (v) =>
    v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") +
      (Math.abs(v) / 10).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " p.p.";
  const fmtCresc = (v) =>
    v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") +
      (Math.abs(v) / 10).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
  const fmtNum = (v) => (v == null ? "—" : v.toLocaleString("pt-BR"));
  const escapa = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  let config, mapa, camada, legenda, tooltip, geracao = 0;

  // ==== Estado (seletores <-> #hash) =========================================

  function lerHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const v = (p.get("v") || "").split(",").map(Number);
    return {
      mapa: p.get("mapa"),
      ano: Number(p.get("ano")) || null,
      turno: Number(p.get("turno")) || null,
      par: p.get("par"),
      vista: v.length === 3 && v.every(Number.isFinite) ? v : null,
    };
  }

  // O link guarda também a vista (centro e zoom), para compartilhar um recorte.
  function gravarHash(estado) {
    estado = estado || estadoAtual();
    const p = new URLSearchParams({ mapa: estado.mapa });
    if (ehMapaMudanca(estado.mapa)) p.set("par", estado.par);
    else {
      p.set("ano", estado.ano);
      p.set("turno", estado.turno);
    }
    const c = mapa.getCenter();
    p.set("v", `${c.lat.toFixed(4)},${c.lng.toFixed(4)},${mapa.getZoom()}`);
    history.replaceState(null, "", "#" + p.toString());
  }

  function combinacoes(chaveMapa) {
    const tipo = MAPAS[chaveMapa].tipo;
    return tipo === "pontos" ? config.disponivel.pontos : config.disponivel.zona_mun;
  }

  function preencheSeletores(estado) {
    const selAno = el("sel-ano");
    const selTurno = el("sel-turno");
    if (ehMapaMudanca(estado.mapa)) {
      // Aqui "ano" é o par de eleições ("2018-2022-2"): o turno vem junto.
      const pares = config.disponivel.mudanca.map(([a, b, t]) => ({ id: `${a}-${b}-${t}`, a, b, t }));
      if (!pares.some((p) => p.id === estado.par)) estado.par = pares[pares.length - 1].id;
      selAno.innerHTML = pares.map((p) => `<option value="${p.id}">${p.a} → ${p.b} (${p.t}º turno)</option>`).join("");
      selAno.value = estado.par;
      const atual = pares.find((p) => p.id === estado.par);
      selTurno.innerHTML = `<option>${atual.t}º</option>`;
      selAno.disabled = false;
      selTurno.disabled = true;
      return estado;
    }
    selAno.disabled = selTurno.disabled = false;
    const combos = combinacoes(estado.mapa);
    const anos = [...new Set(combos.map(([a]) => a))].sort();
    if (!anos.includes(estado.ano)) estado.ano = anos[anos.length - 1];
    const turnos = combos.filter(([a]) => a === estado.ano).map(([, t]) => t).sort();
    if (!turnos.includes(estado.turno)) estado.turno = turnos[turnos.length - 1];
    selAno.innerHTML = anos.map((a) => `<option value="${a}">${a}</option>`).join("");
    selTurno.innerHTML = turnos.map((t) => `<option value="${t}">${t}º</option>`).join("");
    selAno.value = estado.ano;
    selTurno.value = estado.turno;
    return estado;
  }

  function estadoAtual() {
    const mapaSel = el("sel-mapa").value;
    if (ehMapaMudanca(mapaSel)) return { mapa: mapaSel, par: el("sel-ano").value };
    return { mapa: mapaSel, ano: Number(el("sel-ano").value), turno: Number(el("sel-turno").value) };
  }

  // ==== Legenda ==============================================================

  function specLegenda(estado) {
    const chave = estado.mapa;
    const spec = config.legendas[chave];
    if (ehMapaMudanca(chave)) return spec[estado.par];
    return MAPAS[chave].legendaPorAno ? spec[String(estado.ano)] : spec;
  }

  function desenhaLegenda(spec, estado) {
    const itens = spec.itens
      .map((i) => `<div class="leg-item"><span class="leg-cor" style="background:${i.cor}"></span>${escapa(i.rotulo)}</div>`)
      .join("");
    const semDado = `<div class="leg-item"><span class="leg-cor" style="background:${spec.sem_dado}"></span>Sem dado</div>`;
    const nota = spec.nota ? `<div class="leg-nota">${escapa(spec.nota)}</div>` : "";
    const tamanho = MAPAS[estado.mapa].tamanho ? legendaTamanho(MAPAS[estado.mapa]) : "";
    legenda.getContainer().innerHTML =
      `<div class="leg-titulo">${escapa(spec.titulo)}</div>${itens}${semDado}${nota}${tamanho}`;
  }

  // Círculos de referência desenhados com o mesmo raio do mapa no zoom atual.
  function legendaTamanho(spec) {
    const raios = VALORES_LEGENDA_TAMANHO.map(raioPorValor);
    const lado = 2 * Math.ceil(Math.max(...raios)) + 2;
    const itens = VALORES_LEGENDA_TAMANHO.map((v, k) => {
      const r = raios[k];
      return `<div class="leg-item leg-tamanho">
        <svg width="${lado}" height="${lado}" aria-hidden="true">
          <circle cx="${lado / 2}" cy="${lado / 2}" r="${r}" fill="#898781" fill-opacity="0.55" stroke="#52514e" stroke-width="0.8"/>
        </svg>${fmtNum(v)}</div>`;
    }).join("");
    return `<div class="leg-titulo leg-titulo-tamanho">Tamanho: ${escapa(spec.rotuloTamanho)}</div>${itens}`;
  }

  // ==== Camadas ===============================================================

  function corDaClasse(classe, paleta) {
    const cores = paleta === "seq" ? config.cores_sequenciais
      : paleta === "tipo" ? config.cores_tipo_mudanca : config.cores_divergentes;
    return classe >= 0 ? cores[classe] : config.cor_sem_dado;
  }

  // Raio (px) de um local com REF_TAMANHO votos: cresce ~25% por nível de zoom.
  // Mais rápido que isso, as cidades densas viram uma massa só ao aproximar.
  const raioPonto = () => Math.min(10, 0.9 * Math.pow(1.25, mapa.getZoom() - 4));
  const RAIO_MINIMO_PX = 1;
  const RAIO_MINIMO_CONTORNO_PX = 3;

  // Símbolo proporcional: a área é proporcional ao valor (raio ∝ √valor), com
  // referência fixa — um local com REF_TAMANHO votos tem o raio base do zoom —
  // para o mesmo tamanho valer o mesmo nos dois anos e turnos. Piso e teto
  // mantêm os locais minúsculos visíveis e os gigantes (~20 mil votos) sem
  // cobrir o bairro inteiro.
  const REF_TAMANHO = 1000;
  const VALORES_LEGENDA_TAMANHO = [250, 1000, 5000];
  function raioPorValor(valor) {
    const base = raioPonto();
    const escala = valor > 0 ? Math.min(3.5, Math.max(0.35, Math.sqrt(valor / REF_TAMANHO))) : 0.35;
    return Math.max(RAIO_MINIMO_PX, base * escala);
  }

  // Contorno branco só nas bolinhas maiores: nas pequenas ele engole a cor.
  const estiloTamanho = (raio) => ({ radius: raio, weight: raio >= RAIO_MINIMO_CONTORNO_PX ? 0.6 : 0 });

  function mostraTooltip(latlng, html) {
    tooltip.setLatLng(latlng).setContent(html);
    if (!mapa.hasLayer(tooltip)) tooltip.addTo(mapa);
  }
  const escondeTooltip = () => tooltip.remove();

  function maisVotado(venc, candidatos, ano) {
    const conhecidos = [config.nr_pt, config.nr_bolsonaro[String(ano)]];
    if (venc == null || conhecidos.includes(venc)) return "";
    return `<br>Mais votado: ${escapa(candidatos[String(venc)] || venc)}`;
  }

  function linhaPresidente(pt, bo, ano) {
    return `${escapa(config.nome_pt[String(ano)])}: <b>${fmtPct(pt)}</b> · ${escapa(config.nome_bolsonaro[String(ano)])}: <b>${fmtPct(bo)}</b>`;
  }

  async function camadaPontos(estado) {
    const { ano, turno } = estado;
    const spec = MAPAS[estado.mapa];
    const [geo, res] = await Promise.all([carrega(`locais_${ano}.json`), carrega(`locais_${ano}_t${turno}.json`)]);
    const renderer = L.canvas({ padding: 0.5, tolerance: 3 });
    const grupo = L.featureGroup();
    const classes = res[spec.classe];
    const valores = res[spec.tamanho];

    // Maiores primeiro: os pequenos ficam por cima e continuam clicáveis.
    const ordem = geo.id.map((_, i) => i).sort((a, b) => (valores[b] ?? 0) - (valores[a] ?? 0));
    for (const i of ordem) {
      const marcador = L.circleMarker([geo.lat[i], geo.lon[i]], {
        renderer,
        ...estiloTamanho(raioPorValor(valores[i])),
        color: "#ffffff",
        opacity: 0.8,
        fillColor: corDaClasse(classes[i], spec.cores),
        fillOpacity: 0.8,
      });
      marcador._i = i;
      grupo.addLayer(marcador);
    }

    const texto = (i) => {
      const cabecalho = `<b>${escapa(geo.nome[i] || geo.id[i])}</b><br>${escapa(geo.municipios[geo.mun[i]])}<br>`;
      if (estado.mapa === "pontos_branco_nulo")
        return cabecalho + `Brancos e nulos: <b>${fmtPct(res.bn[i])}</b><br>Comparecimento: ${fmtNum(res.comp[i])} votos`;
      if (estado.mapa === "pontos_abstencao")
        return cabecalho + `Abstenção: <b>${fmtPct(res.abst[i])}</b><br>Inscritos: ${fmtNum(res.aptos[i])}`;
      return cabecalho + linhaPresidente(res.pt[i], res.bo[i], ano) + maisVotado(res.venc[i], res.candidatos, ano) +
        `<br>Votos válidos: ${fmtNum(res.val[i])}`;
    };
    grupo.on("mouseover", (e) => mostraTooltip(e.layer.getLatLng(), texto(e.layer._i)));
    grupo.on("mouseout", escondeTooltip);
    grupo._ajustaRaio = () => {
      grupo.eachLayer((l) => l.setStyle(estiloTamanho(raioPorValor(valores[l._i]))));
      desenhaLegenda(specLegenda(estado), estado);
    };
    return grupo;
  }

  async function camadaPoligonos(estado) {
    const ehMudanca = ehMapaMudanca(estado.mapa);
    const posClasse = MAPAS[estado.mapa].posClasse || 0;
    const paleta = MAPAS[estado.mapa].cores;
    const [anoAntes, anoDepois, turnoMud] = ehMudanca ? estado.par.split("-").map(Number) : [null, estado.ano, null];
    const arquivoDados = ehMudanca
      ? `mudanca_${anoAntes}_${anoDepois}_t${turnoMud}.json`
      : `zona_mun_${estado.ano}_t${estado.turno}.json`;
    const [geo, res] = await Promise.all([carrega(`zona_mun_${anoDepois}_geo.json`), carrega(arquivoDados)]);
    const dados = res.dados;

    const estilo = (f) => {
      const d = dados[f.properties.id];
      const virou = ehMudanca && d && d[4] === 1;
      return {
        fillColor: corDaClasse(d ? d[posClasse] : -1, paleta),
        fillOpacity: 0.85,
        color: virou ? "#1f1f1f" : "#ffffff",
        weight: virou ? 1.6 : 0.4,
      };
    };

    const texto = (p) => {
      const d = dados[p.id];
      let html = `<b>${escapa(p.rot)}</b><br>`;
      if (!d) return html + "Sem dado";
      if (ehMudanca) {
        html +=
          `PT em ${anoAntes} (${escapa(config.nome_pt[String(anoAntes)])}): ${fmtPct(d[1])}<br>` +
          `PT em ${anoDepois} (${escapa(config.nome_pt[String(anoDepois)])}): ${fmtPct(d[2])}<br>` +
          `Variação nos votos válidos: <b>${fmtPp(d[3])}</b>` +
          (d[4] === 1 ? "<br><b>Trocou de vencedor</b>" : "") +
          `<br><span class="tt-nota">Sobre o eleitorado inscrito:</span>` +
          `<br>${escapa(config.nome_pt[String(anoDepois)])}: <b>${fmtPp(d[6])}</b> (votos ${fmtCresc(d[9])})` +
          `<br>${escapa(config.nome_bolsonaro[String(anoDepois)])}: <b>${fmtPp(d[7])}</b> (votos ${fmtCresc(d[10])})` +
          `<br>Abstenção, brancos e nulos${turnoMud === 1 ? " e outros candidatos" : ""}: <b>${fmtPp(d[8])}</b>`;
      } else {
        html += linhaPresidente(d[1], d[2], estado.ano) + maisVotado(d[5], res.candidatos, estado.ano) +
          `<br>Comparecimento: ${fmtNum(d[4])} votos`;
      }
      if (p.est) html += '<br><span class="tt-nota">Fronteira interna estimada (cidade com várias zonas)</span>';
      return html;
    };

    const renderer = L.canvas({ padding: 0.5 });
    const camadaGeo = L.geoJSON(geo, { renderer, style: estilo });
    camadaGeo.on("mouseover", (e) => {
      e.layer.setStyle({ weight: 2.2, color: "#1f1f1f" });
      mostraTooltip(e.latlng, texto(e.layer.feature.properties));
    });
    camadaGeo.on("mousemove", (e) => tooltip.setLatLng(e.latlng));
    camadaGeo.on("mouseout", (e) => {
      camadaGeo.resetStyle(e.layer);
      escondeTooltip();
    });
    return camadaGeo;
  }

  // ==== Orquestração ==========================================================

  async function desenha() {
    const estado = preencheSeletores(estadoAtual());
    gravarHash(estado);
    const minha = ++geracao;
    el("mapa-carregando").hidden = false;
    try {
      const nova = MAPAS[estado.mapa].tipo === "pontos" ? await camadaPontos(estado) : await camadaPoligonos(estado);
      if (minha !== geracao) return; // outra seleção chegou antes
      escondeTooltip();
      if (camada) mapa.removeLayer(camada);
      camada = nova.addTo(mapa);
      desenhaLegenda(specLegenda(estado), estado);
    } catch (erro) {
      console.error(erro);
      el("mapa-erro").textContent = "Não foi possível carregar este mapa: " + erro.message;
      el("mapa-erro").hidden = false;
      return;
    } finally {
      if (minha === geracao) el("mapa-carregando").hidden = true;
    }
    el("mapa-erro").hidden = true;
  }

  async function inicia() {
    config = await carrega("config.json");

    mapa = L.map("mapa", { preferCanvas: true, zoomControl: true }).setView([-15.8, -52], 4);
    L.tileLayer(config.tiles.url, { attribution: config.tiles.attr, maxZoom: 18 }).addTo(mapa);
    L.control.scale({ imperial: false }).addTo(mapa);
    legenda = L.control({ position: "bottomright" });
    legenda.onAdd = () => L.DomUtil.create("div", "mapa-legenda");
    legenda.addTo(mapa);
    tooltip = L.tooltip({ direction: "top", offset: [0, -6], className: "mapa-tooltip" });
    mapa.on("zoomend", () => camada && camada._ajustaRaio && camada._ajustaRaio());

    const inicial = lerHash();
    if (inicial.vista) mapa.setView([inicial.vista[0], inicial.vista[1]], inicial.vista[2]);
    mapa.on("moveend", () => gravarHash());
    if (inicial.mapa && MAPAS[inicial.mapa]) el("sel-mapa").value = inicial.mapa;
    preencheSeletores({ mapa: el("sel-mapa").value, ano: inicial.ano, turno: inicial.turno, par: inicial.par });

    el("sel-mapa").addEventListener("change", desenha);
    el("sel-ano").addEventListener("change", desenha);
    el("sel-turno").addEventListener("change", desenha);
    await desenha();
  }

  inicia().catch((erro) => {
    console.error(erro);
    el("mapa-erro").textContent = "Não foi possível iniciar o visualizador: " + erro.message;
    el("mapa-erro").hidden = false;
  });
})();
