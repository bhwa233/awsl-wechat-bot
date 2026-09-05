import { h, createContext, cloneElement } from 'preact';
import { useContext, useEffect, useLayoutEffect, useRef, useState, useMemo, useCallback, useId } from 'preact/hooks';
export { h, useContext, useEffect, useLayoutEffect, useRef, useState, useMemo, useCallback };
export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

export function Button({
    children,
    variant = 'ghost',
    busy = false,
    disabled,
    quietDisabled = false,
    class: className = '',
    ...props
}) {
    return h(
        'button',
        {
            type: 'button',
            ...props,
            disabled: disabled || busy,
            'data-quiet-disabled': quietDisabled && !busy,
            class: `btn btn-${variant} ${busy ? 'loading' : ''} ${className}`,
            'aria-busy': busy,
        },
        children
    );
}
export function Empty({ children, icon = '◇' }) {
    return h(
        'div',
        { class: 'chat-empty' },
        h('div', { class: 'chat-empty-icon', 'aria-hidden': true }, icon),
        h('p', null, children)
    );
}
export function Feedback({ loading, error, retry }) {
    if (error)
        return h(
            'div',
            { class: 'request-error', role: 'alert' },
            h('span', null, error),
            retry && h(Button, { onClick: retry, class: 'btn-sm' }, '重试')
        );
    if (loading)
        return h('div', { class: 'loading-hint', role: 'status' }, h('span', { class: 'loading-dot' }), '加载中…');
    return null;
}
export function Field({ label, help, children }) {
    const id = useId();
    return h(
        'label',
        { class: 'form-group' },
        h('span', { class: 'field-label', id }, label),
        cloneElement(children, { 'aria-labelledby': id }),
        help && h('span', { class: 'help-text' }, help)
    );
}
export function Input({ value = '', onValue, ...props }) {
    return h('input', { ...props, value, onInput: (event) => onValue(event.currentTarget.value) });
}
export function Select({ value, onValue, options, ...props }) {
    return h(
        'select',
        { ...props, value, onChange: (event) => onValue(event.currentTarget.value) },
        options.map(([key, label]) => h('option', { key, value: key }, label))
    );
}
export function EnabledField({ value, onValue }) {
    return h(
        Field,
        { label: '状态' },
        h(Select, {
            value: String(value),
            onValue: (v) => onValue(v === 'true'),
            options: [
                ['true', '启用'],
                ['false', '禁用'],
            ],
        })
    );
}
export function GroupPicker({ names, value = [], onValue }) {
    const all = [...new Set([...names, ...value])];
    if (!all.length) return h('div', { class: 'help-text' }, '暂无在线群组');
    return h(
        'div',
        { class: 'checkbox-group' },
        all.map((name) =>
            h(
                'label',
                { class: 'checkbox-item', key: name },
                h('input', {
                    type: 'checkbox',
                    value: name,
                    checked: value.includes(name),
                    onChange: (event) =>
                        onValue(event.currentTarget.checked ? [...value, name] : value.filter((item) => item !== name)),
                }),
                name
            )
        )
    );
}
export function Badge({ enabled, children }) {
    return h('span', { class: `badge ${enabled ? 'on' : 'off'}` }, children || (enabled ? '已启用' : '已禁用'));
}
export function PageHead({ title, subtitle, children }) {
    return h(
        'div',
        { class: 'section-head' },
        h(
            'div',
            null,
            h('h2', { class: 'section-title' }, title),
            subtitle && h('p', { class: 'section-sub' }, subtitle)
        ),
        h('div', { class: 'section-head-actions' }, children)
    );
}

const modalStack = [];
export function Modal({ title, onClose, children, footer, busy = false, class: className = '', id }) {
    const ref = useRef(null);
    const close = useRef(onClose);
    close.current = busy ? null : onClose;
    useLayoutEffect(() => {
        const previous = document.activeElement;
        const element = ref.current;
        modalStack.push(element);
        const focusables = () =>
            [...element.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')].filter(
                (el) => !el.disabled && el.getClientRects().length
            );
        (focusables().find((el) => el.matches('input, textarea')) || focusables()[0] || element).focus();
        const keydown = (event) => {
            if (modalStack.filter((item) => item.getClientRects().length).at(-1) !== element) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                close.current?.();
            }
            if (event.key !== 'Tab') return;
            const items = focusables();
            if (!items.length) {
                event.preventDefault();
                element.focus();
                return;
            }
            const first = items[0],
                last = items.at(-1);
            if (event.shiftKey && (document.activeElement === first || !element.contains(document.activeElement))) {
                event.preventDefault();
                last.focus();
            } else if (
                !event.shiftKey &&
                (document.activeElement === last || !element.contains(document.activeElement))
            ) {
                event.preventDefault();
                first.focus();
            }
        };
        document.addEventListener('keydown', keydown);
        return () => {
            modalStack.splice(modalStack.indexOf(element), 1);
            document.removeEventListener('keydown', keydown);
            if (previous?.isConnected) previous.focus();
        };
    }, []);
    return h(
        'div',
        {
            class: 'modal-overlay',
            id,
            style: { display: 'flex' },
            onClick: (e) => {
                if (e.target === e.currentTarget) close.current?.();
            },
        },
        h(
            'div',
            {
                class: `modal-content ${className}`,
                role: 'dialog',
                'aria-modal': true,
                'aria-label': title,
                tabIndex: -1,
                ref,
            },
            h(
                'div',
                { class: 'modal-header' },
                h('span', null, title),
                h(Button, { class: 'modal-close', disabled: busy, onClick: onClose, 'aria-label': '关闭' }, '×')
            ),
            h('div', { class: 'modal-body' }, children),
            footer && h('div', { class: 'modal-footer' }, footer)
        )
    );
}

export function useResource(loader, dependencies = [], enabled = true) {
    const [state, setState] = useState({ data: null, loading: true, error: '' });
    const [revision, setRevision] = useState(0);
    const request = useRef(null);
    const latest = useRef(loader);
    latest.current = loader;
    useEffect(() => {
        if (!enabled) return;
        const controller = new AbortController();
        request.current = controller;
        setState((previous) => ({ ...previous, loading: true, error: '' }));
        Promise.resolve()
            .then(() => latest.current(controller.signal))
            .then((data) => {
                if (!controller.signal.aborted) setState({ data, loading: false, error: '' });
            })
            .catch((error) => {
                if (!controller.signal.aborted)
                    setState((previous) => ({ ...previous, loading: false, error: error.message }));
            });
        return () => controller.abort();
    }, [...dependencies, revision, enabled]);
    return {
        ...state,
        reload: useCallback(() => setRevision((n) => n + 1), []),
        update: useCallback((data) => {
            request.current?.abort();
            setState({ data, loading: false, error: '' });
        }, []),
    };
}

export function usePoll(callback, active, delay = 5000) {
    const latest = useRef(callback);
    latest.current = callback;
    useEffect(() => {
        if (!active) return;
        let stopped = false,
            timer;
        const run = async () => {
            if (stopped) return;
            if (document.visibilityState === 'visible') {
                try {
                    await latest.current();
                } catch {}
            }
            if (!stopped) timer = setTimeout(run, delay);
        };
        timer = setTimeout(run, delay);
        return () => {
            stopped = true;
            clearTimeout(timer);
        };
    }, [active, delay]);
}
