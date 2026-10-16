// Pairing is possible only from the tool's random, local capability URL.
if (location.pathname === '/greatlibrary/connect' && location.hostname === '127.0.0.1') {
  const token = location.hash.slice(1);
  if (/^[a-f0-9]{64}$/.test(token) && location.port) {
    chrome.runtime.sendMessage({type: 'connect', port: Number(location.port), token}, response => {
      const result = document.getElementById('status');
      if (result) result.textContent = chrome.runtime.lastError?.message || response?.error ||
        '扩展通道已连接。请返回工具箱等待工作页初始化；任务默认后台运行，沿用当前 Edge 登录状态；需要查看时从任务栏打开工作窗口。';
    });
  }
}
