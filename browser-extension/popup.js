document.querySelector('#pair').addEventListener('submit', async event => {
  event.preventDefault();
  const result = await chrome.runtime.sendMessage({type:'connect',
    port:Number(document.querySelector('#port').value), code:document.querySelector('#code').value.trim()});
  document.querySelector('#status').textContent = result.message;
  if (result.ok) document.querySelector('#code').value = '';
});
document.querySelector('#disconnect').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({type:'disconnect'});
  document.querySelector('#status').textContent = 'Desconectado.';
});
