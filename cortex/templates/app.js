const $ = id => document.getElementById(id);

let authEnabled = false;
let allGroups = [];

function getToken() { return localStorage.getItem('awsl_api_token') || ''; }

function saveToken() {
    const t = $('authToken').value.trim();
    if (!t) return;
    localStorage.setItem('awsl_api_token', t);
    updateAuthUI();
    toast('Token 已保存', 'success');
    if (typeof onTokenSaved === 'function') onTokenSaved();
}

function clearToken() {
    localStorage.removeItem('awsl_api_token');
    $('authToken').value = '';
    updateAuthUI();
}

function updateAuthUI() {
    const section = $('authSection');
    if (!authEnabled) { section.style.display = 'none'; return; }
    section.style.display = '';
    const has = !!getToken();
    section.classList.toggle('authenticated', has);
    $('authStatus').textContent = has ? 'Token 已保存' : '服务器已启用认证，请输入 Token（必填）';
    if (has) $('authToken').value = getToken();
}

async function api(method, path, opts = {}) {
    const headers = {};
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (opts.body) headers['Content-Type'] = 'application/json';
    try {
        const res = await fetch(path, {
            method, headers,
            body: opts.body ? JSON.stringify(opts.body) : undefined,
        });
        if (res.status === 401) return { ok: false, error: '认证失败，请检查 Token', status: 401 };
        if (opts.text) return { ok: res.ok, data: await res.text(), status: res.status };
        const data = await res.json();
        return { ok: res.ok, data, status: res.status };
    } catch (e) {
        return { ok: false, error: '网络错误', status: 0 };
    }
}

function toast(msg, type = 'info') {
    const el = $('toast');
    el.textContent = msg;
    el.className = `toast ${type} show`;
    setTimeout(() => el.classList.remove('show'), 3000);
}

function initTheme() {
    const saved = localStorage.getItem('bot_theme');
    const theme = saved || (matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
    matchMedia('(prefers-color-scheme:dark)').addEventListener('change', e => {
        if (!localStorage.getItem('bot_theme'))
            document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
    });
}

function toggleTheme() {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('bot_theme', next);
}

async function loadGroups() {
    const r = await api('GET', '/api/groups');
    if (r.status === 401) {
        $('statusText').textContent = '认证失败';
        document.querySelector('.status-dot').style.background = 'var(--red)';
        return;
    }
    if (!r.ok) {
        $('statusText').textContent = '连接失败';
        document.querySelector('.status-dot').style.background = 'var(--red)';
        return;
    }
    allGroups = r.data;
    $('statusText').textContent = `运行中 · ${allGroups.length} 个群聊`;
    document.querySelector('.status-dot').style.background = '';
    if (typeof onGroupsLoaded === 'function') onGroupsLoaded();
}

async function loadServerInfo() {
    const r = await api('GET', '/api/health');
    if (!r.ok) return;
    if (r.data.auth_enabled !== undefined) {
        authEnabled = r.data.auth_enabled;
        updateAuthUI();
    }
    return r.data;
}

function switchTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === `${name}-panel`));
    localStorage.setItem('cortex_tab', name);
    if (typeof onTabSwitch === 'function') onTabSwitch(name);
}

function formatTime(iso) {
    const d = new Date(iso);
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function formatDate(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function escapeHtml(s) {
    if (!s) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

async function init() {
    initTheme();
    document.querySelectorAll('.tab').forEach(t => {
        t.addEventListener('click', () => switchTab(t.dataset.tab));
    });
    await loadServerInfo();
    await loadGroups();
    const saved = localStorage.getItem('cortex_tab') || 'chat';
    switchTab(saved);
}

init();
