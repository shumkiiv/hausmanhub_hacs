"""Exercise the unmodified exported editor in real Chromium, without HA.

HH-016.c guarded return: the owner button must request the one-time intent
first, then POST the guarded return with the exact token. There is no legacy
``/force-auto`` fallback: the stored-evidence acceptance belongs to the
guarded route only, so the button must never call it.
"""

import functools
import http.server
import os
from pathlib import Path
import subprocess
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_real_panel_supplies_confirmation_handler():
    """The real screen must never render the button without a confirm handler."""
    panel = (ROOT / "custom_components" / "hausman_hub" / "frontend"
             / "hausman-hub-panel.js").read_text(encoding="utf-8")
    assert "confirm: (message) => window.confirm(message)" in panel
    editor = (ROOT / "custom_components" / "hausman_hub" / "frontend"
              / "hausman-hub-room-lighting.js").read_text(encoding="utf-8")
    assert 'if (typeof deps.confirm !== "function" || !deps.confirm(message)) return;' in editor


def test_force_auto_button_receipts_refresh_and_draft_in_chromium():
    module = os.environ.get("HAUSMAN_PLAYWRIGHT_MODULE")
    if not module:
        pytest.skip("set HAUSMAN_PLAYWRIGHT_MODULE to an installed playwright module")

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = r'''
      import assert from 'node:assert/strict';
      import { pathToFileURL } from 'node:url';
      const {chromium} = await import(pathToFileURL(process.argv[1]));
      const browser = await chromium.launch({headless: true,
        executablePath: process.env.HAUSMAN_CHROMIUM_EXECUTABLE || undefined});
      const origin = process.argv[2];
      const token = 'a'.repeat(64);
      const modes = ['returned','blocked','incomplete','old','wrong-room','wrong-version',
                     'unknown','network','token-invalid','intent-network','intent-missing',
                     'version-mismatch','no-confirm-handler','status-error','preview-error',
                     'cancel','double'];
      try {
        for (const mode of modes) {
          const page = await browser.newPage();
          const errors = [];
          page.on('pageerror', error => errors.push(String(error)));
          await page.goto(origin + '/custom_components/hausman_hub/frontend/');
          await page.evaluate(async ({mode, origin, token}) => {
            const {renderRoomLightingEditor} = await import(origin + '/custom_components/hausman_hub/frontend/hausman-hub-room-lighting.js');
            document.body.replaceChildren();
            const container = document.createElement('div');
            document.body.append(container);
            const config = {name:'Saved',version:7,updatedAt:1,autoAdopt:true,devices:{light_targets:[]}};
            const store = {loaded:true,loading:false,saving:false,config,draft:{...config,name:'Unsaved'},
                           templates:[],catalog:[],section:'overview',error:'old-error',notice:'old-success',
                           preview:{summary:'old-preview'},status:{old:true}};
            const calls = [];
            const panel = {_roomLighting:{byRoom:{room:store}},_render(){},_hass:{async callApi(method,path,body){
              calls.push({method,path,body:structuredClone(body)});
              if(path.endsWith('/force-auto/intent')) {
                if(mode === 'intent-network') throw {body:{message:'intent failure'}};
                if(mode === 'intent-missing') {
                  return {contract:{name:'hausman-hub-room-lighting-return-intent',version:1},
                    room:'room', configurationVersion:7};
                }
                if(mode === 'version-mismatch') {
                  return {contract:{name:'hausman-hub-room-lighting-return-intent',version:1},
                    room:'room', configurationVersion:9, intentToken:token, expiresInSeconds:60};
                }
                return {contract:{name:'hausman-hub-room-lighting-return-intent',version:1},
                  room:'room', configurationVersion:7, intentToken:token, expiresInSeconds:60};
              }
              if(path.endsWith('/force-auto/guarded')) {
                await new Promise(resolve => setTimeout(resolve, 40));
                if(mode === 'network') throw {body:{message:'network failure'}};
                if(mode === 'old') return {released:1};
                if(mode === 'token-invalid') {
                  return {contract:{name:'hausman-hub-room-lighting-guarded-return',version:1},
                    room:'room', known:true, outcome:'blocked', blockers:['return_intent_invalid']};
                }
                return {contract:{name:'hausman-hub-room-lighting-guarded-return',version:mode==='wrong-version'?2:1},
                  room:mode==='wrong-room'?'other':'room', known:mode!=='unknown',
                  outcome:['blocked','incomplete'].includes(mode)?mode:'returned', blockers:['new_manual_action']};
              }
              if(path.endsWith('/status')) {
                if(mode === 'status-error') throw new Error('status unavailable');
                return {fresh:true};
              }
              if(path.endsWith('/preview')) {
                if(mode === 'preview-error') throw {body:{message:'preview failure'}};
                return {summary:'fresh-preview'};
              }
              throw new Error('unexpected API: '+path);
            }}};
            const fullDeps = {el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;
                          if(text != null)node.textContent=text;return node;},
                          confirm:()=>mode!=='cancel'};
            // The real panel must always supply a confirmation handler; a
            // missing one has to fail closed with no requests at all.
            const deps = mode === 'no-confirm-handler'
              ? {el: fullDeps.el}
              : fullDeps;
            renderRoomLightingEditor(panel,container,{id:'room'},deps,{screen:true});
            window.testState={store,calls};
          }, {mode,origin,token});
          await page.locator('[data-testid="room-lighting:force-auto"]').click();
          if(mode === 'double') {
            await page.locator('[data-testid="room-lighting:force-auto"]').dispatchEvent('click');
          }
          if(mode !== 'cancel' && mode !== 'no-confirm-handler') {
            await page.waitForFunction(() => window.testState.calls.length>=2 && !window.testState.store.saving
              && !window.testState.store.previewing);
          }
          const result = await page.evaluate(() => ({store:window.testState.store,calls:window.testState.calls}));
          assert.equal(result.store.draft.name,'Unsaved',mode);
          assert.equal(result.store.config.name,'Saved',mode);
          assert.equal(result.store.config.version,7,mode);
          const intentCalls = result.calls.filter(c=>c.path.endsWith('/force-auto/intent'));
          const guardedCalls = result.calls.filter(c=>c.path.endsWith('/force-auto/guarded'));
          const legacyCalls = result.calls.filter(c=>c.path.endsWith('/force-auto')
            && !c.path.endsWith('/force-auto/intent') && !c.path.endsWith('/force-auto/guarded'));
          assert.equal(legacyCalls.length,0,'no legacy fallback: '+mode);
          const silent = mode==='cancel' || mode==='no-confirm-handler';
          if(mode==='no-confirm-handler') assert.equal(result.calls.length,0,mode);
          assert.equal(intentCalls.length,silent?0:1,mode);
          if(mode==='intent-network' || mode==='intent-missing' || mode==='version-mismatch') {
            assert.equal(guardedCalls.length,0,'no guarded call after failed intent: '+mode);
          } else {
            assert.equal(guardedCalls.length,silent?0:1,mode);
          }
          assert.equal(result.calls.filter(c=>c.path.endsWith('/config') || c.method==='PUT').length,0,mode);
          if(!silent) {
            assert.equal(result.store.saving,false,mode);
            if(guardedCalls.length === 1) {
              assert.equal(result.calls[0].method,'GET',mode);
              assert.equal(result.calls[1].method,'POST',mode);
              assert.equal(result.calls[1].body.intentToken,token,mode);
            }
            const statusIndex = guardedCalls.length === 1 ? 2 : 1;
            assert.equal(result.calls[statusIndex].method,'GET',mode);
            assert.ok(result.calls[statusIndex].path.endsWith('/status'),mode);
            if(!['status-error','preview-error'].includes(mode)) {
              assert.equal(result.calls[statusIndex+1].body.name,'Unsaved',mode);
            }
            if(['returned','double'].includes(mode)) {
              assert.equal(result.store.error,'',mode);
              assert.match(result.store.notice,/включение этим ответом не подтверждено/,mode);
              assert.equal(result.store.preview.summary,'fresh-preview',mode);
            } else {
              assert.ok(result.store.error,mode);
              if(mode==='token-invalid') {
                assert.match(result.store.error,/одноразовое подтверждение истекло/i,mode);
              }
              if(mode==='intent-missing') {
                assert.match(result.store.error,/не выдал одноразовое подтверждение/,mode);
              }
              if(mode==='version-mismatch') {
                assert.match(result.store.error,/профиль комнаты изменился/i,mode);
              }
              if(!['status-error','preview-error'].includes(mode)) assert.equal(result.store.notice,'',mode);
            }
          }
          assert.deepEqual(errors,[],mode);
          await page.close();
        }
        console.log('17 real-browser force-auto scenarios PASS');
      } finally { await browser.close(); }
    '''
    try:
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, module,
             f"http://127.0.0.1:{server.server_port}"],
            capture_output=True, text=True, timeout=90, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "17 real-browser force-auto scenarios PASS" in result.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
