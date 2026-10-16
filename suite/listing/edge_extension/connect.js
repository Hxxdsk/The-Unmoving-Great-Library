// Pairing is possible only from the tool's random, local capability URL.
if (location.pathname === '/greatlibrary/connect' && location.hostname === '127.0.0.1') {
  const token = location.hash.slice(1);
  if (/^[a-f0-9]{64}$/.test(token) && location.port) {
    chrome.runtime.sendMessage({type: 'connect', port: Number(location.port), token}, response => {
      const result = document.getElementById('status');
      if (result) result.textContent = chrome.runtime.lastError?.message || response?.error ||
        '已连接。任务使用独立普通窗口，不抢占其他窗口焦点；沿用当前 Edge 登录状态。';
    });
  }
}
