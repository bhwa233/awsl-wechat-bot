// ============================================================================
// app.js — 全局运行时：路由、主题、鉴权、API、状态、概览仪表盘
// ============================================================================

const $ = id => document.getElementById(id);

let authEnabled = false;
let allGroups = [];
let gatewayCapabilities = { voice: false };
const groupsLoadedHandlers = [];
let chatlogBackend = null; // 由 chat.js 维护，app 里只作为全局状态声明

// ---------------------------------------------------------------- Token / API
function getToken() { return localStorage.getItem('awsl_api_token') || ''; }

function openTokenModal() {
    $('tokenInput').value = getToken();
    updateAuthUI();
    $('tokenModal').style.display = 'flex';
    setTimeout(() => $('tokenInput').focus(), 30);
}
function closeTokenModal() { $('tokenModal').style.display = 'none'; }

function saveToken() {
    const t = $('tokenInput').value.trim();
    if (!t) { toast('请输入 Token', 'error'); return; }
    localStorage.setItem('awsl_api_token', t);
    closeTokenModal();
    updateAuthUI();
    toast('Token 已保存', 'success');
    onTokenSaved();
}

function clearToken() {
    localStorage.removeItem('awsl_api_token');
    $('tokenInput').value = '';
    updateAuthUI();
    closeTokenModal();
    toast('Token 已清除', 'info');
    onTokenSaved();
}

function onTokenSaved() {
    loadGroups();
    loadGatewayCapabilities();
    chatlogBackend = null;
    if (typeof resetChatlogCaches === 'function') resetChatlogCaches();
    if (typeof detectChatlogBackend === 'function') detectChatlogBackend();
}

function updateAuthUI() {
    const btn = $('tokenBtn');
    const needs = authEnabled && !getToken();
    if (btn) btn.classList.toggle('needs-token', needs);
    if ($('tokenHint')) {
        $('tokenHint').textContent = authEnabled
            ? (getToken() ? '已保存 Token，如需更换请重新输入。' : '服务器已启用认证，请输入 Bearer Token。')
            : '当前服务器未启用认证，Token 可留空。';
    }
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

// ---------------------------------------------------------------- Toast / theme
function toast(msg, type = 'info') {
    const el = $('toast');
    el.textContent = msg;
    el.className = `toast ${type} show`;
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('show'), 3000);
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

// ---------------------------------------------------------------- Groups / status
function setStatus(text, off = false) {
    const dot = $('statusDot');
    const el = $('statusText');
    if (el) el.textContent = text;
    if (dot) dot.classList.toggle('off', off);
}

async function loadGroups() {
    const r = await api('GET', '/api/groups');
    if (r.status === 401) { setStatus('认证失败', true); return; }
    if (!r.ok) { setStatus('连接失败', true); return; }
    allGroups = Array.isArray(r.data) ? r.data : (r.data?.groups || []);
    const active = allGroups.filter(g => g.active).length;
    setStatus(`运行中 · ${active} 群聊在线`);
    for (const handler of groupsLoadedHandlers) {
        try { handler(allGroups); } catch {}
    }
}

function registerGroupsLoadedHandler(handler) {
    if (typeof handler !== 'function') return;
    groupsLoadedHandlers.push(handler);
    if (allGroups.length) handler(allGroups);
}

function groupDisplayName(group) {
    return group?.name || group?.group_name || group?.display_name || group?.nick_name || group?.username || '';
}

async function loadServerInfo() {
    const r = await api('GET', '/api/health');
    if (!r.ok) return null;
    if (r.data.auth_enabled !== undefined) {
        authEnabled = r.data.auth_enabled;
        updateAuthUI();
    }
    return r.data;
}

async function loadGatewayCapabilities() {
    const r = await api('GET', '/api/gateway/health');
    gatewayCapabilities.voice = !!(r.ok && r.data?.capabilities?.voice);
    document.querySelectorAll('select option[value="voice"], select option[value="voice_file"]').forEach(option => {
        option.disabled = !gatewayCapabilities.voice;
        option.hidden = !gatewayCapabilities.voice;
    });
    document.querySelectorAll('.composer-type-option[data-value="voice"], .composer-type-option[data-value="voice_file"]').forEach(button => {
        button.hidden = !gatewayCapabilities.voice;
    });
}

// ---------------------------------------------------------------- Drawer
function closeDrawer() {
    $('app')?.classList.remove('drawer-open');
}

// ---------------------------------------------------------------- Router
function activateSection(name) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.section === name));
    document.querySelectorAll('.section').forEach(s => s.classList.toggle('active', s.id === `section-${name}`));
    localStorage.setItem('cortex_section', name);
}

function navigate(name) {
    activateSection(name);

    if (name === 'dashboard') loadDashboard();
    else if (name === 'chat') { if (typeof onChatEnter === 'function') onChatEnter(); }
    else if (name === 'console') { if (typeof consoleTab !== 'undefined') consoleTab.activate(); }
    else if (name === 'tasks') { if (typeof taskCrud !== 'undefined') { taskCrud.load(); if (typeof loadTaskServerInfo === 'function') loadTaskServerInfo(); } }
    else if (name === 'remote-commands') { if (typeof remoteCommandPolicy !== 'undefined') remoteCommandPolicy.load(); }
    else if (name === 'custom-commands') { if (typeof customCommandCrud !== 'undefined') customCommandCrud.load(); }
    else if (name === 'webhooks') { if (typeof webhookCrud !== 'undefined') webhookCrud.load(); }
}

// ---------------------------------------------------------------- Dashboard
function setHealth(row, ok, value) {
    if (!row) return;
    row.classList.toggle('ok', ok);
    row.classList.toggle('bad', !ok);
    const val = row.querySelector('[data-role="value"]');
    if (val) val.textContent = value;
}

async function loadDashboard() {
    const [cortex, gateway, chatlog, groups, chatlogGroups, tasks, webhooks, commands, remote] = await Promise.all([
        api('GET', '/api/health'),
        api('GET', '/api/gateway/health'),
        api('GET', '/api/chatlog/health'),
        api('GET', '/api/groups'),
        api('GET', '/api/chatlog/groups'),
        api('GET', '/api/tasks'),
        api('GET', '/api/webhooks'),
        api('GET', '/api/custom-commands'),
        api('GET', '/api/remote-commands/policy'),
    ]);

    // 服务状态
    setHealth($('healthCortex'), cortex.ok, cortex.ok ? '运行中' : '不可用');
    setHealth($('healthGateway'), gateway.ok, gateway.ok
        ? `运行中${gateway.data?.capabilities?.voice ? ' · 语音' : ''}`
        : '不可用');
    setHealth($('healthChatlog'), chatlog.ok, chatlog.ok ? `运行中 · ${chatlog.data?.backend || 'v1'}` : '不可用');

    // 时间
    if (cortex.ok && cortex.data?.server_time) {
        const t = new Date(cortex.data.server_time).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
        $('dashTime').textContent = `服务器时间 ${t}`;
    }

    // 数据概览
    const groupsArr = Array.isArray(groups.data) ? groups.data : (groups.data?.groups || []);
    const active = groupsArr.filter(g => g.active).length;
    setStat('statGroups', active);
    setStat('statChatlogGroups', Array.isArray(chatlogGroups.data) ? chatlogGroups.data.length : null);
    setStat('statTasks', Array.isArray(tasks.data) ? tasks.data.length : null);
    setStat('statWebhooks', Array.isArray(webhooks.data) ? webhooks.data.length : null);
    setStat('statCommands', Array.isArray(commands.data) ? commands.data.length : null);
    setStat('statRemoteCommands', remote.ok ? (remote.data?.available_commands?.length ?? null) : null);
}

function setStat(id, value) {
    const el = $(id);
    if (!el) return;
    el.textContent = value === null || value === undefined ? '—' : String(value);
}

// ---------------------------------------------------------------- Format helpers
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
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ---------------------------------------------------------------- Init
async function init() {
    initTheme();

    document.querySelectorAll('.nav-item').forEach(n => {
        n.addEventListener('click', () => { navigate(n.dataset.section); closeDrawer(); });
    });
    document.querySelectorAll('[data-goto]').forEach(b => {
        b.addEventListener('click', () => { navigate(b.dataset.goto); closeDrawer(); });
    });

    // 桌面端：标题栏缩放按钮切换大/小侧边栏
    if (localStorage.getItem('sidebar_collapsed') === '1') $('app').classList.add('sidebar-collapsed');
    $('sidebarToggle').addEventListener('click', () => {
        const app = $('app');
        app.classList.toggle('sidebar-collapsed');
        localStorage.setItem('sidebar_collapsed', app.classList.contains('sidebar-collapsed') ? '1' : '0');
    });

    // 移动端：悬浮按钮开/关抽屉
    $('menuToggle').addEventListener('click', () => $('app').classList.toggle('drawer-open'));
    $('drawerBackdrop').addEventListener('click', closeDrawer);

    $('themeToggle').addEventListener('click', toggleTheme);
    $('tokenBtn').addEventListener('click', openTokenModal);
    $('tokenModal').addEventListener('click', e => { if (e.target === $('tokenModal')) closeTokenModal(); });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') { closeTokenModal(); }
    });

    // 先只切换分区外壳（此时 chat/tasks 等模块还没加载，不能触发它们的接口）
    const saved = localStorage.getItem('cortex_section') || 'dashboard';
    activateSection(saved);

    // 等全局数据 + 各模块脚本都就绪后，再触发当前分区的数据加载
    await loadServerInfo();
    await loadGatewayCapabilities();
    await loadGroups();
    navigate(saved);
}

init();
