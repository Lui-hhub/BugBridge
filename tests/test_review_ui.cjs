// Executes the generated page in a DOM emulator; never claims browser/OS coverage.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const script=html.slice(html.indexOf('<script>')+8,html.lastIndexOf('</script>'));
class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.style={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.parent=null;this.attrs={};}
  append(...items){for(const x of items){x.parent=this;this.children.push(x);}}
  replaceChildren(...items){this.children=[];this.append(...items);}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,v){this['on'+k]=v;}
  select(){selectedText=this.value;}
  focus(){}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
  set innerHTML(_){throw Error('Untrusted content must not be rendered as innerHTML');}
}
let selectedText='',clipboard=[],stored={};
const elements=new Map();
const get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
for(const id of ['actionfilter','coveragefilter','relationfilter','todofilter','reprofilter','kindfilter'])get(id).value='all';
const context={document:{getElementById:get,createElement:tag=>new Element(tag),body:new Element('body'),execCommand:()=>{clipboard.push(selectedText);return true;}},
  navigator:{clipboard:{writeText:async text=>clipboard.push(text)}},window:{isSecureContext:true},URL,
  localStorage:{getItem:k=>stored[k]||null,setItem:(k,v)=>stored[k]=v},setTimeout:()=>1,clearTimeout:()=>{}};
vm.createContext(context);vm.runInContext(script,context);
const data=JSON.parse(vm.runInContext('JSON.stringify(REVIEW)',context));
const model=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
assert.deepStrictEqual(data,model);
assert.strictEqual(get('queue').children.length,data.cards.length);
const walk=x=>[x,...x.children.flatMap(walk)];
let fields=0,selects=0,checks=0,uploads=0,instructions=0,evidenceCopies=0,archives=0,followups=0;
(async()=>{
for(const card of data.cards){
  context.selectIssue(card.id);
  assert.strictEqual(get('fieldselect').children.length,card.fields.length);
  instructions+=card.instructions.length;
  assert.ok(card.fields.every(f=>f.type!=='markdown'));
  if(['no_submission','needs_evidence'].includes(card.action.type)){
    archives++;assert.strictEqual(get('formpanel').hidden,true);assert.strictEqual(get('copytitle').disabled,true);assert.strictEqual(get('copyfield').disabled,true);
  }
  if(card.action.type==='existing_issue_comment'){
    followups++;assert.strictEqual(get('copytitle').disabled,true);assert.ok(card.fields.every(f=>f.id==='comment'));
    assert.strictEqual(get('target').href,card.action.target_url);
  }
  if(card.readiness.pending_todos)assert.strictEqual(get('todopanel').open,true);
  if(card.quality_warnings.length)assert.strictEqual(get('quality').hidden,false);
  for(let index=0;index<card.fields.length;index++){
    const field=card.fields[index];get('fieldselect').value=String(index);get('fieldselect').onchange();
    assert.strictEqual(get('description').textContent,field.description||'');assert.strictEqual(get('content').value,field.text);
    get('rawtab').onclick();assert.strictEqual(get('content').hidden,false);
    get('previewtab').onclick();assert.strictEqual(get('preview').hidden,false);assert.strictEqual(get('content').value,field.text);
    if(!get('copyfield').disabled){
      await get('copyfield').onclick();assert.strictEqual(clipboard.at(-1),field.text);
      get('bodytab').onclick();assert.strictEqual(get('content').value,card.body);
      await get('copyfield').onclick();assert.strictEqual(clipboard.at(-1),field.text);
      await get('copyview').onclick();assert.strictEqual(clipboard.at(-1),card.body);
      get('fieldtab').onclick();fields++;
    }
    if(field.type==='dropdown'){selects++;assert.strictEqual(get('typed').children[0].disabled,true);}
    if(field.type==='checkboxes'){checks++;for(const l of get('typed').children[0].children)assert.strictEqual(l.children[0].disabled,true);}
    if(field.type==='upload'){uploads++;assert.ok(get('typed').children[0].textContent.includes('手动'));}
  }
  const buttons=walk(get('runs')).filter(x=>x.tagName==='button');
  for(let i=0;i<buttons.length;i++){
    await buttons[i].onclick();assert.strictEqual(clipboard.at(-1),card.runs[i].source_text);evidenceCopies++;
  }
}
for(const [id,key]of [['actionfilter',x=>x.action.type],['coveragefilter',x=>x.upstream.coverage],['relationfilter',x=>x.upstream.relation]]){
  const chosen=data.cards[0];get(id).value=key(chosen);get(id).onchange();
  assert.strictEqual(get('queue').children.length,data.cards.filter(x=>key(x)===key(chosen)).length);
  get(id).value='all';get(id).onchange();
}
get('search').value='__definitely_no_matching_record__';get('search').oninput();assert.strictEqual(get('empty').hidden,false);assert.strictEqual(get('main').hidden,true);
get('search').value='';get('search').oninput();
context.selectIssue(data.cards[0].id);get('processed').checked=true;get('processed').onchange();assert.ok(stored['issue-materials-to-html:'+data.run_id]);
assert.strictEqual(context.compromised,undefined);
const first=data.cards.find(c=>['new_issue','existing_issue_comment'].includes(c.action.type)&&c.fields.some(f=>f.text));
if(first){context.selectIssue(first.id);get('fieldselect').value=String(first.fields.findIndex(f=>f.text));get('fieldselect').onchange();context.window.isSecureContext=false;
  await get('copyfield').onclick();assert.strictEqual(clipboard.at(-1),get('content').value);
}
context.previewTest=new Element('div');
vm.runInContext('renderMarkdown(previewTest, "# Test\\n\\n<details>\\n<summary>Evidence</summary>\\n\\n```python\\nprint(1)\\n```\\n\\n</details>\\n\\n<script>compromised=true</script>\\n[unsafe](javascript:alert) [safe](https://example.invalid/report)\\n\\n| A | B |\\n| --- | --- |\\n| one | two |")',context);
const rendered=walk(context.previewTest);
assert.ok(rendered.some(x=>x.tagName==='details'));assert.ok(rendered.some(x=>x.tagName==='pre'));assert.ok(rendered.some(x=>x.tagName==='table'));
assert.ok(!rendered.some(x=>['script','img','iframe'].includes(x.tagName)));assert.ok(rendered.filter(x=>x.tagName==='a').every(x=>x.href.startsWith('https://')));
const report={pass:true,method:'Node vm DOM emulation; no real browser or OS clipboard',records:data.cards.length,
  copied_fields:fields,dropdowns:selects,checkbox_groups:checks,uploads,instruction_elements:instructions,
  archive_records:archives,followup_records:followups,evidence_source_copies:evidenceCopies,
  selected_field_copy_from_body:true,local_file_copy_fallback:true,run_scoped_storage:true,untrusted_innerHTML:false,
  rendered_markdown_subset:true,action_and_coverage_filters:true};
if(process.argv[4])fs.writeFileSync(process.argv[4],JSON.stringify(report,null,2)+'\n');
process.stdout.write(JSON.stringify(report)+'\n');
})().catch(e=>{console.error(e);process.exitCode=1;});
