document.getElementById('identifier').textContent = chrome.runtime.id;
const status = document.getElementById('status');
async function refresh() {
  const value = await chrome.runtime.sendMessage({action: 'status'});
  status.textContent = value.connected ? 'Connected · access is visible in Jarvix' : (value.error || 'Disconnected');
}
document.getElementById('connect').addEventListener('click', async () => {
  const allowed = await chrome.permissions.request({origins: ['http://*/*', 'https://*/*']});
  if (!allowed) { status.textContent = 'Page access was not granted.'; return; }
  await chrome.runtime.sendMessage({action: 'connect'}); await refresh();
});
document.getElementById('disconnect').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({action: 'disconnect'}); await refresh();
});
refresh();

