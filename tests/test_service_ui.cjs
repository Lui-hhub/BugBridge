// Copy-only frontend against a real local HTTP service, with an emulated DOM.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const base=process.argv[2],assets=process.argv[3];let copies=0;
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attrs={};this.style={};this.value='';this.hidden=false;this.textContent='';}
 append(...xs){for(const x of xs){x.parent=this;this.children.push(x);}}
 replaceChildren(...xs){this.children=[];this.append(...xs);}
 setAttribute(k,v){this.attrs[k]=v;}
 select(){}focus(){}remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
 set innerHTML(_){throw Error('Untrusted markup must remain data');}
}
const all=x=>[x,...x.children.flatMap(all)];
async function setup(){const elements=new Map(),clipboard=[],stored={};const get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
 const context={document:{getElementById:get,createElement:tag=>new Element(tag),body:new Element('body'),querySelectorAll:selector=>all(get('fields')).filter(x=>selector==='.copy-button'&&x.className==='copy-button'),execCommand:()=>false},
  navigator:{clipboard:{writeText:async text=>clipboard.push(text)}},window:{isSecureContext:true,addEventListener:()=>{},scrollTo:()=>{}},URL,
  fetch:(path,opts)=>fetch(base+path,opts),localStorage:{getItem:k=>stored[k]||null,setItem:(k,v)=>stored[k]=v},setTimeout:()=>1,clearTimeout:()=>{}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(assets+'/markdown.js','utf8'),context);vm.runInContext(fs.readFileSync(assets+'/review-app.js','utf8'),context);
 const until=Date.now()+3000;while(!get('fields').children.length&&Date.now()<until)await new Promise(r=>setImmediate(r));
 assert.ok(get('fields').children.length);return {context,get,clipboard};
}
(async()=>{
 const first=await setup();const initial=await (await fetch(base+'/api/review')).json();
 assert.strictEqual(first.get('issues').children.length,initial.items.length);
 assert.ok(!all(first.get('fields')).some(x=>['select','input','textarea'].includes(x.tagName)));
 assert.strictEqual(first.context.compromised,undefined);
 assert.ok(!all(first.get('fields')).some(x=>['script','iframe','img'].includes(x.tagName)));
 const buttons=all(first.get('fields')).filter(x=>x.className==='copy-button');
 const current=initial.items.find(i=>i.id===initial.selected_id)||initial.items[0];
 assert.strictEqual(buttons.length,current.fields.length);
 for(let i=0;i<buttons.length;i++){await buttons[i].onclick();assert.strictEqual(first.clipboard.at(-1),current.fields[i].text);copies++;}
 const saved=await (await fetch(base+'/api/review')).json();assert.ok(saved.items.find(i=>i.id===current.id).fields.every(f=>f.copied));
 const target=saved.items[1];first.get('issues').children[1].onclick();
 const until=Date.now()+3000;while(Date.now()<until){const state=await(await fetch(base+'/api/review')).json();if(state.selected_id===target.id)break;await new Promise(r=>setImmediate(r));}
 const second=await setup();const active=second.get('issues').children.find(x=>x.attrs['aria-current']==='true');assert.strictEqual(active.children[0].textContent,target.title);
 // A fresh browser-side store must not erase server-side copy progress.
 const restored=await(await fetch(base+'/api/review')).json();assert.ok(restored.items.find(i=>i.id===current.id).fields.every(f=>f.copied));
 console.log(JSON.stringify({pass:true,copied_fields:copies,refresh_restores_selection:true,server_retains_copy_progress:true,main_controls:'navigation and copy buttons only',method:'DOM emulation plus real HTTP API; no browser/OS clipboard claim'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
