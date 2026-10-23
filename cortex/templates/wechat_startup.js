import { h, useState, useEffect, useResource, usePoll, Button, Feedback } from './ui.js';
import { api } from './api.js';

const labels = {
    disabled: '自动启动已关闭，可手动启动',
    starting: '正在启动微信…',
    logging_in: '正在登录微信…',
    restoring: '正在恢复独立窗口…',
    ready: '微信已就绪',
    restarting: '微信已退出，正在恢复…',
    waiting: '等待微信桌面或界面就绪…',
    restart_limited: '恢复次数已达上限，看护已停止',
    qr_required: '需要扫码，自动操作已停止',
    locked: '微信已锁定，请先解锁',
    timeout: '登录超时，自动操作已停止',
    failed: '启动失败',
    unsupported: '当前平台不支持启动微信',
};

export function WeChatStartup({ active, onReady }) {
    const resource = useResource((signal) => api('GET', '/api/console/wechat-startup', { signal }), [], active);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const status = resource.data;
    const pending = ['starting', 'logging_in', 'restoring', 'restarting', 'waiting'].includes(status?.state);
    usePoll(resource.reload, active, pending ? 2000 : 10000);
    useEffect(() => {
        if (status?.state === 'ready') onReady?.();
    }, [status?.state]);
    async function start() {
        if (busy || pending) return;
        setBusy(true);
        setError('');
        try {
            resource.update(await api('POST', '/api/console/wechat-startup'));
        } catch (e) {
            setError(e.message);
        } finally {
            setBusy(false);
        }
    }
    return h('div', { class: 'card dash-startup', id: 'wechat-startup' },
        h('div', { class: 'startup-content' },
            h('div', { class: 'startup-heading' },
                h('h3', null, '微信启动'),
                h('span', { role: 'status', class: `startup-status ${status?.state || ''}` },
                    status?.supported === false ? labels.unsupported : labels[status?.state] || '正在读取微信状态…')),
            h('p', { class: 'startup-description' }, '登录后持续看护并恢复独立窗口，主窗口和独立窗口全部最大化；遇到二维码时停止，不会主动关闭微信。'),
            status?.message && h('p', { class: 'startup-message' }, status.message),
            h(Feedback, { error: error || resource.error, retry: resource.reload })),
        h(Button, { variant: 'green', busy: busy || pending, disabled: !status?.supported, onClick: start }, '启动微信'));
}
