#!/usr/bin/env python3
"""Build one static page from envelope JSONL: find a company, compare two, and check the evidence behind any fact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Signalpost: Norwegian company profiles</title>
<style>
:root{--ink:#17201d;--mute:#56645f;--line:#d8dfdc;--ok:#0d6146;--warn:#8a5100;--bg:#f6f8f7;--tint:#eef3f1}
*{box-sizing:border-box}
body{font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);margin:0;background:var(--bg)}
header{background:#fff;border-bottom:1px solid var(--line);padding:14px 16px}
header h1{margin:0;font-size:20px}
header p{margin:2px 0 0;color:var(--mute);font-size:14px}
main{max-width:1180px;margin:0 auto;padding:16px;display:grid;gap:16px}
a{color:var(--ok)}
.bar{display:grid;gap:10px;grid-template-columns:1fr 1fr}
.pick{position:relative;min-width:0}
.pick label{display:block;font-size:13px;font-weight:600;margin-bottom:3px}
input[type=search]{width:100%;min-height:44px;padding:9px 11px;font:inherit;border:1px solid #9aa8a3;border-radius:6px;background:#fff}
.list{list-style:none;margin:4px 0 0;padding:0;max-height:280px;overflow:auto;background:#fff;border:1px solid var(--line);border-radius:6px}
.list:empty{display:none}
.list button{display:block;width:100%;min-height:44px;text-align:left;font:inherit;background:none;border:0;border-top:1px solid var(--line);padding:8px 11px;cursor:pointer;color:inherit}
.list li:first-child button{border-top:0}
.list button:hover,.list button:focus,.list button.on{background:var(--tint);outline:none}
.tag{display:inline-block;font-size:11px;line-height:16px;padding:0 5px;border-radius:3px;background:var(--tint);color:var(--ok);margin-left:4px}
.tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:14px}
.tools button{min-height:44px;font:inherit;font-size:14px;padding:6px 12px;border:1px solid #9aa8a3;border-radius:6px;background:#fff;cursor:pointer}
.tools span{color:var(--mute)}
.card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:16px;min-width:0}
.card h2{margin:0;font-size:19px;overflow-wrap:anywhere}
.mute{color:var(--mute);font-size:14px}
.sum{margin:10px 0 0}
.cols{display:grid;gap:16px;grid-template-columns:1fr 1fr}
h3{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:18px 0 2px}
.fact{display:grid;grid-template-columns:minmax(0,34%) minmax(0,1fr);gap:0 10px;padding:6px 0;border-top:1px solid var(--line);font-size:15px}
.fact>div{min-width:0;overflow-wrap:anywhere}
.fact>div:first-child{color:var(--mute)}
.state{color:var(--warn);font-size:14px}
details.src{margin-top:2px;font-size:13px}
details.src summary{cursor:pointer;color:var(--ok);padding:3px 0}
.ev{border-left:3px solid var(--line);padding:2px 0 2px 8px;margin:4px 0;font-size:13px;overflow-wrap:anywhere}
.ev dl{margin:2px 0 0;display:grid;grid-template-columns:max-content minmax(0,1fr);gap:0 8px}
.ev dt{color:var(--mute)}.ev dd{margin:0;min-width:0}
.q{font-style:italic}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:6px 8px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
th{font-weight:600;background:var(--bg)}
td.n{text-align:right;white-space:nowrap}
td:first-child,th:first-child{color:var(--mute);font-weight:400}
.better{font-weight:600}
footer{max-width:1180px;margin:0 auto;padding:0 16px 24px;font-size:13px;color:var(--mute)}
.ev{white-space:normal;text-align:left}
@media(max-width:760px),(pointer:coarse){details.src summary{padding:12px 0}.ev a{display:inline-block;padding:10px 0}}
@media(max-width:760px){.cols,.bar{grid-template-columns:minmax(0,1fr)}.fact{grid-template-columns:minmax(0,1fr)}.fact>div:first-child{font-size:13px}
.ev dl{display:block}.ev dd{margin:0 0 4px}
#cmp table,#cmp tbody,#cmp thead{display:block}
#cmp tr{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);border-top:1px solid var(--line)}
#cmp thead th:first-child{display:none}
#cmp thead th{overflow-wrap:anywhere;border:0}
#cmp tbody td{border:0;min-width:0;white-space:normal;text-align:left}
#cmp tbody td:first-child{grid-column:1/-1;padding-bottom:0;font-size:13px}
#cmp .scroll{overflow:visible}}
</style>
</head>
<body>
<header><h1>Signalpost: Norwegian company profiles</h1>
<p id="meta"></p></header>
<main>
<div class="bar">
 <div class="pick"><label for="qa">Find a company</label><input id="qa" type="search" autocomplete="off" placeholder="Name or organisation number"><ul class="list" id="la"></ul></div>
 <div class="pick"><label for="qb">Compare with</label><input id="qb" type="search" autocomplete="off" placeholder="Choose a second company"><ul class="list" id="lb"></ul></div>
</div>
<div class="tools"><button id="copy" type="button">Copy link to this view</button><button id="clear" type="button">Clear comparison</button><span id="note" role="status"></span></div>
<section class="card" id="cmp" hidden></section>
<div class="cols"><section class="card" id="a" aria-live="polite"></section><section class="card" id="b" aria-live="polite"></section></div>
</main>
<footer id="foot"></footer>
<script>
const D=__DATA__,M=__META__;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safe=u=>/^https?:\/\//i.test(u||'')?esc(u):'';
const nf=new Intl.NumberFormat('nb-NO');
const byOrg=new Map(D.map(c=>[c.org,c]));
const fold=s=>String(s??'').toLowerCase().replace(/æ/g,'ae').replace(/ø/g,'o').replace(/å/g,'a').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]+/g,' ').trim();
const loose=s=>fold(s).replace(/aa/g,'a').replace(/oe/g,'o').replace(/ae/g,'a');
D.forEach(c=>{c._s=fold(c.name)+' | '+loose(c.name)});
const ROLE={'Daglig leder':'Managing director','Styrets leder':'Chair of the board','Styremedlem':'Board member','Nestleder':'Deputy chair','Varamedlem':'Deputy board member','Observatør':'Observer','Regnskapsfører':'Accountant','Revisor':'Auditor','Forretningsfører':'Business manager','Kontaktperson':'Contact person','Innehaver':'Owner','Bestyrende reder':'Managing owner','Deltaker med proratarisk ansvar':'Partner (pro rata liability)','Deltaker med delt ansvar':'Partner (shared liability)','Komplementar':'General partner','Kommandittist':'Limited partner'};
const roleLabel=r=>ROLE[r]?`${ROLE[r]} (${r})`:(r||'Role');
const REL={'Konsern datter':'Group subsidiary','Konsern mor':'Group parent','Konsern søster':'Group sister company'};
const FORM={AS:'Limited company',ASA:'Public limited company',ENK:'Sole proprietorship',ANS:'General partnership',DA:'Partnership, shared liability',BRL:'Housing co-operative',SA:'Co-operative',STI:'Foundation',ESEK:'Co-ownership',NUF:'Branch of a foreign company',KS:'Limited partnership',BA:'Company with limited liability',FLI:'Association',HF:'Health trust',SF:'State enterprise',KF:'Municipal enterprise',IKS:'Inter-municipal company'};
const METHOD={brreg_api_json:'Official register API',brreg_bulk_csv:'Official bulk file',rule_from_registry_row:'Rule applied to a register row',html_fetch:'Page fetched',html_text_match:'Text found on the page'};
const FIN=[['revenue','Revenue'],['operating_result','Operating result'],['profit_before_tax','Profit before tax'],['annual_result','Annual result'],['assets','Total assets'],['equity','Equity'],['debt','Debt']];
const STATE={not_available:'Not found',ambiguous:'Held back: identity not certain',blocked:'Blocked by the source',not_applicable:'Not applicable',failed:'Check failed'};
const CLASS={official_registry_bulk:'Official register, bulk file',official_registry_live:'Official register',official_annual_accounts:'Official annual accounts',official_roles:'Official roles register',official_group_structure:'Official group register',official_subunits:'Official workplace register',official_rule_interpretation:'Rule applied to official data',company_owned:'Company website',company_owned_discovered:'Company website',registry_linked_company_website:'Website filed in the register'};
const when=s=>s?String(s).replace('T',' ').replace(/\.\d+Z?$/,'').replace(/Z$/,'')+' UTC':'';
const host=u=>{try{return new URL(u).hostname.replace(/^www\./,'')}catch(e){return ''}};
const link=(u,t)=>safe(u)?`<a href="${safe(u)}" rel="noopener noreferrer" target="_blank">${esc(t||host(u)||u)}</a>`:esc(t||u);
function sources(c,ids){const items=(ids||[]).map(i=>c.ev[i]).filter(Boolean);if(!items.length)return'';
 return `<details class="src"><summary>Source${items.length>1?'s ('+items.length+')':''}</summary>`+items.map(e=>`<div class="ev">${e.u?link(e.u):''}<dl><dt>Type</dt><dd>${esc(CLASS[e.c]||e.c||'')}</dd><dt>Retrieved</dt><dd>${esc(when(e.at))}</dd>${e.q?`<dt>Excerpt</dt><dd class="q">${esc(e.q)}</dd>`:''}<dt>How it was read</dt><dd>${esc(METHOD[e.m]||e.m||'')}</dd>${e.h?`<dt>Content hash</dt><dd title="${esc(e.h)}">${esc(e.h.slice(0,16))}…</dd>`:''}</dl></div>`).join('')+'</details>'}
const value=x=>x.a!=='available'?`<span class="state">${esc(STATE[x.a]||x.a)}</span>`:(x.f==='legal_form'&&FORM[x.v]?`${esc(FORM[x.v])} (${esc(x.v)})`:(x.u?link(x.u,x.v):(/^https?:\/\//i.test(x.v)?link(x.v):esc(x.v))));
const fact=(c,label,x)=>`<div class="fact"><div>${esc(label)}</div><div>${value(x)}${x.note?` <span class="mute">${esc(x.note)}</span>`:''}${sources(c,x.e)}</div></div>`;
const all=(c,f)=>c.claims.filter(x=>x.f===f);
const first=(c,f)=>all(c,f)[0];
const avail=(c,f)=>all(c,f).filter(x=>x.a==='available');
function periods(c){const m=new Map();for(const x of c.claims){if(!x.f.startsWith('financials.')||x.a!=='available'||!x.end||!FIN.some(f=>'financials.'+f[0]===x.f))continue;const k=x.end+'|'+(x.t||'');if(!m.has(k))m.set(k,{end:x.end,start:x.start||'',t:x.t||'',cur:x.cur||'',v:{}});m.get(k).v[x.f.split('.')[1]]=x}return[...m.values()].sort((p,q)=>q.end.localeCompare(p.end)||(p.t==='SELSKAP'?-1:1))}
const months=p=>{if(!p.start||!p.end)return 12;const[a,b,c,d]=[+p.start.slice(0,4),+p.start.slice(5,7),+p.end.slice(0,4),+p.end.slice(5,7)];return(c-a)*12+d-b+1};
const pLabel=p=>{const m=months(p);return(p.end||'Unknown period').slice(0,4)+(m!==12?` (${m} months)`:'')+(p.t&&p.t!=='SELSKAP'?' (group)':'')};
function section(title,body){return body?`<h3>${esc(title)}</h3>${body}`:''}
function finTable(c){const ps=periods(c).slice(0,4);if(!ps.length)return'<p class="state">No filed annual accounts were found for this company in this run.</p>';
 let h=`<div class="scroll"><table><thead><tr><th>${esc(ps[0].cur||'Amount')}</th>${ps.map(p=>`<th title="Period ending ${esc(p.end)}">${esc(pLabel(p))}</th>`).join('')}</tr></thead><tbody>`;
 for(const[k,l]of FIN){h+=`<tr><td>${esc(l)}</td>${ps.map(p=>{const x=p.v[k];return x&&typeof x.n==='number'?`<td class="n">${nf.format(x.n)}</td>`:'<td class="n"><span class="state">Not filed</span></td>'}).join('')}</tr>`}
 const x=ps[0].v.revenue||ps[0].v.annual_result||Object.values(ps[0].v)[0];
 return h+`</tbody></table></div>${sources(c,x?x.e:[])}`}
function card(c,empty){if(!c)return`<p class="mute">${esc(empty)}</p>`;
 const avAct=avail(c,'public_activity.dated_item').slice().sort((a,b)=>String(b.at||'').localeCompare(String(a.at||'')));
 const missing=c.claims.filter(x=>x.a!=='available'&&!x.f.startsWith('company_profile.'));
 const social=c.claims.filter(x=>x.f.startsWith('company_profile.')&&x.a==='available');
 let h=`<h2>${esc(c.name)}</h2><div class="mute">Org. no. ${esc(c.org)} · checked ${esc(when(c.done))}</div><p class="sum">${esc(c.summary)}</p>${sources(c,c.sum_e)}`;
 h+=section('Company',['legal_form','industry','business_address','registered_employees','official_website','public_brand','self_description'].flatMap(f=>{const x=first(c,f);return x&&x.a==='available'?[fact(c,{legal_form:'Legal form',industry:'Industry',business_address:'Registered address',registered_employees:'Registered employees',official_website:'Official website',public_brand:'Public brand',self_description:'Own description'}[f],x)]:[]}).join(''));
 h+=section('Filed annual accounts',finTable(c));
 h+=section('Leadership',avail(c,'leadership.role').map(x=>fact(c,roleLabel(x.r),x)).join(''));
 h+=section('Registered workplaces',avail(c,'registered_workplace').map(x=>fact(c,'Workplace',x)).join(''));
 h+=section('Group',avail(c,'group.link').map(x=>fact(c,REL[x.rel]||x.rel||'Linked company',x)).join(''));
 h+=section('Linked profiles',social.map(x=>fact(c,x.f.split('.')[1],x)).join(''));
 h+=section('Hiring',[...avail(c,'hiring.job_posting'),...avail(c,'hiring.job_board_link'),...avail(c,'hiring.careers_page')].map(x=>fact(c,{'hiring.job_posting':'Job posting','hiring.job_board_link':'Recruitment board','hiring.careers_page':'Careers page'}[x.f],x)).join(''));
 h+=section('Dated public activity',avAct.slice(0,12).map(x=>fact(c,x.at||'Undated',x)).join('')+(avAct.length>12?`<details class="src"><summary>Show ${avAct.length-12} older items</summary>${avAct.slice(12).map(x=>fact(c,x.at||'Undated',x)).join('')}</details>`:''));
 h+=section('Changes since the previous run',c.changes.length?c.changes.map(t=>`<div class="fact"><div>Change</div><div>${esc(t)}</div></div>`).join(''):`<p class="mute">${esc(c.first?'First run for this company, so there is no earlier snapshot to compare.':'No material change since the previous run.')}</p>`);
 if(missing.length)h+=section('Not found or not checked',`<details class="src"><summary>Show ${missing.length} open items</summary>${missing.map(x=>fact(c,x.f.replace(/[._]/g,' '),x)).join('')}</details>`);
 return h}
function metric(c,key){const p=periods(c)[0];return p&&p.v[key]||null}
function compare(a,b){if(!a||!b||a===b){$('cmp').hidden=true;$('cmp').innerHTML='';return}
 const end=p=>p?pLabel(p):'';
 const rows=[['Legal form',c=>first(c,'legal_form')],['Industry',c=>first(c,'industry')],['Registered employees',c=>first(c,'registered_employees')],['Official website',c=>first(c,'official_website')]];
 const cell=(c,x)=>x?`<td>${value(x)}${sources(c,x.e)}</td>`:'<td><span class="state">Not found</span></td>';
 let h=`<h2>Side by side</h2><div class="scroll"><table><thead><tr><th></th><th>${esc(a.name)}</th><th>${esc(b.name)}</th></tr></thead><tbody>`;
 for(const[l,g]of rows)h+=`<tr><td>${esc(l)}</td>${cell(a,g(a))}${cell(b,g(b))}</tr>`;
 const pa=periods(a)[0],pb=periods(b)[0];
 h+=`<tr><td>Latest accounts</td><td>${esc(end(pa))||'<span class="state">Not found</span>'}</td><td>${esc(end(pb))||'<span class="state">Not found</span>'}</td></tr>`;
 for(const[k,l]of FIN){const xa=metric(a,k),xb=metric(b,k);const f=(c,x)=>x&&typeof x.n==='number'?`<td class="n">${nf.format(x.n)} ${esc(x.cur||'')}${sources(c,x.e)}</td>`:'<td class="n"><span class="state">Not filed</span></td>';h+=`<tr><td>${esc(l)}</td>${f(a,xa)}${f(b,xb)}</tr>`}
 const counts=[['Leaders on record','leadership.role'],['Registered workplaces','registered_workplace'],['Job postings','hiring.job_posting'],['Dated activity items','public_activity.dated_item']];
 for(const[l,f]of counts){const sh=c=>{const n=avail(c,f).length;return n?String(n):value(first(c,f)||{a:'not_available'})};h+=`<tr><td>${esc(l)}</td><td>${sh(a)}</td><td>${sh(b)}</td></tr>`}
 $('cmp').innerHTML=h+'</tbody></table></div>';$('cmp').hidden=false}
const tags=c=>(avail(c,'official_website').length?'<span class="tag">website</span>':'')+(avail(c,'hiring.job_posting').length?'<span class="tag">jobs</span>':'')+(avail(c,'public_activity.dated_item').length?'<span class="tag">activity</span>':'');
const S={a:null,b:null};
function render(update){const a=byOrg.get(S.a),b=byOrg.get(S.b);if(update)$('note').textContent='';$('a').innerHTML=card(a,'Choose a company above.');$('b').innerHTML=card(b,'Choose a second company to compare it side by side.');compare(a,b);$('qa').value=a?a.name:'';$('qb').value=b?b.name:'';
 if(update){const h=[];if(S.a)h.push('a='+S.a);if(S.b)h.push('b='+S.b);history.replaceState(null,'','#'+h.join('&'))}}
function bind(key,inp,list){const I=$(inp),L=$(list),other=key==='a'?'b':'a';let idx=-1;
 const close=()=>{L.innerHTML='';idx=-1};
 const current=()=>{const c=byOrg.get(S[key]);return c?c.name:''};
 const show=()=>{const raw=I.value.trim();const filled=raw===current();const q=filled?'':fold(raw),l=filled?'':loose(raw),d=filled?'':raw.replace(/\D/g,'');L.innerHTML='';idx=-1;
  const hits=D.filter(c=>c.org!==S[other]&&(!q||c._s.includes(q)||c._s.includes(l)||(d.length>=3&&c.org.includes(d))));
  const note=t=>{const li=document.createElement('li');li.className='mute';li.style.padding='9px 11px';li.textContent=t;L.appendChild(li)};
  if(!hits.length){note(`No company matches "${raw}". Try fewer letters or an organisation number.`);return}
  hits.slice(0,25).forEach(c=>{const li=document.createElement('li');const b=document.createElement('button');b.type='button';b.tabIndex=-1;b.innerHTML=esc(c.name)+' <span class="mute">'+esc(c.org)+'</span>'+tags(c);b.onclick=()=>{S[key]=c.org;close();render(true);I.blur()};li.appendChild(b);L.appendChild(li)});
  if(hits.length>25)note(`Showing 25 of ${hits.length}. Keep typing to narrow the list.`)};
 I.addEventListener('input',()=>{if(key==='b'&&S.b&&!I.value.trim()){S.b=null;render(true)}show()});I.addEventListener('focus',()=>{I.select();show()});I.addEventListener('click',()=>{if(!L.children.length)show()});
 I.addEventListener('keydown',e=>{const bs=[...L.querySelectorAll('button')];if(e.key==='ArrowDown'&&bs.length){e.preventDefault();idx=Math.min(idx+1,bs.length-1)}else if(e.key==='ArrowUp'&&bs.length){e.preventDefault();idx=Math.max(idx-1,0)}else if(e.key==='Enter'&&bs.length){e.preventDefault();bs[Math.max(idx,0)].click();return}else if(e.key==='Escape'){e.preventDefault();close();I.value=current();return}else return;bs.forEach((b,i)=>b.classList.toggle('on',i===idx));if(bs[idx])bs[idx].scrollIntoView({block:'nearest'})});
 document.addEventListener('click',e=>{if(e.target!==I&&!L.contains(e.target)&&L.children.length){close();I.value=current()}})}
function fromHash(){const p=new URLSearchParams(location.hash.slice(1));S.a=byOrg.has(p.get('a'))?p.get('a'):null;S.b=byOrg.has(p.get('b'))?p.get('b'):null;if(!S.a&&!S.b&&D.length){const rich=D.slice().sort((x,y)=>y.score-x.score);S.a=rich[0].org}render(false)}
bind('a','qa','la');bind('b','qb','lb');
$('copy').onclick=async()=>{const u=location.href;try{await navigator.clipboard.writeText(u);$('note').textContent='Link copied.'}catch(e){$('note').textContent=u}};
$('clear').onclick=()=>{S.b=null;render(true);$('note').textContent=''};
window.addEventListener('hashchange',fromHash);
$('meta').textContent=`${D.length} companies · updated ${M.updated} · each listed fact opens its source, and anything not found is shown as not found, never as zero.`;
$('foot').innerHTML=`Built by an AI agent (Claude, operating Nightshift Labs for one human owner). Company register and annual-account data: Brønnøysundregistrene open data under <a href="https://data.norge.no/nlod/en/2.0" rel="noopener noreferrer" target="_blank">NLOD 2.0</a>. Website facts are short excerpts from each company's own pages, linked to the source, and may mention people the company names there. Leadership names come from the public register. Corrections or removal: nightshiftlabs-api@agentmail.to`;
fromHash();
</script>
</body>
</html>
"""


def _names(name: Any) -> str:
    """The roles register gives a person's name as text and an organisation's name as a list of name lines."""
    return " ".join(str(item) for item in name if item) if isinstance(name, list) else str(name or "")


def _display(value: Any) -> str:
    if isinstance(value, dict):
        if "name" in value and "role" in value:
            return _names(value["name"])
        if "title" in value:
            return str(value["title"])
        if "board_host" in value:
            return str(value.get("anchor_text") or value["board_host"])
        if "adresse" in value or "poststed" in value:
            street = ", ".join(str(item) for item in value.get("adresse") or [] if item)
            place = " ".join(str(item) for item in (value.get("postnummer"), value.get("poststed")) if item)
            return ", ".join(part for part in (street, place) if part)
        if "beskrivelse" in value:
            return " ".join(str(item) for item in (value.get("beskrivelse"), f"({value['kode']})" if value.get("kode") else None) if item)
        if "name" in value:
            parts = [_names(value["name"]) + (f" ({value['organisation_number']})" if value.get("relationship") and value.get("organisation_number") else "")]
            address = value.get("address")
            if isinstance(address, dict):
                parts.append(_display(address))
            return ": ".join(part for part in parts if part)
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _claim(claim: dict[str, Any]) -> dict[str, Any]:
    value = claim.get("value")
    row: dict[str, Any] = {"f": claim.get("field", ""), "a": claim.get("availability", ""), "v": _display(value), "e": list(claim.get("evidence_ids") or [])}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        row["n"] = value
    if claim.get("currency"):
        row["cur"] = claim["currency"]
    period = claim.get("reporting_period") or {}
    if isinstance(period, dict) and period.get("tilDato"):
        row["end"] = period["tilDato"]
        if period.get("fraDato"):
            row["start"] = period["fraDato"]
    if claim.get("account_type"):
        row["t"] = claim["account_type"]
    if claim.get("effective_at"):
        row["at"] = claim["effective_at"]
    if claim.get("source_note"):
        row["note"] = claim["source_note"]
    if isinstance(value, dict):
        if isinstance(value.get("url"), str):
            row["u"] = value["url"]
        if value.get("date") and not row.get("at"):
            row["at"] = value["date"]
        if "role" in value:
            row["r"] = value.get("role")
        if "relationship" in value:
            row["rel"] = value.get("relationship")
            if value.get("since"):
                row["note"] = f"since {value['since']}"
    return row


def _change(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    kind = {"changed": "Changed", "added": "New", "removed": "No longer found"}.get(str(item.get("type")), "Changed")
    field = str(item.get("field") or "").replace("_", " ").replace(".", " ")
    before, after = _display(item.get("previous")), _display(item.get("current"))
    detail = f"{before} \u2192 {after}" if before and after else (after or before)
    return f"{kind}: {field}" + (f": {detail}" if detail else "")


def _evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {"u": item.get("source_url"), "c": item.get("source_class"), "at": item.get("retrieved_at"), "h": item.get("content_sha256"), "q": item.get("claim_span"), "m": item.get("extraction_method")}


def row(envelope: dict[str, Any]) -> dict[str, Any]:
    summary = envelope.get("summary") or {}
    claims = [_claim(claim) for claim in envelope.get("claims") or []]
    summary_evidence = sorted({evidence_id for sentence in (summary.get("sentences") or [] if isinstance(summary, dict) else []) for evidence_id in sentence.get("evidence_ids") or []})
    cited = {evidence_id for claim in claims for evidence_id in claim["e"]} | set(summary_evidence)
    richness = sum(1 for claim in claims if claim["a"] == "available" and not claim["f"].startswith(("financials.", "leadership.", "registered_workplace")))
    richness += 25 * any(claim["f"] == "official_website" and claim["a"] == "available" for claim in claims)
    refresh = envelope.get("refresh") or {}
    return {
        "org": envelope["organisation_number"],
        "name": (envelope.get("profile") or {}).get("name") or envelope["organisation_number"],
        "summary": summary.get("text") if isinstance(summary, dict) else summary,
        "sum_e": summary_evidence,
        "done": envelope.get("completed_at"),
        "first": bool(refresh.get("first_observation")) if isinstance(refresh, dict) else False,
        "changes": [_change(item) for item in envelope.get("changes") or []],
        "claims": claims,
        "ev": {item["id"]: _evidence(item) for item in envelope.get("evidence") or [] if item.get("id") in cited},
        "score": richness,
    }


def render(envelopes: Iterable[dict[str, Any]]) -> tuple[str, int]:
    rows = [row(envelope) for envelope in envelopes]
    rows.sort(key=lambda item: str(item["name"]).casefold())
    finished = sorted(str(envelope.get("completed_at"))[:10] for envelope in envelopes if envelope.get("completed_at")) if isinstance(envelopes, list) else []
    meta = {"updated": finished[-1] if finished else "unknown"}
    # "<" stays inside JSON strings only; escaping it keeps a value from closing the script element.
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    # META goes in first, so a value that happens to contain the placeholder text cannot be mistaken for it.
    return PAGE.replace("__META__", json.dumps(meta, ensure_ascii=False).replace("<", "\\u003c"), 1).replace("__DATA__", data, 1), len(rows)


def build(envelopes_path: str | Path, output_path: str | Path) -> int:
    lines = Path(envelopes_path).read_text(encoding="utf-8").splitlines()
    page, count = render([json.loads(line) for line in lines if line.strip()])
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(build(args.envelopes, args.output), "profiles ->", args.output)


if __name__ == "__main__":
    main()
