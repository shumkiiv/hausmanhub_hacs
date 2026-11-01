import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const path = new URL('../custom_components/hausman_hub/frontend/hausman-hub-room-lighting.js', import.meta.url);
const source = readFileSync(path, 'utf8').replace(/^import .*;\n/gm, '').replace(/export /g, '');
const context = vm.createContext({console, setTimeout: () => 1, clearTimeout: () => {}});
vm.runInContext(source + '\nglobalThis.renderManual = renderManualProtection; globalThis.keys = EDITABLE_KEYS;', context);
function node(tag, cls, text) { return {tag, cls, text, children: [], listeners: {},
  appendChild(child) {this.children.push(child);return child;},
  addEventListener(name, fn) {this.listeners[name] = fn;}}; }
function nodes(root) {return [root, ...root.children.flatMap(nodes)];}

test('manual return controls preserve exact seconds until changed and submit editable policy', () => {
  const draft = {manualOffProtection:{enabled:true,minimumIntervalSeconds:601,releaseMode:'timer_only',stableAbsenceSeconds:30},
    manualControlReturn:{prioritySeconds:3601,clearOnAway:false}};
  const store = {draft, draftRevision:0, previewRequest:0};
  const panel = {_roomLighting:{byRoom:{demo:store}}, _render() {}};
  const root = context.renderManual(panel, 'demo', store, {el:node});
  assert.equal(draft.manualControlReturn.prioritySeconds, 3601);
  assert.equal(draft.manualOffProtection.minimumIntervalSeconds, 601);
  assert(context.keys.includes('manualControlReturn'));
  const all = nodes(root);
  const on = all.find(n => n.children[0]?.text === 'После ручного включения, минут').children[1];
  const off = all.find(n => n.children[0]?.text === 'После ручного выключения, минут').children[1];
  on.value = '90'; on.listeners.change();
  off.value = '20'; off.listeners.change();
  assert.equal(draft.manualControlReturn.prioritySeconds,5400);
  assert.equal(draft.manualOffProtection.minimumIntervalSeconds,1200);
  const away = all.find(n => n.tag === 'label' && n.children.some(c => c.text === 'Снимать ручной приоритет при уходе')).children[0];
  assert.equal(away.checked,false); away.checked=true; away.listeners.change();
  assert.equal(draft.manualControlReturn.clearOnAway,true);
});

test('real browser renders and edits the bounded manual settings card', {skip: !process.env.PLAYWRIGHT_MODULE}, async () => {
  const {chromium} = await import(process.env.PLAYWRIGHT_MODULE);
  const browser = await chromium.launch({headless:true, executablePath:process.env.BROWSER_PATH});
  try {
    const page = await browser.newPage({viewport:{width:1100,height:900}});
    await page.setContent('<div id="host"></div>');
    await page.addScriptTag({content:source+'\nwindow.manualRender=renderManualProtection;'});
    const css = readFileSync(new URL('../custom_components/hausman_hub/frontend/hausman-hub-room-lighting.css',import.meta.url),'utf8');
    const tokens = readFileSync(new URL('../custom_components/hausman_hub/frontend/hausman-hub-tokens.css',import.meta.url),'utf8');
    await page.evaluate(({css,tokens}) => {
      const root=document.querySelector('#host').attachShadow({mode:'open'});
      root.innerHTML='<style>'+tokens+css+':host{display:block;padding:24px;font-family:Inter,sans-serif;background:#101318;color:white}</style>';
      const deps={el:(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text)n.textContent=text;return n;}};
      const store={draft:{manualOffProtection:{enabled:true,minimumIntervalSeconds:600,releaseMode:'timer_only',stableAbsenceSeconds:30},manualControlReturn:{prioritySeconds:3600,clearOnAway:true}},draftRevision:0,previewRequest:0};
      window.cardStore=store;
      const panel={_roomLighting:{byRoom:{demo:store}},_render(){}};
      root.appendChild(window.manualRender(panel,'demo',store,deps));
    }, {css,tokens});
    const host=page.locator('#host');
    const on=host.getByLabel('После ручного включения, минут');
    const off=host.getByLabel('После ручного выключения, минут');
    assert.equal(await on.inputValue(),'60'); assert.equal(await off.inputValue(),'10');
    assert.equal(await host.getByLabel('Устойчивое отсутствие, секунд').isDisabled(),true);
    await on.fill('90'); await on.dispatchEvent('change');
    await off.fill('20'); await off.dispatchEvent('change');
    await host.getByLabel('Снимать ручной приоритет при уходе').uncheck();
    assert.deepEqual(await page.evaluate(()=>[window.cardStore.draft.manualControlReturn.prioritySeconds,window.cardStore.draft.manualOffProtection.minimumIntervalSeconds,window.cardStore.draft.manualControlReturn.clearOnAway]),[5400,1200,false]);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
    if(process.env.UI_SCREENSHOT) await page.screenshot({path:process.env.UI_SCREENSHOT,fullPage:true});
  } finally {await browser.close();}
});
