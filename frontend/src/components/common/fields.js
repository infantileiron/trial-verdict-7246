import {esc} from '../../utils/html.js';
export const field=(label,name,type='text',extra='')=>`<label>${label}<input name=${name} type=${type} ${extra}></label>`;
export const select=(label,name,options,selected='')=>`<label>${label}<select name=${name}>${options.map(([v,t])=>`<option value="${esc(v)}"${String(v)===String(selected)?' selected':''}>${esc(t)}</option>`).join('')}</select></label>`;
export const area=(label,name,extra='')=>`<label>${label}<textarea name=${name} ${extra}></textarea></label>`;
