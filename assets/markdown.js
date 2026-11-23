// Offline safe Markdown subset. No raw HTML execution.
(()=>{
const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
function externalURL(value){try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:null;}catch(e){return null;}}
function inline(parent,text){
  const pattern=/(`+)([\s\S]*?)\1|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^\s)]+)\)/g;let end=0,m;
  while((m=pattern.exec(text))){if(m.index>end)parent.append(node('span',text.slice(end,m.index)));
    if(m[1])parent.append(node('code',m[2]));else if(m[3])parent.append(node('strong',m[3]));else{const url=externalURL(m[5]);if(url){const a=node('a',m[4]);a.href=url;a.target='_blank';a.rel='noopener noreferrer';parent.append(a);}else parent.append(node('span',m[0]));}end=pattern.lastIndex;
  }if(end<text.length)parent.append(node('span',text.slice(end)));
}
function renderMarkdown(parent,text){
  parent.replaceChildren();const lines=text.replace(/\r\n?/g,'\n').split('\n'),stack=[parent];let paragraph=[];
  const current=()=>stack[stack.length-1];
  const flush=()=>{if(paragraph.length){const p=node('p');inline(p,paragraph.join('\n'));current().append(p);paragraph=[];}};
  const cells=line=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(x=>x.trim());
  for(let i=0;i<lines.length;i++){
    const line=lines[i],fence=line.match(/^\s*(`{3,}|~{3,})(.*)$/);
    if(fence){flush();const code=[];const close=new RegExp('^\\s*'+fence[1][0]+'{'+fence[1].length+',}\\s*$');while(i+1<lines.length&&!close.test(lines[i+1]))code.push(lines[++i]);if(i+1<lines.length)i++;const pre=node('pre');pre.append(node('code',code.join('\n')));current().append(pre);continue;}
    if(/^\s*<details(?: open)?>\s*$/.test(line)){flush();const d=node('details');if(line.includes(' open'))d.open=true;current().append(d);stack.push(d);continue;}
    if(/^\s*<\/details>\s*$/.test(line)){flush();if(stack.length>1)stack.pop();else current().append(node('p',line));continue;}
    const summary=line.match(/^\s*<summary>(.*?)<\/summary>\s*$/);if(summary&&stack.length>1){flush();const s=node('summary');inline(s,summary[1]);current().append(s);continue;}
    const heading=line.match(/^(#{1,6})\s+(.+)$/);if(heading){flush();const h=node('h'+heading[1].length);inline(h,heading[2]);current().append(h);continue;}
    if(i+1<lines.length&&line.includes('|')&&/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i+1])){
      flush();const table=node('table'),head=node('tr');for(const x of cells(line)){const th=node('th');inline(th,x);head.append(th);}table.append(head);i++;
      while(i+1<lines.length&&lines[i+1].includes('|')&&lines[i+1].trim()){const tr=node('tr');for(const x of cells(lines[++i])){const td=node('td');inline(td,x);tr.append(td);}table.append(tr);}current().append(table);continue;
    }
    const list=line.match(/^\s*(?:([-*])|\d+\.)\s+(.+)$/);if(list){flush();const tag=list[1]?'ul':'ol',group=node(tag);let entry=list;
      while(entry){const li=node('li');inline(li,entry[2]);group.append(li);const next=(lines[i+1]||'').match(/^\s*(?:([-*])|\d+\.)\s+(.+)$/);if(!next||(next[1]?'ul':'ol')!==tag)break;i++;entry=next;}current().append(group);continue;}
    if(/^>\s?/.test(line)){flush();const q=node('blockquote');inline(q,line.replace(/^>\s?/,''));current().append(q);continue;}
    if(!line.trim()){flush();continue;}paragraph.push(line);
  }flush();
}
globalThis.ReviewMarkdown={render:renderMarkdown};
})();
