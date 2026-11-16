/* Courtside HQ — League Winners tab (shared by the NBA, NFL and MLB pages).
   Reads data/winners_<sport>.json, written nightly by scripts/refresh_winners.py. */
(function () {
  "use strict";
  const CACHE = {};
  const STATE = {};
  const e = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (x, f) => {
    if (x == null || x === '' || isNaN(x)) return '–';
    x = +x;
    if (f === '+1') return (x > 0 ? '+' : '') + x.toFixed(1);
    if (f === '1') return x.toFixed(1);
    if (f === '2') return x.toFixed(2);
    return Math.round(x).toString();
  };
  const css = `
  .lw-bars{display:flex;flex-direction:column;gap:7px;margin:6px 0 2px}
  .lw-bar{display:grid;grid-template-columns:minmax(92px,150px) minmax(80px,1fr) auto;align-items:center;gap:10px;font-size:12.5px}
  .lw-bar .tr{background:#eef1f5;border-radius:5px;height:16px;position:relative;overflow:hidden}
  .lw-bar .fl{background:var(--acc);height:100%;border-radius:5px}
  .lw-bar .n{color:var(--mute);font-size:12px}
  .lw-sum{margin:6px 0 4px 0;padding-left:18px}
  .lw-sum li{margin:3px 0}
  .lw-ck{display:inline-block;width:18px;text-align:center;font-weight:700}
  .lw-ck.y{color:var(--good)} .lw-ck.n{color:#c5cad3}
  .lw-legend{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:8px;margin:4px 0 12px}
  .lw-legend div{background:#f7f8fa;border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12.5px}
  .lw-legend b{display:block;margin-bottom:2px}
  .lw-tag{display:inline-block;font-size:10.5px;font-weight:600;border-radius:4px;padding:1px 5px;margin-left:5px;background:#eef1f5;color:#3b4250}
  td.lw-up{color:var(--good);font-weight:700} td.lw-dn{color:var(--bad);font-weight:700}
  @media(max-width:1100px){.lw-grid{grid-template-columns:1fr!important}} .lw-grid>div{min-width:0}`;
  function addCss() {
    if (document.getElementById('lw-css')) return;
    const s = document.createElement('style'); s.id = 'lw-css'; s.textContent = css; document.head.appendChild(s);
  }

  function cell(c, r) {
    let v = r[c.k], f = c.fmt;
    if (r.kind === 'P' && c.k === 'xgap') f = '2';
    if (c.k === 'pos' || f == null) return `<td class="l">${e(v == null ? '' : v)}</td>`;
    if (c.k === 'profit') return `<td class="${v > 0 ? 'lw-up' : 'lw-dn'}">${v > 0 ? '+' : ''}${fmt(v, f)}</td>`;
    if (c.k === 'cost' && v == null) return `<td title="Not drafted">UD</td>`;
    return `<td>${fmt(v, f)}</td>`;
  }
  const head = cols => cols.map(c => `<th class="${c.k === 'pos' ? 'l' : ''}" title="${e(c.tip || '')}">${e(c.label)}</th>`).join('');
  const nameCell = r => `<td class="l"><b>${e(r.n)}</b><span class="meta">${e(r.t || '')}</span></td>`;

  function anatomy(d) {
    const a = d.anatomy, maxPct = Math.max(1, ...a.bands.map(b => b.winPct));
    const bars = a.bands.map(b => `<div class="lw-bar"><div>${e(b.label)}</div><div class="tr"><div class="fl" style="width:${100 * b.winPct / maxPct}%"></div></div>
      <div><b>${b.winPct}%</b> <span class="n">(${b.winners}${b.hitRate != null ? ` · ${b.hitRate}% hit` : ''})</span></div></div>`).join('');
    const traits = (a.traits || []).length ? `<div class="tw" style="margin-top:12px"><table><thead><tr><th class="l">Trait</th><th>League winners</th><th>All drafted players</th></tr></thead><tbody>
      ${a.traits.map(t => `<tr><td class="l">${e(t.label)}</td><td><b>${fmt(t.winners, t.fmt === '0' ? '1' : t.fmt)}</b></td><td>${fmt(t.all, t.fmt === '0' ? '1' : t.fmt)}</td></tr>`).join('')}</tbody></table></div>` : '';
    const usage = a.usage ? `<div class="tw" style="margin-top:12px"><table><thead><tr><th class="l">Position</th><th class="l">Usage measure</th><th title="In the season they won leagues">League winners</th><th title="Same players, the season before">Winners, year before</th><th title="Players drafted in rounds 1-12">Drafted players</th></tr></thead><tbody>
      ${Object.entries(a.usage).map(([p, u]) => `<tr><td class="l"><b>${p}</b></td><td class="l">${e(u.label)}</td><td><b>${fmt(u.winners, '1')}</b></td><td>${fmt(u.winners_prev, '1')}</td><td>${fmt(u.drafted, '1')}</td></tr>`).join('')}</tbody></table></div>
      <p class="sub" style="margin-top:8px">League winners usually had a bigger role than the year before: the jump in usage is what the market misses.</p>` : '';
    return `<div class="card"><div class="sec-h"><h2>🏆 Anatomy of a league winner</h2><span class="meta">${e(a.span)} · ${a.n} league winners</span></div>
      <p class="sub">${e(d.intro)}</p>
      <ul class="lw-sum">${a.summary.map(s => `<li>${e(s)}</li>`).join('')}</ul>
      <div class="grid2 lw-grid" style="margin-top:12px"><div><h3>Where league winners were drafted</h3><p class="sub">Share of all league winners from each part of the draft. "Hit" is the share of players drafted there who became one.</p><div class="lw-bars">${bars}</div></div>
      <div>${traits}</div></div>${usage}</div>`;
  }

  function targets(d, sport) {
    const t = d.targets;
    if (!t || !t.rows) return '';
    const st = STATE[sport];
    const poss = [...new Set(t.rows.map(r => r.kind ? (r.kind === 'H' ? 'Hitters' : 'Pitchers') : r.pos.split('/')[0]))];
    const rows = t.rows.filter(r => st.tpos === 'ALL' || (r.kind ? (r.kind === 'H' ? 'Hitters' : 'Pitchers') : r.pos.split('/')[0]) === st.tpos);
    const cols = d.cols.filter(c => c.k !== 'profit');
    return `<div class="card"><div class="sec-h"><h2>🎯 ${e(t.label)}</h2>
      <label class="meta">Show <select id="lw-tpos">${['ALL', ...poss].map(p => `<option ${p === st.tpos ? 'selected' : ''}>${e(p)}</option>`).join('')}</select></label></div>
      <p class="sub">This season's players scored against the traits past league winners shared. Every player here passes the cost check and at least three traits in all, so the list favors players you can still get at a discount.</p>
      <div class="lw-legend">${t.checks.map(c => `<div><b>${e(c.label)}</b>${e(c.desc)}</div>`).join('')}</div>
      ${rows.length ? `<div class="tw"><table><thead><tr><th class="l">Player</th>${head(cols)}<th title="Rough guess at next year's draft cost: blends this year's cost with his finish">Next-yr cost</th>${t.checks.map(c => `<th title="${e(c.desc)}">${e(c.label.split(' (')[0])}</th>`).join('')}</tr></thead><tbody>
        ${rows.map(r => `<tr>${nameCell(r)}${cols.map(c => cell(c, r)).join('')}<td>${fmt(r.nxt, '0')}</td>${t.checks.map(c => `<td style="text-align:center"><span class="lw-ck ${r.checks[c.k] ? 'y' : 'n'}">${r.checks[c.k] ? '✓' : '·'}</span></td>`).join('')}</tr>`).join('')}
      </tbody></table></div>` : '<div class="empty">No players match enough traits yet.</div>'}</div>`;
  }

  function seasons(d, sport) {
    const st = STATE[sport];
    const s = d.seasons.find(x => x.key === st.season) || d.seasons[0];
    const list = st.view === 'busts' ? s.busts : s.winners;
    return `<div class="card"><div class="sec-h"><h2>📜 League winners by season</h2>
      <div class="bar" style="margin:0"><label>Season<select id="lw-season">${d.seasons.map(x => `<option value="${e(x.key)}" ${x.key === s.key ? 'selected' : ''}>${e(x.label)}</option>`).join('')}</select></label>
      <label>Show<select id="lw-view"><option value="win" ${st.view !== 'busts' ? 'selected' : ''}>League winners</option><option value="busts" ${st.view === 'busts' ? 'selected' : ''}>Biggest busts (early picks)</option></select></label></div></div>
      <p class="sub">${s.done ? '' : `Season in progress${s.weeks ? ` (${s.weeks} weeks)` : ''}: these are the players beating their draft cost so far. `}Draft cost: ${e(s.cost)}. "Finish" is where his value ranked among all players.</p>
      <div class="tw"><table><thead><tr><th class="l">Player</th>${head(d.cols)}</tr></thead><tbody>
      ${list.map(r => `<tr>${nameCell(r)}${d.cols.map(c => cell(c, r)).join('')}</tr>`).join('') || '<tr><td colspan="9" class="l">No data</td></tr>'}
      </tbody></table></div></div>`;
  }

  async function renderWinners(el, sport) {
    addCss();
    STATE[sport] = STATE[sport] || { tpos: 'ALL', view: 'win', season: null };
    if (!CACHE[sport]) {
      el.innerHTML = '<div class="card"><div class="empty">Loading league-winner research…</div></div>';
      try {
        CACHE[sport] = await fetch(`data/winners_${sport}.json?v=${Date.now()}`, { cache: 'no-store' }).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
      } catch (err) {
        el.innerHTML = '<div class="card"><div class="empty">The league-winner research hasn\'t been built yet. It refreshes nightly; check back tomorrow.</div></div>';
        return;
      }
    }
    const d = CACHE[sport], st = STATE[sport];
    if (!st.season) st.season = d.seasons[0] && d.seasons[0].key;
    const up = new Date(d.updated);
    el.innerHTML = anatomy(d) + targets(d, sport) + seasons(d, sport) +
      `<p class="sub">Sources: ${e(d.sources)}. Updated ${up.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}. Same philosophy as the "Anatomy of a League Winner" research; the picks here come from this tool's own data.</p>`;
    const on = (id, fn) => { const x = el.querySelector('#' + id); if (x) x.onchange = ev => { fn(ev.target.value); renderWinners(el, sport); }; };
    on('lw-tpos', v => st.tpos = v);
    on('lw-season', v => st.season = v);
    on('lw-view', v => st.view = v === 'busts' ? 'busts' : 'win');
  }
  window.renderWinners = renderWinners;
})();
