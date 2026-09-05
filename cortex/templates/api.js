export function getToken() {
    return localStorage.getItem('awsl_api_token') || '';
}

export async function api(method, path, { body, signal, timeout = 30000 } = {}) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(abort, timeout);
    try {
        const headers = {};
        const token = getToken();
        if (token) headers.Authorization = `Bearer ${token}`;
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        const response = await fetch(path, {
            method,
            headers,
            signal: controller.signal,
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        const text = await response.text();
        let data = null;
        try {
            data = text ? JSON.parse(text) : null;
        } catch {
            throw new Error(`服务器返回了无效数据 (${response.status})`);
        }
        if (!response.ok) {
            const detail = data?.detail || data?.message;
            const message =
                response.status === 401
                    ? '认证失败，请检查 Token'
                    : typeof detail === 'string'
                      ? detail
                      : Array.isArray(detail)
                        ? detail.map((item) => item.msg || '参数错误').join('；')
                        : `请求失败 (${response.status})`;
            const error = new Error(message);
            error.status = response.status;
            throw error;
        }
        return data;
    } catch (error) {
        if (signal?.aborted) throw new DOMException('请求已取消', 'AbortError');
        if (controller.signal.aborted) throw new Error('请求超时，请重试');
        if (error instanceof TypeError) throw new Error('网络连接失败，请重试');
        throw error;
    } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
    }
}

export const groupName = (group) =>
    group?.name ||
    group?.group_name ||
    group?.display_name ||
    group?.remark ||
    group?.nick_name ||
    group?.username ||
    '';
export const initial = (name) => (Array.from(String(name || '').trim())[0] || '?').toUpperCase();
export const formatTime = (time) =>
    time ? new Date(time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—';
export function formatFileSize(bytes) {
    if (!bytes || bytes < 0) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return `${i ? bytes.toFixed(1) : bytes} ${units[i]}`;
}
export function safeUrl(value) {
    if (!value) return '';
    try {
        const url = new URL(value, location.href);
        return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
    } catch {
        return '';
    }
}
