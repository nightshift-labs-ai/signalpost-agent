#!/usr/bin/env python3
"""Build a single static page from envelope JSONL: find a company, compare two, check each fact's source."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PAGE = """<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Signalpost: Norwegian company profiles</title>
<style>
:root{--ink:#17201d;--mute:#5d6b66;--line:#d8dfdc;--ok:#116b4f;--warn:#9a5b00}
*{box-sizing:border-box}body{font:16px/1.5 system-ui,sans-serif;color:var(--ink);margin:0;background:#f6f8f7}
header{background:#fff;border-bottom:1px solid var(--line);padding:16px}header h1{margin:0;font-size:20px}header p{margin:4px 0 0;color:var(--mute);font-size:14px}
main{max-width:1180px;margin:0 auto;padding:16px;display:grid;gap:16px}
.bar{display:grid;gap:8px;grid-template-columns:1fr 1fr}input{width:100%;padding:10px;font:inherit;border:1px solid var(--line);border-radius:6px}
.cols{display:grid;gap:16px;grid-template-columns:1fr 1fr}@media(max-width:760px){.cols,.bar{grid-template-columns:1fr}}
.card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:16px;min-width:0}.card h2{margin:0 0 4px;font-size:18px}.sub{color:var(--mute);font-size:13px}
.sum{margin:10px 0}h3{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:16px 0 4px}
table{width:100%;border-collapse:collapse;font-size:14px}td{border-top:1px solid var(--line);padding:5px 6px 5px 0;vertical-align:top;overflow-wrap:anywhere}td:first-child{color:var(--mute);width:36%}
a{color:var(--ok)}.st{font-size:12px;color:var(--warn)}ul.list{list-style:none;margin:6px 0 0;padding:0;max-height:210px;overflow:auto;background:#fff;border:1px solid var(--line);border-radius:6px}ul.list li{padding:7px 10px;cursor:pointer;border-top:1px solid var(--line)}ul.list li:first-child{border:0}ul.list li:hover{background:#eef3f1}
</style>
<header><h1>Signalpost: Norwegian company profiles</h1><p>Every fact links to its source. Missing data is shown as missing, never as zero. Built by an AI agent (Nightshift Labs).</p></header>
<main><div class=bar><div><input id=qa placeholder="Find a company by name or organisation number" aria-label="Find company"><ul class=list id=la></ul></div><div><input id=qb placeholder="Compare with a second company" aria-label="Compare with"><ul class=list id=lb></ul></div></div>
<div class=cols><section class=card id=a><p class=sub>Choose a company above.</p></section><section class=card id=b><p class=sub>Choose a second company to compare.</p></section></div></main>
<script>const D=__DATA__;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const safe=u=>/^https?:\\/\\//i.test(u||'')?esc(u):'#';
function card(c){if(!c)return'';const by={};for(const x of c.claims){const g=x.field.split('.')[0];(by[g]=by[g]||[]).push(x)}
let h=`<h2>${esc(c.name)}</h2><div class=sub>Org. no. ${esc(c.org)}</div><p class=sum>${esc(c.summary)}</p>`;
for(const g of Object.keys(by)){h+=`<h3>${esc(g.replace(/_/g,' '))}</h3><table>`;for(const x of by[g]){const v=x.availability==='available'?`${esc(x.value)} ${(x.sources||[]).map(s=>`<a href="${safe(s)}" rel=noopener target=_blank>source</a>`).join(' ')}`:`<span class=st>${esc(x.availability.replace('_',' '))}</span>`;h+=`<tr><td>${esc((x.field.split('.').slice(1).join(' ')||g).replace(/_/g,' '))}</td><td>${v}</td></tr>`}h+='</table>'}return h}
function bind(q,l,t){const I=document.getElementById(q),L=document.getElementById(l),T=document.getElementById(t);const show=()=>{const s=I.value.trim().toLowerCase();L.innerHTML='';D.filter(c=>!s||c.name.toLowerCase().includes(s)||c.org.includes(s)).slice(0,20).forEach(c=>{const li=document.createElement('li');li.textContent=c.name+' ('+c.org+')';li.onclick=()=>{T.innerHTML=card(c);I.value=c.name;L.innerHTML=''};L.appendChild(li)})};I.addEventListener('input',show);I.addEventListener('focus',show)}
bind('qa','la','a');bind('qb','lb','b');
</script></html>"""


def row(envelope: dict) -> dict:
    evidence = {item["id"]: item.get("source_url") for item in envelope.get("evidence") or []}
    summary = envelope.get("summary") or {}
    return {
        "org": envelope["organisation_number"],
        "name": (envelope.get("profile") or {}).get("name") or envelope["organisation_number"],
        "summary": summary.get("text") if isinstance(summary, dict) else summary,
        "claims": [{
            "field": claim["field"],
            "availability": claim["availability"],
            "value": _display(claim.get("value")),
            "sources": sorted({evidence[item] for item in claim.get("evidence_ids") or [] if evidence.get(item)}),
        } for claim in envelope.get("claims") or []],
    }


def _display(value: object) -> str:
    if isinstance(value, dict):
        if "name" in value and "role" in value:
            return f"{value['name']} ({value.get('role')})"
        if "title" in value:
            return " ".join(str(value[key]) for key in ("title", "date") if value.get(key))
        if "name" in value:
            return str(value["name"])
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = [row(json.loads(line)) for line in Path(args.envelopes).read_text(encoding="utf-8").splitlines() if line.strip()]
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(PAGE.replace("__DATA__", data), encoding="utf-8")
    print(len(rows), "profiles ->", args.output)


if __name__ == "__main__":
    main()
