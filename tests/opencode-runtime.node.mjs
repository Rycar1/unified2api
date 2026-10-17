import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';
import {startProxy} from '../vendor/opencode2api/src/proxy.js';

test('Responses completion metadata and previous_response_id preserve one upstream session', async () => {
  const sessions = [];
  const backend = http.createServer(async (req, res) => {
    for await (const chunk of req) { /* consume synthetic request body */ }
    res.setHeader('Content-Type','application/json');
    let data = {};
    if (req.url === '/global/health') data = {healthy:true,version:'1.19.0'};
    else if (req.url === '/config/providers') data = {providers:[{id:'opencode',models:{'synthetic-free':{name:'Synthetic'}}}]};
    else if (req.url === '/config') data = {plugin:['/synthetic/opencode2api-tool-lock.js']};
    else if (req.method === 'POST' && req.url === '/session') data = {id:'ses_synthetic'};
    else if (req.method === 'POST' && req.url.endsWith('/message')) {
      sessions.push(req.url);
      data = {info:{id:'msg_synthetic',role:'assistant',finish:'stop'},parts:[{type:'text',text:'synthetic'}]};
    } else if (req.url.endsWith('/message')) data = [];
    res.end(JSON.stringify(data));
  });
  await new Promise(resolve => backend.listen(0,'127.0.0.1',resolve));
  const proxy = startProxy({PORT:18789,BIND_HOST:'127.0.0.1',API_KEY:'synthetic-private',
    MANAGE_BACKEND:false,OPENCODE_SERVER_URL:`http://127.0.0.1:${backend.address().port}`,DISABLE_TOOLS:true});
  try {
    if (!proxy.server.listening) await new Promise(resolve => proxy.server.once('listening',resolve));
    const send = async (extra = {}) => {
      const response = await fetch('http://127.0.0.1:18789/v1/responses',{method:'POST',
        headers:{Authorization:'Bearer synthetic-private','Content-Type':'application/json'},
        body:JSON.stringify({model:'synthetic-free',input:'synthetic',stream:false,...extra})});
      assert.equal(response.status,200);
      return response.json();
    };
    const first = await send();
    assert.equal(first.object,'response');
    assert.equal(first.status,'completed');
    assert.equal(typeof first.created_at,'number');
    assert.ok(first.output.length);
    const next = await send({previous_response_id:first.id});
    assert.equal(next.status,'completed');
    assert.equal(sessions.length,2);
    assert.equal(sessions[0],sessions[1]);
  } finally {
    proxy.killBackend();
    await new Promise(resolve => proxy.server.close(resolve));
    await new Promise(resolve => backend.close(resolve));
  }
});
