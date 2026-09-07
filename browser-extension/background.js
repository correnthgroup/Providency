// All traffic stays on loopback. Target access is checked again for every command.
const allowedOrigins = new Set(['https://web.vectorcrypto.com', 'https://web.telegram.org']);
let socket = null, heartbeat = null, autoAttach = false, nextSession = 1;
const sessions = new Map();
const attaching = new Map();

function allowed(tab) {
  try { return allowedOrigins.has(new URL(tab.url).origin); } catch { return false; }
}
function emit(message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type:'cdp', message}));
}
function removed(tabId) {
  const entry = sessions.get(tabId);
  if (!entry) return;
  sessions.delete(tabId);
  emit({method:'Target.detachedFromTarget', params:{sessionId:entry.id, targetId:entry.info.targetId}});
}
async function detachAll() {
  autoAttach = false;
  await Promise.allSettled([...sessions.keys()].map(tabId => chrome.debugger.detach({tabId})));
  sessions.clear();
}
async function attach(tab) {
  if (!allowed(tab)) return;
  if (sessions.has(tab.id)) return sessions.get(tab.id);
  if (attaching.has(tab.id)) return attaching.get(tab.id);
  const pending = (async () => {
    await chrome.debugger.attach({tabId:tab.id}, '1.3');
    const {targetInfo} = await chrome.debugger.sendCommand({tabId:tab.id}, 'Target.getTargetInfo');
    const entry = {tabId:tab.id, id:`providency-${nextSession++}`, info:targetInfo, children:new Set()};
    sessions.set(tab.id, entry);
    emit({method:'Target.attachedToTarget', params:{sessionId:entry.id,
      targetInfo:{...targetInfo, attached:true}, waitingForDebugger:false}});
    return entry;
  })();
  attaching.set(tab.id, pending);
  try { return await pending; } finally { attaching.delete(tab.id); }
}
async function command({method, params = {}, sessionId}) {
  if (method === 'Browser.getVersion') return {protocolVersion:'1.3', product:'Chrome/Providency', userAgent:navigator.userAgent};
  if (method === 'Browser.setDownloadBehavior') return {};
  if (method === 'Target.setAutoAttach' && !sessionId) {
    autoAttach = !!params.autoAttach;
    if (autoAttach) await Promise.all((await chrome.tabs.query({})).filter(allowed).map(attach));
    else await detachAll();
    return {};
  }
  if (['Target.createTarget','Target.closeTarget','Browser.close','Page.navigate'].includes(method)) {
    throw new Error('O onboarding não abre, fecha ou navega as suas abas.');
  }
  let entry = [...sessions.values()].find(value => value.id === sessionId || value.children.has(sessionId));
  if (!entry && !sessionId) entry = sessions.values().next().value;
  if (!entry || !allowed(await chrome.tabs.get(entry.tabId))) throw new Error('Aba não autorizada ou desconectada.');
  if (method === 'Target.getTargetInfo' && entry.id === sessionId) return {targetInfo:entry.info};
  const target = {tabId:entry.tabId};
  if (sessionId && sessionId !== entry.id) target.sessionId = sessionId;
  return await chrome.debugger.sendCommand(target, method, params);
}
chrome.debugger.onEvent.addListener((source, method, params) => {
  const entry = sessions.get(source.tabId);
  if (!entry) return;
  if (method === 'Target.attachedToTarget' && params?.sessionId) entry.children.add(params.sessionId);
  if (method === 'Target.detachedFromTarget' && params?.sessionId) entry.children.delete(params.sessionId);
  emit({sessionId:source.sessionId || entry.id, method, params});
});
chrome.debugger.onDetach.addListener(source => removed(source.tabId));
chrome.tabs.onRemoved.addListener(removed);
chrome.tabs.onUpdated.addListener(async (tabId, change, tab) => {
  if (sessions.has(tabId) && !allowed(tab)) {
    await chrome.debugger.detach({tabId}).catch(() => {});
    removed(tabId);
  } else if (autoAttach && allowed(tab) && change.status === 'complete') {
    await attach(tab).catch(() => {});
  }
});
async function disconnect() {
  clearInterval(heartbeat);
  heartbeat = null;
  await detachAll();
  const previous = socket;
  socket = null;
  previous?.close();
}
async function connect(port, code) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535 || !code) throw new Error('Confira a porta e o código.');
  await disconnect();
  return await new Promise((resolve, reject) => {
    const current = new WebSocket(`ws://127.0.0.1:${port}/browser-bridge/extension`);
    socket = current;
    let paired = false;
    const timeout = setTimeout(() => { current.close(); reject(new Error('O Providency não respondeu.')); }, 12000);
    current.onopen = () => current.send(JSON.stringify({code}));
    current.onmessage = async event => {
      let packet;
      try { packet = JSON.parse(event.data); } catch { return; }
      if (packet.type === 'paired') {
        paired = true;
        clearTimeout(timeout);
        heartbeat = setInterval(() => {
          if (current.readyState === WebSocket.OPEN) current.send(JSON.stringify({type:'ping'}));
        }, 20000);
        resolve({ok:true, message:'Conectado. Volte ao Providency e clique em OK.'});
      } else if (packet.type === 'detach') {
        await detachAll();
      } else if (packet.type === 'cdp') {
        const {id, sessionId} = packet.message;
        try { emit({id, sessionId, result:await command(packet.message)}); }
        catch { emit({id, sessionId, error:{code:-32000, message:'Não foi possível acessar a aba autorizada.'}}); }
      }
    };
    current.onerror = () => { clearTimeout(timeout); reject(new Error('Abra o Providency e confira a porta.')); };
    current.onclose = () => {
      clearTimeout(timeout);
      if (socket === current) { clearInterval(heartbeat); socket = null; void detachAll(); }
      if (!paired) reject(new Error('Conexão recusada. Gere um novo código no Providency.'));
    };
  });
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return false;
  if (message.type === 'connect') {
    connect(message.port, message.code).then(respond).catch(error => respond({ok:false,message:error.message}));
    return true;
  }
  if (message.type === 'disconnect') { disconnect().then(() => respond({ok:true})); return true; }
  return false;
});
