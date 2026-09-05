import { useEffect, useRef, useState, useMemo, useCallback, useResource, usePoll } from './ui.js';
import { api } from './api.js';

export function useChatlog() {
    const health = useResource((signal) => api('GET', '/api/chatlog/health', { signal }), []);
    const groups = useResource(async (signal) => {
        const data = await api('GET', '/api/chatlog/groups', { signal });
        return Array.isArray(data) ? data : [];
    }, []);
    const data = useMemo(() => ({ health: health.data || {}, groups: groups.data }), [health.data, groups.data]);
    const reload = useCallback(() => {
        health.reload();
        groups.reload();
    }, [health.reload, groups.reload]);
    return {
        data,
        loading: health.loading || groups.loading,
        error: [health.error, groups.error].filter(Boolean).join('；'),
        reload,
    };
}

export function useMessages(group, page, active, revision = 0) {
    const [state, setState] = useState({
        messages: [],
        contacts: {},
        members: [],
        loading: false,
        error: '',
        hasMore: false,
        mediaRevision: 0,
    });
    const request = useRef(null);
    const generation = useRef(0);
    const cache = useRef({ contacts: {}, members: new Map() });
    const context = useRef('');
    const latest = useRef(null);
    const [refresh, setRefresh] = useState(0);
    const reload = () => setRefresh((n) => n + 1);
    async function load(background = false) {
        if (!group) return;
        if (background && request.current) return;
        request.current?.abort();
        const controller = new AbortController();
        request.current = controller;
        const token = ++generation.current;
        const identity = `${group}:${page}`;
        const changed = context.current !== identity;
        context.current = identity;
        setState((previous) => ({
            ...previous,
            ...(changed ? { messages: [], members: [], hasMore: false } : {}),
            loading: !background,
            error: '',
        }));
        const valid = () => generation.current === token && !controller.signal.aborted;
        try {
            const params = new URLSearchParams({ group, limit: '101', offset: String(page * 100), desc: 'true' });
            const data = await api('GET', `/api/chatlog/messages?${params}`, { signal: controller.signal });
            if (!valid()) return;
            const messages = data.slice(0, 100).reverse();
            setState((previous) => ({
                ...previous,
                messages,
                loading: false,
                hasMore: data.length > 100,
                mediaRevision: previous.mediaRevision + (background ? 0 : 1),
                contacts: { ...cache.current.contacts },
                members: cache.current.members.get(group) || [],
            }));
            const senders = [
                ...new Set(
                    messages
                        .filter((m) => !m.is_self && m.sender && !(m.sender in cache.current.contacts))
                        .map((m) => m.sender)
                ),
            ];
            const jobs = [];
            for (let i = 0; i < senders.length; i += 200) {
                const chunk = senders.slice(i, i + 200);
                jobs.push(
                    api('GET', `/api/chatlog/contacts?${new URLSearchParams({ usernames: chunk.join(',') })}`, {
                        signal: controller.signal,
                    }).then((contacts) => {
                        if (!valid()) return;
                        chunk.forEach((name) => {
                            cache.current.contacts[name] = null;
                        });
                        contacts.forEach((contact) => {
                            cache.current.contacts[contact.username] = contact;
                        });
                    })
                );
            }
            if (group.endsWith('@chatroom') && !cache.current.members.has(group)) {
                jobs.push(
                    api('GET', `/api/chatlog/members?group=${encodeURIComponent(group)}`, {
                        signal: controller.signal,
                    }).then((members) => {
                        if (valid()) cache.current.members.set(group, members);
                    })
                );
            }
            await Promise.allSettled(jobs);
            if (valid())
                setState((previous) => ({
                    ...previous,
                    contacts: { ...cache.current.contacts },
                    members: cache.current.members.get(group) || [],
                }));
        } catch (error) {
            if (valid()) setState((previous) => ({ ...previous, loading: false, error: error.message }));
        } finally {
            if (request.current === controller) request.current = null;
        }
    }
    latest.current = load;
    useEffect(() => {
        if (group && active) load();
        else if (!group)
            setState({
                messages: [],
                contacts: {},
                members: [],
                loading: false,
                error: '',
                hasMore: false,
                mediaRevision: 0,
            });
        return () => {
            generation.current++;
            request.current?.abort();
            request.current = null;
        };
    }, [group, page, active, revision, refresh]);
    usePoll(() => latest.current(true), active && !!group && page === 0);
    return {
        ...state,
        ...(context.current !== `${group}:${page}`
            ? { messages: [], members: [], hasMore: false, loading: !!group, error: '' }
            : {}),
        reload,
    };
}
