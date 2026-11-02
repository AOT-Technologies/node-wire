// SPDX-FileCopyrightText: 2026 AOT Technologies
//
// SPDX-License-Identifier: Apache-2.0

// A card and a generic form for every connector the server lists at GET /playground/connectors
// that has no hand-coded panel in app.js. A connector built with `nw gen-all` shows up here
// after a server restart; nothing in the playground changes. It reuses the existing CSS classes,
// the Smart Pipeline layout and the Technical Audit console, so it looks like the other connectors.
// Multitenancy: the tenant header, the config name and the Add config dialog come from app.js
// through the window.nwPlayground hook.
(async () => {
    // Connectors that keep their own panels in app.js / index.html.
    const COVERED = ['fhir_epic', 'fhir_cerner', 'google_drive', 'http_generic', 'slack', 'stripe', 'salesforce'];
    const NAMES = { github: 'GitHub' };
    // GitHub accepts either token kind as a bearer token, so both fill the same secret.
    const TOKEN_TYPES = {
        github: [
            {
                value: 'pat',
                label: 'Personal access token',
                placeholder: 'github_pat_… or ghp_…',
                help: 'Create one at GitHub → Settings → Developer settings → Personal access tokens.',
            },
            {
                value: 'oauth',
                label: 'OAuth access token',
                placeholder: 'gho_…',
                help: 'The access token from your OAuth app or GitHub App authorization (starts with gho_ or ghu_).',
            },
        ],
    };
    const $ = (id) => document.getElementById(id);
    const el = (tag, props = {}, ...kids) => {
        const node = Object.assign(document.createElement(tag), props);
        node.append(...kids);
        return node;
    };
    const nameOf = (id) => NAMES[id] || id.charAt(0).toUpperCase() + id.slice(1).replace(/_/g, ' ');
    const colorOf = (id) => {
        let hash = 0;
        for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
        return `hsl(${hash}, 55%, 42%)`;
    };

    let data;
    try {
        const res = await fetch('/playground/connectors');
        if (!res.ok) return;
        data = await res.json();
    } catch (err) {
        return;
    }
    // app.js sets the hook when the page has loaded; the fetch can finish first.
    if (!window.nwPlayground) await new Promise((resolve) => document.addEventListener('nw:ready', resolve, { once: true }));
    const ids = Object.keys(data).filter((id) => !COVERED.includes(id));
    const grid = document.querySelector('#connectors-list-panel .connector-grid');
    const inputCol = document.querySelector('#playground-view .col-input');
    const stockPipeline = document.querySelector('#col-progress .pipeline-card');
    if (!ids.length || !grid || !inputCol || !stockPipeline) return;

    const terminal = $('log-terminal');
    const log = (message, type = 'system') => {
        if (!terminal) return;
        terminal.append(el('div', { className: `log-entry ${type}`, textContent: `> ${new Date().toLocaleTimeString()} | ${message}` }));
        terminal.scrollTop = terminal.scrollHeight;
    };

    // Our right-hand "Smart Pipeline": one step, same classes as the stock one.
    const stepLabel = el('span', { className: 'node-label' });
    const stepStatus = el('span', { className: 'node-status' });
    const bodyBtn = el('button', { type: 'button', className: 'view-response-btn hidden', textContent: 'Response' });
    const headersBtn = el('button', { type: 'button', className: 'view-response-btn hidden', textContent: 'Headers' });
    const bodyBox = el('div', { className: 'response-json hidden' });
    const headersBox = el('div', { className: 'response-json hidden' });
    const step = el(
        'div',
        { className: 'flow-node pending' },
        el('div', { className: 'node-icon' }, el('div', { className: 'icon-circle' })),
        el(
            'div',
            { className: 'node-content' },
            el('div', { className: 'node-header' }, stepLabel, el('span', { style: 'display: flex; gap: 0.4rem; margin-left: 0.75rem' }, bodyBtn, headersBtn)),
            stepStatus,
            bodyBox,
            headersBox
        )
    );
    const pipeline = el(
        'div',
        { className: 'glass-card pipeline-card hidden' },
        el('h3', { textContent: 'Smart Pipeline' }),
        el('div', { className: 'pipeline-flow' }, step)
    );
    pipeline.dataset.dynamic = 'true';
    stockPipeline.after(pipeline);
    for (const [btn, box] of [[bodyBtn, bodyBox], [headersBtn, headersBox]]) {
        btn.addEventListener('click', () => box.classList.toggle('hidden'));
    }

    function resetStep(action) {
        step.className = 'flow-node pending';
        stepLabel.textContent = action ? `1. ${action}` : '1. Select an action';
        stepStatus.textContent = 'Waiting for input...';
        for (const node of [bodyBtn, headersBtn, bodyBox, headersBox]) node.classList.add('hidden');
        bodyBox.textContent = headersBox.textContent = '';
    }

    const panel = el('div', { className: 'glass-card main-form-card hidden' });
    inputCol.append(panel);

    // A hand-coded card was clicked: app.js shows its own panel, so hide ours and restore the stock one.
    document.addEventListener(
        'click',
        (event) => {
            if (!event.target.closest('.connector-card:not([data-dynamic])')) return;
            panel.classList.add('hidden');
            pipeline.classList.add('hidden');
            stockPipeline.style.display = '';
        },
        true
    );

    function field(name, prop, required) {
        let input;
        if (prop.enum || prop.type === 'boolean') {
            input = el('select', { name }, el('option', { value: '' }));
            for (const value of prop.enum || ['true', 'false']) input.append(el('option', { value: String(value), textContent: String(value) }));
        } else if (prop.type === 'object' || prop.type === 'array') {
            input = el('textarea', { name, placeholder: `JSON ${prop.type}` });
        } else {
            input = el('input', {
                name,
                type: prop.type === 'integer' || prop.type === 'number' ? 'number' : 'text',
                placeholder: prop.type || 'text, number or JSON',
            });
        }
        input.dataset.type = prop.type || '';
        const group = el('div', { className: 'field-group' }, el('label', { textContent: name + (required ? ' *' : '') }), input);
        if (prop.description) group.append(el('span', { className: 'field-help', textContent: prop.description.slice(0, 140) }));
        if (input.tagName === 'TEXTAREA') group.style.gridColumn = '1 / -1';
        return group;
    }

    function fieldsFor(schema) {
        const required = schema.required || [];
        const all = Object.entries(schema.properties || {});
        if (!all.length) return [el('p', { className: 'field-help', textContent: 'This action takes no input.' })];
        const rows = (list, req) => el('div', { className: 'input-row' }, ...list.map(([name, prop]) => field(name, prop, req)));
        const must = all.filter(([name]) => required.includes(name));
        const may = all.filter(([name]) => !required.includes(name));
        const out = [];
        if (must.length) out.push(el('div', { className: 'input-section' }, el('h3', { className: 'section-label', textContent: 'Required' }), rows(must, true)));
        if (may.length) {
            const optional = el('details', { open: !must.length }, el('summary', { className: 'section-label', textContent: `Optional fields (${may.length})` }), rows(may, false));
            out.push(el('div', { className: 'input-section' }, optional));
        }
        return out;
    }

    function render(id) {
        const actions = data[id].actions;
        const groups = [...new Set(actions.map((a) => a.action.split(/[._]/)[0]))];
        // No group means every action, so one search covers the whole connector.
        const groupBox = nwCombobox({ label: 'Target group', options: groups, placeholder: 'All groups', onPick: () => fillActions() });
        const actionBox = nwCombobox({ label: 'Target action', placeholder: 'Search actions…', onPick: () => pickAction() });
        const groupField = groupBox.field;
        const actionField = actionBox.field;
        const fields = el('div');
        const spinner = el('div', { className: 'loading-spinner hidden' });
        const label = el('span', { className: 'btn-lbl', textContent: 'Run Action' });
        const run = el('button', { type: 'submit', className: 'action-btn', disabled: true }, label, spinner);
        groupField.style.display = groups.length > 1 ? '' : 'none';
        let current;

        function fillActions() {
            const group = groups.length > 1 ? groupBox.value : '';
            const options = actions.filter((a) => !group || a.action.split(/[._]/)[0] === group);
            actionBox.setOptions(options.map((a) => ({ value: a.action, badge: a.method })));
            pickAction();
        }
        function pickAction() {
            current = actions.find((a) => a.action === actionBox.value);
            fields.replaceChildren(...(current ? fieldsFor(current.input_schema) : []));
            run.disabled = !current;
            resetStep(current && current.action);
        }
        const form = el(
            'form',
            {},
            el('div', { className: 'input-section', style: 'padding-bottom: 0' }, groupField, el('div', { style: 'height: 1rem' }), actionField),
            fields,
            run
        );
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const body = {};
            for (const input of fields.querySelectorAll('input, select, textarea')) {
                const raw = input.value.trim();
                if (!raw) continue;
                // Simplified: a field not typed "string" is sent as JSON when it parses, else as text.
                if (input.dataset.type === 'string') body[input.name] = raw;
                else {
                    try {
                        body[input.name] = JSON.parse(raw);
                    } catch (err) {
                        body[input.name] = raw;
                    }
                }
            }
            const configName = window.nwPlayground.getConfigName();
            if (configName) body.config_name = configName;
            resetStep(current.action);
            run.disabled = true;
            spinner.classList.remove('hidden');
            label.textContent = 'Orchestrating...';
            step.className = 'flow-node active';
            stepStatus.textContent = 'Processing...';
            log(`Calling ${id}.${current.action}...`);
            try {
                const res = await fetch(`/connectors/${id}/${current.action}`, {
                    method: 'POST',
                    headers: window.nwPlayground.requestHeaders(),
                    body: JSON.stringify(body),
                });
                const json = (await res.json().catch(() => null)) || {};
                const out = json.data || {};
                const ok = json.success === true;
                const payload = 'body' in out ? out.body : ok ? out : json;
                step.className = `flow-node ${ok ? 'success' : 'error'}`;
                stepStatus.textContent = ok
                    ? `HTTP ${out.status_code || res.status}${Array.isArray(payload) ? `, ${payload.length} items` : ''}`
                    : `${json.error_code || 'HTTP ' + res.status}: ${(json.message || 'request failed').split('\n')[0]}`;
                bodyBox.textContent = JSON.stringify(payload, null, 2);
                bodyBtn.classList.remove('hidden');
                if (out.headers) {
                    headersBox.textContent = JSON.stringify(out.headers, null, 2);
                    headersBtn.classList.remove('hidden');
                }
                if (json.trace_id) $('current-trace').textContent = json.trace_id.toUpperCase();
                log(`${current.action}: ${ok ? 'SUCCESS' : 'FAILED'}`, ok ? 'success' : 'error');
            } catch (err) {
                step.className = 'flow-node error';
                stepStatus.textContent = `Request failed: ${err.message}`;
                log(`${current.action}: ${err.message}`, 'error');
            }
            run.disabled = false;
            spinner.classList.add('hidden');
            label.textContent = 'Run Action';
        });

        panel.replaceChildren(
            el('div', { className: 'card-title' }, el('h2', { textContent: nameOf(id) }), el('span', { className: 'info-tip', textContent: `${actions.length} actions` })),
            form
        );
        fillActions();
    }

    function open(id) {
        $('connectors-list-panel').classList.add('hidden');
        $('playground-view').classList.remove('hidden');
        $('back-selection-btn')?.classList.add('hidden');
        $('back-to-connectors').classList.remove('hidden');
        const mode = `dyn:${id}`;
        window.nwPlayground.setMode(mode);
        inputCol.querySelectorAll('.main-form-card').forEach((p) => p.classList.add('hidden'));
        stockPipeline.style.display = 'none';
        pipeline.classList.remove('hidden');
        document.documentElement.style.setProperty('--brand-accent', colorOf(id));
        $('connector-status').textContent = `${nameOf(id)} Online`;
        document.querySelector('.tagline').textContent = 'Connector Orchestration';
        render(id);
        panel.classList.remove('hidden');
        window.nwPlayground.refreshConfigDropdown(mode);
        window.nwPlayground.syncMultitenancyUi();
        log(`System ready [${id.toUpperCase()}]. Awaiting trigger...`);
    }

    for (const id of ids) {
        // Multitenancy: the Add config dialog needs the YAML template and the secret names.
        window.nwPlayground.registerConnector(
            `dyn:${id}`,
            id,
            data[id].config_template,
            data[id].secret_names.map((key) => ({ key, label: key, type: 'text', required: true, tokenTypes: TOKEN_TYPES[id] }))
        );
        const icon = el('div', { className: 'connector-icon', textContent: nameOf(id).slice(0, 2).toUpperCase(), style: `background: ${colorOf(id)}; font-weight: 700` });
        const details = el('div', { className: 'connector-details' }, el('h3', { textContent: nameOf(id) }), el('p', { textContent: `${data[id].actions.length} actions, discovered at runtime.` }));
        const card = el('div', { className: 'connector-card' }, icon, details);
        card.dataset.dynamic = 'true';
        card.addEventListener('click', () => open(id));
        grid.append(card);
    }
})();
