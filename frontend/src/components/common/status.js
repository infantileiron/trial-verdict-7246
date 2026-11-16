import {esc} from '../../utils/html.js';export const badge=s=>`<span class=tag>${esc(s)}</span>`;
export const kv=(pairs)=>`<dl class="kv">${pairs.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`;
