(()=>{
const el=id=>document.getElementById(id);
const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
let model=null,selected=null,toastTimer,loading=false;
const queueKey=()=> 'issue-materials-to-html:pending:'+model.scope;
function readQueue(){try{const value=JSON.parse(localStorage.getItem(queueKey())||'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}catch(e){return {};}}
function writeQueue(q){try{localStorage.setItem(queueKey(),JSON.stringify(q));}catch(e){}}
function dropPending(key){const q=readQueue();delete q[key];writeQueue(q);}
function toast(message){el('toast').textContent=message;el('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el('toast').hidden=true,3000);}
async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),keepalive:true}:{});const data=await response.json();if(!response.ok){const e=new Error(data.error||'请求未完成');e.status=response.status;throw e;}return data;}
function progress(item){return item.fields.filter(f=>f.copied).length+'/'+item.fields.length+' 项已复制';}
function renderNav(){el('issues').replaceChildren();for(const item of model.items){const button=node('button');button.className='issue'+(selected?.id===item.id?' active':'');button.setAttribute('aria-current',selected?.id===item.id?'true':'false');button.append(node('strong',item.title),node('small',item.kind+' · '+progress(item)));button.onclick=()=>choose(item.id,true);el('issues').append(button);}}
function updateProgress(){renderNav();for(const button of document.querySelectorAll('.copy-button')){const field=model.items.flatMap(i=>i.fields).find(f=>f.key===button.dataset.key);if(field){button.textContent=field.copied?'已复制 · 再复制':'复制';button.setAttribute('data-copied',String(field.copied));}}}
function choose(id,persist=false){selected=model.items.find(i=>i.id===id)||model.items[0];renderNav();el('loading').hidden=!!selected;el('report').hidden=!selected;if(!selected){el('loading').textContent='暂无已整理好的投稿内容。';return;}
 el('kind').textContent=model.library+' / '+selected.kind;const url=new URL(selected.official_url);if(!['http:','https:'].includes(url.protocol))throw Error('Invalid official URL');el('official').href=url.href;el('official').textContent=selected.official_label+' ↗';el('note').textContent=selected.note;el('note').hidden=!selected.note;el('notice').textContent=model.notice;el('notice').hidden=!model.notice;
 el('fields').replaceChildren();for(const field of selected.fields){const section=node('section');section.className='field';const head=node('div');head.className='field-head';const button=node('button','复制');button.className='copy-button';button.dataset.key=field.key;button.setAttribute('aria-label','复制 '+field.label);button.onclick=()=>copyField(selected.id,field,button);head.append(node('h2',field.label),button);section.append(head);
  if(field.id==='title'){const title=node('p',field.text);title.className='title-content';section.append(title);}
  else if(field.type==='dropdown'){const value=node('p',field.text);value.className='choice';const help=node('p',field.multiple?'在官方页面选择这些值。':'在官方页面选择此值。');help.className='choice-help';section.append(value,help);}
  else{const content=node('div');content.className='payload';if(field.render){const pre=node('pre',field.text);pre.className='raw-payload';content.append(pre);}else ReviewMarkdown.render(content,field.text);if(field.purpose==='environment')content.className+=' environment';section.append(content);}
  el('fields').append(section);
 }updateProgress();if(window.location&&new URLSearchParams(window.location.search).has('checkLayout')){const root=document.documentElement;root.dataset.viewportWidth=String(window.innerWidth);root.dataset.pageWidth=String(root.scrollWidth);root.dataset.mainWidth=String(el('main').getBoundingClientRect().width);root.dataset.reportWidth=String(el('report').getBoundingClientRect().width);}if(persist){api('/api/selection',{issue_id:selected.id}).catch(()=>{});window.scrollTo(0,0);}}
async function writeClipboard(text){if(navigator.clipboard&&window.isSecureContext){try{await navigator.clipboard.writeText(text);return true;}catch(e){}}
 const box=node('textarea');box.value=text;box.style.position='fixed';box.style.left='-10000px';document.body.append(box);box.select();let ok=false;try{ok=document.execCommand('copy');}catch(e){}box.remove();return ok;
}
async function saveCopy(issue_id,key){await api('/api/copied',{issue_id,key});dropPending(key);const item=model.items.find(i=>i.id===issue_id),field=item?.fields.find(f=>f.key===key);if(field)field.copied=true;updateProgress();}
async function copyField(issue_id,field,button){button.disabled=true;try{
 if(!await writeClipboard(field.text)){const box=node('textarea');box.className='manual-copy';box.readOnly=true;box.value=field.text;button.parentElement?.parentElement?.append(box);box.focus();box.select();toast('已选中原文，请按 Ctrl+C；此次手动复制未自动标记。');return;}
 const q=readQueue();q[field.key]={issue_id,key:field.key};writeQueue(q);
 try{await saveCopy(issue_id,field.key);toast('已复制，进度已保存。');}
 catch(e){if(e.status===409){const current=readQueue();delete current[field.key];writeQueue(current);toast('稿件已更新，请刷新后重新复制。');}else{toast('已复制；进度暂未保存，将自动重试。');retryPending(0);}}
 }finally{button.disabled=false;}}
async function retryPending(attempt=0){if(!model)return;const keys=new Set(model.items.flatMap(i=>i.fields).map(f=>f.key));const q=readQueue();let remaining=false;
 for(const [key,value]of Object.entries(q)){if(!keys.has(key)){dropPending(key);continue;}try{await saveCopy(value.issue_id,key);}catch(e){if(e.status===409){dropPending(key);}else remaining=true;}}
 if(remaining&&attempt<2)setTimeout(()=>retryPending(attempt+1),[1000,3000,10000][attempt]);
}
async function load(){if(loading)return;loading=true;try{const next=await api('/api/review');const previous=selected?.id;model=next;el('library').textContent=model.library;document.title=model.library+' · Issue 稿件';choose(previous||model.selected_id);retryPending();}catch(e){if(!model){el('loading').textContent='无法读取报告，请确认本地服务仍在运行。';}}finally{loading=false;}}
window.addEventListener('online',()=>retryPending());window.addEventListener('focus',()=>{if(model)load();});load();
})();
