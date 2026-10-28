// Searchable dropdown: type to filter, pick with mouse or keyboard, clear with ×.
// Simplified: a classic script that sets a global, like window.nwPlayground, because the
// playground has no module system. Add other shared components as sibling files that do the same.
//
//   const box = nwCombobox({ label, options, value, placeholder, onPick });
//   options: strings, or { value, badge } (badge is a short tag such as an HTTP method).
//   box.field is the element to mount; box.value is the pick ('' when cleared);
//   box.setOptions(options) swaps the list and clears the pick without calling onPick.
(() => {
    const MAX_SHOWN = 100; // Simplified: render the first matches only; typing narrows the rest.
    let count = 0;

    const el = (tag, props = {}, ...kids) => {
        const node = Object.assign(document.createElement(tag), props);
        node.append(...kids);
        return node;
    };
    const asOption = (o) => (typeof o === 'string' ? { value: o } : o);
    const badgeEl = (badge) => el('span', { className: `nw-method nw-method-${badge.toLowerCase()}`, textContent: badge });

    function highlight(text, tokens) {
        if (!tokens.length) return [text];
        const pattern = tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
        // A split with one capture group puts the matches at the odd positions.
        return text.split(new RegExp(`(${pattern})`, 'gi')).map((part, i) => (i % 2 ? el('mark', { textContent: part }) : part));
    }

    window.nwCombobox = function ({ label, options = [], value = '', placeholder = 'Search…', onPick = () => {} }) {
        const uid = `nw-combobox-${++count}`;
        let items = options.map(asOption);
        let selected = value;
        let shown = [];
        let active = -1;

        const input = el('input', { id: uid, type: 'text', className: 'nw-combobox-input', placeholder, autocomplete: 'off', spellcheck: false });
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', `${uid}-list`);
        const clear = el('button', { type: 'button', className: 'nw-combobox-clear', hidden: true, title: 'Clear', textContent: '×' });
        clear.setAttribute('aria-label', `Clear ${label}`);
        const list = el('ul', { id: `${uid}-list`, className: 'nw-combobox-list', hidden: true });
        list.setAttribute('role', 'listbox');
        const tag = el('span', { className: 'nw-combobox-tag' });
        const box = el('div', { className: 'nw-combobox' }, tag, input, clear, list);
        const field = el('div', { className: 'field-group' }, el('label', { htmlFor: uid, textContent: label }), box);

        const selectedItem = () => items.find((o) => o.value === selected);

        // The pick's badge sits inside the field while it is closed, so a DELETE stays visible.
        function syncSelection() {
            input.value = selected;
            const badge = selectedItem() && selectedItem().badge;
            tag.replaceChildren(...(badge ? [badgeEl(badge)] : []));
            box.classList.toggle('has-badge', Boolean(badge) && list.hidden);
            clear.hidden = !input.value;
        }

        function setActive(index) {
            active = index;
            list.querySelectorAll('[role=option]').forEach((li, i) => li.classList.toggle('active', i === index));
            const li = list.querySelector('.active');
            if (li) {
                input.setAttribute('aria-activedescendant', li.id);
                li.scrollIntoView({ block: 'nearest' });
            } else input.removeAttribute('aria-activedescendant');
        }

        function open(query) {
            const tokens = query.toLowerCase().split(/[\s_.]+/).filter(Boolean);
            const matches = items.filter((o) => tokens.every((t) => o.value.toLowerCase().includes(t)));
            shown = matches.slice(0, MAX_SHOWN);
            const rows = shown.map((o, i) => {
                const li = el('li', { id: `${uid}-${i}` }, ...(o.badge ? [badgeEl(o.badge)] : []), el('span', {}, ...highlight(o.value, tokens)));
                li.setAttribute('role', 'option');
                li.setAttribute('aria-selected', String(o.value === selected));
                // mousedown, not click: a click would blur the input and close the list first.
                li.addEventListener('mousedown', (event) => {
                    event.preventDefault();
                    pick(o.value);
                });
                li.addEventListener('mousemove', () => active !== i && setActive(i));
                return li;
            });
            const hint = matches.length > shown.length ? `Showing ${shown.length} of ${matches.length.toLocaleString()}. Keep typing to narrow.` : shown.length ? `${matches.length.toLocaleString()} of ${items.length.toLocaleString()}` : 'No matches';
            list.replaceChildren(...rows, el('li', { className: 'nw-combobox-hint', textContent: hint }));
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            box.classList.remove('has-badge');
            setActive(shown.length ? Math.max(0, shown.findIndex((o) => o.value === selected)) : -1);
        }

        function close() {
            list.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            syncSelection(); // Typed text that was never picked reverts to the last pick.
        }

        function pick(next) {
            selected = next;
            close();
            onPick(next);
        }

        input.addEventListener('focus', () => {
            input.select();
            open('');
        });
        input.addEventListener('click', () => list.hidden && open(''));
        input.addEventListener('input', () => {
            clear.hidden = !input.value;
            open(input.value);
        });
        input.addEventListener('blur', close);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (list.hidden) return open('');
                if (!shown.length) return;
                setActive((active + (event.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length);
            } else if (event.key === 'Enter') {
                event.preventDefault(); // Never submit the surrounding form from here.
                if (!list.hidden && active >= 0) pick(shown[active].value);
            } else if (event.key === 'Escape' && !list.hidden) {
                event.preventDefault();
                close();
            }
        });
        clear.addEventListener('mousedown', (event) => {
            event.preventDefault();
            selected = '';
            syncSelection();
            onPick('');
            input.focus();
            open('');
        });

        syncSelection();
        return {
            field,
            get value() {
                return selected;
            },
            setOptions(next) {
                items = next.map(asOption);
                selected = '';
                syncSelection();
            },
        };
    };
})();
