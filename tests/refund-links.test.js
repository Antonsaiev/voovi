const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
    constructor(tag='div') { this.tagName=tag; this.children=[]; this.events={}; this.value=''; this.disabled=false; this.textContent=''; this.dataset={}; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children=items; }
    setAttribute(name,value) { this[name]=value; }
    addEventListener(name,callback) { this.events[name]=callback; }
    querySelectorAll(selector) { return this.children.flatMap(child=>[(selector==='input'&&child.tagName==='input')?child:null,...child.querySelectorAll(selector)]).filter(Boolean); }
    reportValidity() { return true; }
    get lastChild() { return this.children.at(-1); }
    set innerHTML(value) { throw new Error('Unsafe HTML write'); }
}
const nodes={};
for(const key of ['#payment-refund-search-form','#payment-refund-link-form','#payment-refund-inn','#payment-refund-amount','[data-refund-results]','[data-refund-message]','[data-refund-status]','[data-refund-totals]','[data-refund-editor]','[data-refund-blocked]','[data-refund-links]']) nodes[key]=new Element();
const search=nodes['#payment-refund-search-form'], link=nodes['#payment-refund-link-form'];
const searchButton=new Element('button'), linkButton=new Element('button'), csrf=new Element('input'); csrf.value='safe-token';
search.querySelector=()=>searchButton;
link.querySelector=selector=>selector==='button'?linkButton:csrf; link.action='/payments/beznal/add/refunds/link';
const root=new Element(); root.dataset={randomKey:'key&value',remainingAmount:'75.55',linkedAmount:'24.45',canLink:'1'}; root.querySelector=selector=>nodes[selector];
const inn=nodes['#payment-refund-inn']; inn.value='1234567890';
const amount=nodes['#payment-refund-amount'], tbody=nodes['[data-refund-results]'];
const calls=[]; let confirms=0;
function fetch(url,options) {
    return new Promise((resolve,reject)=>{
        const call={url,options,resolve,reject}; calls.push(call);
        if(options.signal) options.signal.addEventListener('abort',()=>{const error=new Error('aborted');error.name='AbortError';reject(error);});
    });
}
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../analitic/static/js/custom/refund_links.js'),'utf8'),{
    document:{getElementById:id=>id==='payment-refund-links'?root:null,createElement:tag=>new Element(tag),createTextNode:text=>{const e=new Element();e.textContent=text;return e;}},
    window:{confirm:()=>{confirms++;return true;}}, URLSearchParams,AbortController,fetch
});
const event={preventDefault(){}};
const summary=(remaining='75.55',linked='24.45')=>({can_link:true,remaining_amount:remaining,linked_amount:linked,complete:false,links:[]});
const result={id:17,number:'R1',date:'2026-11-01',recipient:'<img src=x onerror=alert(1)>',inn:'1234567890',organization:'O1',amount:'60.00',available_amount:'40.01',purpose:'refund'};
async function searchRows() {
    const pending=search.events.submit(event);
    assert.equal(calls.at(-1).options.body,undefined);
    calls.at(-1).resolve({ok:true,json:async()=>({results:[result],summary:summary()})}); await pending;
    const radio=tbody.querySelectorAll('input')[0]; tbody.events.change({target:radio});
}
(async()=>{
    assert.equal(calls.length,0,'opening details must not search');
    await searchRows();
    assert.match(calls[0].url,/random_key=key%26value/);
    assert.equal(amount.value,'40.01'); assert.equal(linkButton.disabled,false);
    amount.value='40.02';amount.events.input();assert.equal(linkButton.disabled,true);
    amount.value='30.10';amount.events.input();assert.equal(linkButton.disabled,false);
    const pending=link.events.submit(event), count=calls.length;
    await link.events.submit(event);assert.equal(calls.length,count,'double POST blocked');
    assert.equal(confirms,1);assert.equal(calls.at(-1).options.body.get('amount'),'30.10');
    assert.equal(calls.at(-1).options.body.get('outgoing_payment_id'),'17');
    assert.equal(calls.at(-1).options.body.get('csrfmiddlewaretoken'),'safe-token');
    calls.at(-1).resolve({ok:true,json:async()=>({message:'Saved',summary:summary('45.45','54.55')})});await pending;
    assert.equal(link.hidden,true);assert.equal(linkButton.disabled,true);assert.equal(searchButton.disabled,false);
    assert.match(nodes['[data-refund-totals]'].textContent,/45,45/);
    await searchRows();
    const failed=link.events.submit(event);calls.at(-1).resolve({ok:false,json:async()=>({message:'Safe refusal <script>'})});await failed;
    assert.equal(link.hidden,true);assert.equal(searchButton.disabled,false);assert.equal(linkButton.disabled,true);
    assert.equal(nodes['[data-refund-message]'].textContent,'Safe refusal <script>');
    const stale=search.events.submit(event);let resolveBody;
    calls.at(-1).resolve({ok:true,json:()=>new Promise(resolve=>{resolveBody=resolve;})});
    await new Promise(setImmediate);
    inn.value='123456789012';inn.events.input();
    const current=search.events.submit(event);
    resolveBody({results:[result],summary:{...summary('0.00','100.00'),can_link:false,complete:true}});await stale;
    assert.equal(nodes['[data-refund-editor]'].hidden,false,'stale JSON must not change summary');
    calls.at(-1).resolve({ok:true,json:async()=>({results:[],summary:summary('45.45','54.55')})});await current;
    assert.equal(link.hidden,true);assert.equal(searchButton.disabled,false);
    console.log('Refund UI: manual search, exact cents, single confirmed POST, safe errors and stale search — PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
