// SPDX-FileCopyrightText: 2026 AOT Technologies
//
// SPDX-License-Identifier: Apache-2.0

document.addEventListener('DOMContentLoaded', () => {
    const escapeHTML = (str) => {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    const form = document.getElementById('scenario-form');
    const runBtn = document.getElementById('run-btn');
    const spinner = runBtn.querySelector('.loading-spinner');
    const btnText = runBtn.querySelector('.btn-lbl');
    const terminal = document.getElementById('log-terminal');
    const traceDisplay = document.getElementById('current-trace');
    const finalResult = document.getElementById('final-result');
    const summaryText = document.getElementById('human-summary');
    const resultTag = document.getElementById('result-id');

    // UI Elements for Dynamic Updates
    const itopsForm = document.getElementById('itops-form');
    const itopsRunBtn = document.getElementById('itops-run-btn');
    const itopsSpinner = itopsRunBtn.querySelector('.loading-spinner');
    const itopsBtnText = itopsRunBtn.querySelector('.btn-lbl');
    const cernerForm = document.getElementById('cerner-form');
    const cernerRunBtn = document.getElementById('cerner-run-btn');
    const cernerSpinner = cernerRunBtn.querySelector('.loading-spinner');
    const cernerBtnText = cernerRunBtn.querySelector('.btn-lbl');
    const ehrPanel = document.getElementById('ehr-panel');
    const itopsPanel = document.getElementById('itops-panel');
    const cernerPanel = document.getElementById('cerner-panel');
    const gdriveForm = document.getElementById('gdrive-form');
    const gdriveRunBtn = document.getElementById('gdrive-run-btn');
    const gdriveSpinner = gdriveRunBtn.querySelector('.loading-spinner');
    const gdriveBtnText = gdriveRunBtn.querySelector('.btn-lbl');
    const gdrivePanel = document.getElementById('gdrive-panel');
    const gdriveSubTabs = document.querySelectorAll('.sub-tab');
    const gdriveNoteSection = document.getElementById('gdrive-note-section');
    const gdriveFileSection = document.getElementById('gdrive-file-section');
    const gdriveDocNameGroup = document.getElementById('gdrive-doc-name-group');
    const gdriveFileInput = document.getElementById('gdrive-file');
    const gdriveActionSelect = document.getElementById('gdrive-action-select');
    const gdriveUploadOnly = document.getElementById('gdrive-upload-only');
    const gdriveListOnly = document.getElementById('gdrive-list-only');
    const gdriveSubNav = document.getElementById('gdrive-sub-nav');
    const slackForm = document.getElementById('slack-form');
    const slackRunBtn = document.getElementById('slack-run-btn');
    const slackSpinner = slackRunBtn?.querySelector('.loading-spinner');
    const slackBtnText = slackRunBtn?.querySelector('.btn-lbl');
    const slackPanel = document.getElementById('slack-panel');
    const slackActionSelect = document.getElementById('slack-action-select');
    const slackMessageSection = document.getElementById('slack-message-section');
    const slackFileSection = document.getElementById('slack-file-section');
    const slackFileInput = document.getElementById('slack-file');
    const slackFileDropZone = document.getElementById('slack-file-drop-zone');
    const slackFileChosenPreview = document.getElementById('slack-file-chosen-preview');
    const slackPreviewName = slackFileChosenPreview?.querySelector('.preview-name');
    const slackRemoveFileBtn = slackFileChosenPreview?.querySelector('.remove-file-btn');
    const fileDropZone = document.getElementById('file-drop-zone');
    const fileChosenPreview = document.getElementById('file-chosen-preview');
    const previewName = fileChosenPreview?.querySelector('.preview-name');
    const removeFileBtn = fileChosenPreview?.querySelector('.remove-file-btn');

    const stripeForm = document.getElementById('stripe-form');
    const stripeRunBtn = document.getElementById('stripe-run-btn');
    const stripeSpinner = stripeRunBtn.querySelector('.loading-spinner');
    const stripeBtnText = stripeRunBtn.querySelector('.btn-lbl');
    const stripePanel = document.getElementById('stripe-panel');

    const stripeActionSelect = document.getElementById('stripe-action-select');
    const stripeSections = {
        charge: document.getElementById('stripe-section-charge'),
        payment_intent: document.getElementById('stripe-section-pi'),
        subscription: document.getElementById('stripe-section-sub'),
        cancel_subscription: document.getElementById('stripe-section-cancel'),
        refund: document.getElementById('stripe-section-refund')
    };
    
    const salesforceForm = document.getElementById('salesforce-form');
    const salesforceRunBtn = document.getElementById('salesforce-run-btn');
    const salesforceSpinner = salesforceRunBtn.querySelector('.loading-spinner');
    const salesforceBtnText = salesforceRunBtn.querySelector('.btn-lbl');
    const salesforcePanel = document.getElementById('salesforce-panel');
    const salesforceActionSelect = document.getElementById('salesforce-action-select');
    const salesforceSections = {
        create_lead: document.getElementById('salesforce-section-lead'),
        update_lead: document.getElementById('salesforce-section-lead'),
        create_contact: document.getElementById('salesforce-section-contact'),
        update_contact: document.getElementById('salesforce-section-contact'),
        read_lead: document.getElementById('salesforce-section-id-only'),
        delete_lead: document.getElementById('salesforce-section-id-only'),
        read_contact: document.getElementById('salesforce-section-id-only'),
        delete_contact: document.getElementById('salesforce-section-id-only')
    };

    // External Patient Viewer
    const extViewerForm = document.getElementById('ext-viewer-form');
    const extViewerRunBtn = document.getElementById('ext-viewer-run-btn');
    const extViewerSpinner = extViewerRunBtn ? extViewerRunBtn.querySelector('.loading-spinner') : null;
    const extViewerBtnText = extViewerRunBtn ? extViewerRunBtn.querySelector('.btn-lbl') : null;
    const extViewerPanel = document.getElementById('ext-patient-viewer-panel');

    let currentSubMode = 'file';
    let currentStripeSubMode = 'charge';
    let currentSalesforceSubMode = 'create_lead';
    const connectorStatus = document.getElementById('connector-status');

    const brandLabel = document.querySelector('.brand-text h1 span.accent');
    const tagline = document.querySelector('.tagline');
    const layoutMain = document.querySelector('.layout-main');
    const colProgress = document.getElementById('col-progress');

    // Agent Chat Elements
    const agentPanel = document.getElementById('agent-panel');
    const agentChatHistory = document.getElementById('agent-chat-history');
    const agentInput = document.getElementById('agent-input');
    const agentSendBtn = document.getElementById('agent-send-btn');
    const agentTyping = document.getElementById('agent-typing');
    const agentTransportStatus = document.getElementById('agent-transport-status');
    let agentConversationHistory = [];
    let agentBusy = false;
    let agentTransportMode = 'stdio';

    const pipelineLabels = {
        ehr: [
            "Identify Patient",
            "Locate Medical Visit",
            "Secure Sync to EHR",
            "Verify EHR Update"
        ],
        itops: [
            "Format Incident Payload",
            "Dispatch Webhook",
            "Verify Ticket Creation",
            "Update Audit Log"
        ],
        cerner: [
            "Identify Patient",
            "Locate Medical Visit",
            "Secure Sync to Cerner",
            "Verify EHR Update"
        ],
        gdrive: [
            "Format Archival Metadata",
            "Upload to Secure Vault",
            "Establish Data Access",
            "Verify Integrity"
        ],
        gdriveList: [
            "List Drive Files"
        ],
        gdriveGet: [
            "Get file metadata"
        ],
        gdriveUpdate: [
            "Prepare update request",
            "Apply file update",
            "Verify file metadata",
            "Complete update"
        ],
        slack: [
            "Format Slack Payload",
            "Dispatch to Slack API",
            "Verify Acknowledgment",
            "Update Audit Trail",
        ],
        stripe_charge: [
            "Initialize Payment",
            "Process Charge",
            "Verify Transaction",
        ],
        stripe_payment_intent: [
            "Initialize Session",
            "Create Payment Intent",
            "Verify Allocation",
        ],
        stripe_subscription: [
            "Validate Customer",
            "Create Subscription",
            "Verify Provisioning",
        ],
        stripe_cancel_subscription: [
            "Locate Resource",
            "Cancel Subscription",
            "Verify Termination",
        ],
        stripe_refund: [
            "Validate Charge",
            "Process Refund",
            "Verify Refund"
        ],
        salesforce_create_lead: [
            "Initialize CRM Sync",
            "Create Lead Record",
            "Verify Lead Status"
        ],
        salesforce_create_contact: [
            "Initialize CRM Sync",
            "Create Contact Record",
            "Verify Contact Status"
        ],
        salesforce_read: [
            "Authenticate CRM",
            "Fetch Record Metadata",
            "Verify Data Integrity"
        ],
        salesforce_update: [
            "Authenticate CRM",
            "Apply Partial Update",
            "Verify State Change"
        ],
        salesforce_delete: [
            "Authenticate CRM",
            "Execute Soft Delete",
            "Verify Termination"
        ],
        ext_patient_viewer: [
            "Resolve Patient Identity",
            "Retrieve Encounter History",
            "Retrieve Document Metadata",
            "Assemble External Chart View"
        ],
    };

    const nodes = [
        document.getElementById('step-0'),
        document.getElementById('step-1'),
        document.getElementById('step-2'),
        document.getElementById('step-3')
    ];

    function log(message, type = 'system') {
        if (!terminal) return;
        const line = document.createElement('div');
        line.className = `log-entry ${type}`;
        line.textContent = `> ${new Date().toLocaleTimeString()} | ${message}`;
        terminal.appendChild(line);
        terminal.scrollTop = terminal.scrollHeight;
    }

    function resetUI(pipelineLabelOverride = null) {
        const baseLabels = pipelineLabels[currentMode] || pipelineLabels.ehr;
        const useOverride = Array.isArray(pipelineLabelOverride) && pipelineLabelOverride.length > 0;
        const rowLabels = useOverride ? pipelineLabelOverride : baseLabels;
        const visibleCount = rowLabels.length;

        nodes.forEach((node, i) => {
            if (!node) return;

            const btn = node.querySelector('.view-response-btn');
            const res = node.querySelector('.response-json');
            const beautifulRes = node.querySelector('.beautiful-response');
            const clearPanels = () => {
                if (btn) btn.classList.add('hidden');
                if (res) {
                    res.classList.add('hidden');
                    res.textContent = '';
                }
                if (beautifulRes) {
                    beautifulRes.classList.add('hidden');
                    beautifulRes.innerHTML = '';
                }
            };

            if (useOverride && i >= visibleCount) {
                node.className = 'flow-node pending hidden';
                clearPanels();
                return;
            }

            node.classList.remove('hidden');
            node.className = 'flow-node pending';

            const label = node.querySelector('.node-label');
            if (label) label.textContent = `${i + 1}. ${rowLabels[i]}`;

            const nodeStatus = node.querySelector('.node-status');
            if (nodeStatus) nodeStatus.textContent = i === 0 ? 'Waiting for input...' : 'Awaiting previous step...';

            clearPanels();
        });
        if (finalResult) finalResult.classList.add('hidden');
        if (terminal) terminal.innerHTML = `<div class="log-entry system">System ready [${currentMode.toUpperCase()}]. Awaiting trigger...</div>`;
        if (traceDisplay) traceDisplay.textContent = '...';
    }

    const rootSelectionView = document.getElementById('root-selection-view');
    const selectionCards = document.querySelectorAll('.selection-card, .app-card');
    const rootTabContainer = document.querySelector('.root-tab-container');
    const backToHomeBtn = document.getElementById('back-to-home');

    const rootTabs = document.querySelectorAll('.root-tab');
    const connectorsView = document.getElementById('connectors-view');
    const connectorsListPanel = document.getElementById('connectors-list-panel');
    const playgroundView = document.getElementById('playground-view');
    const backToConnectorsBtn = document.getElementById('back-to-connectors');
    const connectorCards = document.querySelectorAll('.connector-card');

    let currentMode = 'agent'; // Represents the specific playground scenario

    const backSelectionBtn = document.getElementById('back-selection-btn');
    const headerActions = document.getElementById('header-actions');

    // Dashboard Selection Handling
    selectionCards.forEach(card => {
        card.addEventListener('click', () => {
            const view = card.dataset.target;

            if (view === 'agent') {
                rootSelectionView.classList.add('hidden');
                layoutMain.classList.remove('hidden');
                headerActions.classList.remove('hidden');
                agentPanel.classList.remove('hidden');
                connectorsView.classList.add('hidden');
                layoutMain.classList.add('agent-mode');
                connectorStatus.textContent = 'AI Agent Online';
                tagline.textContent = 'Autonomous Healthcare Assistant';
                document.documentElement.style.setProperty('--brand-accent', '#8b5cf6');
                if (backSelectionBtn) {
                    backSelectionBtn.classList.remove('hidden');
                    backSelectionBtn.innerHTML = `
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                        Back to Workspace
                    `;
                }
                log('Switched to AI Agent mode (MCP + LLM)', 'system');
            } else if (view === 'connector-apps-menu') {
                rootSelectionView.classList.add('hidden');
                document.getElementById('connector-apps-selection-view').classList.remove('hidden');
                layoutMain.classList.add('hidden');
                headerActions.classList.remove('hidden');
                connectorStatus.textContent = 'Apps Marketplace';
                tagline.textContent = 'Ready-to-use experiences built on top of connectors';
                document.documentElement.style.setProperty('--brand-accent', '#0d9488');
                if (backSelectionBtn) {
                    backSelectionBtn.classList.remove('hidden');
                    backSelectionBtn.innerHTML = `
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                        Back to Workspace
                    `;
                }
                log('Opened Connector Apps menu', 'system');
            } else if (view === 'ext-patient-viewer') {
                document.getElementById('connector-apps-selection-view').classList.add('hidden');
                rootSelectionView.classList.add('hidden');
                layoutMain.classList.remove('hidden');
                headerActions.classList.remove('hidden');
                agentPanel.classList.add('hidden');
                connectorsView.classList.remove('hidden');
                layoutMain.classList.remove('agent-mode');
                connectorsListPanel.classList.add('hidden');
                playgroundView.classList.remove('hidden');
                if (backToConnectorsBtn) backToConnectorsBtn.classList.add('hidden');
                if (backSelectionBtn) {
                    backSelectionBtn.classList.remove('hidden');
                    backSelectionBtn.innerHTML = `
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                        Back to Apps
                    `;
                }
                setMode('ext-patient-viewer');
            } else {
                rootSelectionView.classList.add('hidden');
                layoutMain.classList.remove('hidden');
                headerActions.classList.remove('hidden');
                agentPanel.classList.add('hidden');
                connectorsView.classList.remove('hidden');
                layoutMain.classList.remove('agent-mode');
                connectorsListPanel.classList.remove('hidden');
                playgroundView.classList.add('hidden');
                connectorStatus.textContent = 'Connectors Ready';
                tagline.textContent = 'Enterprise Integration Suite';
                document.documentElement.style.setProperty('--brand-accent', '#2563eb');
                if (backSelectionBtn) {
                    backSelectionBtn.classList.remove('hidden');
                    backSelectionBtn.innerHTML = `
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                        Back to Workspace
                    `;
                }
                log('Switched to Connectors view', 'system');
            }
        });
    });

    const returnToHome = (e) => {
        if (e) e.preventDefault();
        rootSelectionView.classList.remove('hidden');
        document.getElementById('connector-apps-selection-view').classList.add('hidden');
        layoutMain.classList.add('hidden');
        headerActions.classList.add('hidden');
        tagline.textContent = 'Autonomous Connector Orchestration Platform';
        log('Returned to main selection screen', 'system');
    };

    backToHomeBtn.addEventListener('click', returnToHome);
    
    if (backSelectionBtn) {
        backSelectionBtn.addEventListener('click', (e) => {
            if (e) e.preventDefault();
            const btnText = backSelectionBtn.textContent.trim();
            if (btnText.includes('Back to Apps')) {
                // Return from patient viewer to apps marketplace directory
                layoutMain.classList.add('hidden');
                document.getElementById('connector-apps-selection-view').classList.remove('hidden');
                connectorStatus.textContent = 'Apps Marketplace';
                tagline.textContent = 'Ready-to-use experiences built on top of connectors';
                document.documentElement.style.setProperty('--brand-accent', '#0d9488');
                log('Returned to Apps Marketplace directory', 'system');
            } else {
                // Return from other direct pages to workspace selection home
                returnToHome(e);
            }
        });
    }

    const appsBackBtn = document.getElementById('apps-back-btn');
    if (appsBackBtn) {
        appsBackBtn.addEventListener('click', returnToHome);
    }

    const newChatBtn = document.getElementById('new-chat-btn');
    newChatBtn.addEventListener('click', () => {
        agentConversationHistory = [];
        agentChatHistory.innerHTML = `
            <div class="chat-bubble assistant">
                <div class="bubble-content">
                    <span class="bubble-role">Agent</span>
                    <p>Hello! I'm your AI healthcare assistant. I can help you:</p>
                    <ul style="margin: 0.5rem 0 0 1rem; font-size: 0.9rem; color: var(--text-muted);">
                        <li>Fetch patient records from Cerner or Epic FHIR</li>
                        <li>Upload clinical documents to Google Drive</li>
                        <li>Send patient summaries via email</li>
                    </ul>
                    <p style="margin-top: 0.5rem;">What would you like to do?</p>
                </div>
            </div>
        `;
        log('Agent chat reset', 'system');
    });

    function applyGdriveUploadSubMode() {
        if (!gdriveFileSection) return;
        if (currentSubMode === 'note') {
            if (gdriveNoteSection) gdriveNoteSection.classList.remove('hidden');
            gdriveFileSection.classList.add('hidden');
            if (gdriveDocNameGroup) gdriveDocNameGroup.classList.remove('hidden');
        } else {
            if (gdriveNoteSection) gdriveNoteSection.classList.add('hidden');
            gdriveFileSection.classList.remove('hidden');
            if (gdriveDocNameGroup) gdriveDocNameGroup.classList.add('hidden');
        }
    }

    /** Trust options[selectedIndex] first — some engines lag select.value on early/capture-phase reads. */
    function resolveGdriveActionValue(sel) {
        if (!sel) return '';
        const i = sel.selectedIndex;
        if (i >= 0 && sel.options && sel.options[i]) {
            return String(sel.options[i].value || '').trim();
        }
        return String(sel.value || '').trim();
    }

    function gdrivePipelineLabelOverride() {
        const sel = document.getElementById('gdrive-action-select');
        if (!sel) return null;
        const v = resolveGdriveActionValue(sel);
        if (v === 'files.list') return pipelineLabels.gdriveList;
        if (v === 'files.get') return pipelineLabels.gdriveGet;
        if (v === 'files.update') return pipelineLabels.gdriveUpdate;
        return null;
    }

    function syncGdriveActionForm() {
        const actionSelect = document.getElementById('gdrive-action-select');
        if (!actionSelect) return;
        const uploadRow = document.getElementById('gdrive-upload-only');
        const listSection = document.getElementById('gdrive-list-only');
        const getSection = document.getElementById('gdrive-get-only');
        const updateSection = document.getElementById('gdrive-update-only');
        const fileSection = document.getElementById('gdrive-file-section');
        const subNav = document.getElementById('gdrive-sub-nav');

        const action = resolveGdriveActionValue(actionSelect);
        const isList = action === 'files.list';
        const isGet = action === 'files.get';
        const isUpdate = action === 'files.update';

        if (isList) {
            if (uploadRow) uploadRow.classList.add('hidden');
            if (getSection) getSection.classList.add('hidden');
            if (updateSection) updateSection.classList.add('hidden');
            if (listSection) listSection.classList.remove('hidden');
            if (subNav) subNav.classList.add('hidden');
            if (fileSection) fileSection.classList.add('hidden');
            if (gdriveBtnText) gdriveBtnText.textContent = 'List files';
        } else if (isGet) {
            if (uploadRow) uploadRow.classList.add('hidden');
            if (listSection) listSection.classList.add('hidden');
            if (updateSection) updateSection.classList.add('hidden');
            if (getSection) getSection.classList.remove('hidden');
            if (subNav) subNav.classList.add('hidden');
            if (fileSection) fileSection.classList.add('hidden');
            if (gdriveBtnText) gdriveBtnText.textContent = 'Get file';
        } else if (isUpdate) {
            if (uploadRow) uploadRow.classList.add('hidden');
            if (listSection) listSection.classList.add('hidden');
            if (getSection) getSection.classList.add('hidden');
            if (updateSection) updateSection.classList.remove('hidden');
            if (subNav) subNav.classList.add('hidden');
            if (fileSection) fileSection.classList.add('hidden');
            if (gdriveBtnText) gdriveBtnText.textContent = 'Update file';
        } else {
            if (uploadRow) uploadRow.classList.remove('hidden');
            if (listSection) listSection.classList.add('hidden');
            if (getSection) getSection.classList.add('hidden');
            if (updateSection) updateSection.classList.add('hidden');
            if (subNav) subNav.classList.remove('hidden');
            applyGdriveUploadSubMode();
            if (gdriveBtnText) gdriveBtnText.textContent = 'Encrypt & Archive';
        }
    }

    function stripePipelineLabelOverride() {
        if (currentStripeSubMode === 'charge') return pipelineLabels.stripe_charge;
        if (currentStripeSubMode === 'payment_intent') return pipelineLabels.stripe_payment_intent;
        if (currentStripeSubMode === 'subscription') return pipelineLabels.stripe_subscription;
        if (currentStripeSubMode === 'cancel_subscription') return pipelineLabels.stripe_cancel_subscription;
        if (currentStripeSubMode === 'refund') return pipelineLabels.stripe_refund;
        return pipelineLabels.stripe_charge;
    }

    function salesforcePipelineLabelOverride() {
        if (currentSalesforceSubMode.startsWith('create')) return pipelineLabels.salesforce_create_lead;
        if (currentSalesforceSubMode.startsWith('read')) return pipelineLabels.salesforce_read;
        if (currentSalesforceSubMode.startsWith('update')) return pipelineLabels.salesforce_update;
        if (currentSalesforceSubMode.startsWith('delete')) return pipelineLabels.salesforce_delete;
        return pipelineLabels.salesforce_create_lead;
    }

    function syncSalesforceActionForm() {
        Object.values(salesforceSections).forEach(sec => {
            if (sec) sec.classList.add('hidden');
        });
        const activeSec = salesforceSections[currentSalesforceSubMode] || salesforceSections['create_lead'];
        if (activeSec) activeSec.classList.remove('hidden');
        
        // Handle record ID field visibility in Lead/Contact sections
        const idFields = document.querySelectorAll('#salesforce-form .id-field');
        idFields.forEach(f => {
            if (currentSalesforceSubMode.startsWith('update')) {
                f.classList.remove('hidden');
            } else {
                f.classList.add('hidden');
            }
        });

        // Handle generic ID label for read/delete
        const idLabel = document.getElementById('sf-resource-id-label');
        if (idLabel) {
            if (currentSalesforceSubMode.includes('lead')) {
                idLabel.textContent = 'Lead Record ID';
            } else {
                idLabel.textContent = 'Contact Record ID';
            }
        }

        if (salesforceActionSelect) {
            salesforceActionSelect.value = currentSalesforceSubMode;
        }
    }



    function syncStripeActionForm() {
        Object.values(stripeSections).forEach(sec => {
            if (sec) sec.classList.add('hidden');
        });
        const activeSec = stripeSections[currentStripeSubMode] || stripeSections['charge'];
        if (activeSec) activeSec.classList.remove('hidden');
        
        if (stripeActionSelect) {
            stripeActionSelect.value = currentStripeSubMode;
        }
    }

    function setMode(mode) {
        currentMode = mode;

        if (backToConnectorsBtn) {
            if (mode === 'ext-patient-viewer') {
                backToConnectorsBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                    Back to Workspace
                `;
            } else {
                backToConnectorsBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                    Back to All Connectors
                `;
            }
        }

        // Hide all panels first
        ehrPanel.classList.add('hidden');
        itopsPanel.classList.add('hidden');
        cernerPanel.classList.add('hidden');
        gdrivePanel.classList.add('hidden');
        stripePanel.classList.add('hidden');
        salesforcePanel.classList.add('hidden');
        if (slackPanel) slackPanel.classList.add('hidden');
        if (extViewerPanel) extViewerPanel.classList.add('hidden');

        if (mode === 'ehr') {

            ehrPanel.classList.remove('hidden');
            connectorStatus.textContent = 'Epic R4 Online';
            tagline.textContent = 'Enterprise EHR Orchestration';
            document.documentElement.style.setProperty('--brand-accent', '#2563eb');
            log('Switched to EHR Automation mode (Epic FHIR R4)', 'system');
        } else if (mode === 'itops') {
            itopsPanel.classList.remove('hidden');
            connectorStatus.textContent = 'HTTP Gateway Online';
            tagline.textContent = 'Intelligent Infrastructure Automation';
            document.documentElement.style.setProperty('--brand-accent', '#6366f1');
            log('Switched to IT Ops Automation mode', 'system');
        } else if (mode === 'cerner') {
            cernerPanel.classList.remove('hidden');
            connectorStatus.textContent = 'Cerner R4 Online';
            tagline.textContent = 'Oracle Health Cerner Orchestration';
            document.documentElement.style.setProperty('--brand-accent', '#0ea5e9');
            log('Switched to Cerner FHIR R4 Orchestration mode', 'system');
        } else if (mode === 'gdrive') {
            gdrivePanel.classList.remove('hidden');
            connectorStatus.textContent = 'Google API Online';
            tagline.textContent = 'Secure Vault Orchestration';
            document.documentElement.style.setProperty('--brand-accent', '#10b981');
            log('Switched to Secure Document Archival mode (Google Drive)', 'system');
        } else if (mode === 'stripe') {
            stripePanel.classList.remove('hidden');
            connectorStatus.textContent = 'Stripe Online';
            tagline.textContent = 'Financial Infrastructure';
            document.documentElement.style.setProperty('--brand-accent', '#635bff');
            log('Switched to Stripe Payment Orchestration mode', 'system');
        } else if (mode === 'salesforce') {
            salesforcePanel.classList.remove('hidden');
            connectorStatus.textContent = 'Salesforce Online';
            tagline.textContent = 'CRM Orchestration';
            document.documentElement.style.setProperty('--brand-accent', '#00A1E0');
            log('Switched to Salesforce CRM Orchestration mode', 'system');
        } else if (mode === 'slack') {
            if (slackPanel) slackPanel.classList.remove('hidden');
            connectorStatus.textContent = 'Slack Online';
            tagline.textContent = 'Team Collaboration & Notifications';
            document.documentElement.style.setProperty('--brand-accent', '#4A154B');
            log('Switched to Slack Operations mode', 'system');
        } else if (mode === 'ext-patient-viewer') {
            if (extViewerPanel) extViewerPanel.classList.remove('hidden');
            connectorStatus.textContent = 'EHR Source ─ Read-Only';
            tagline.textContent = 'External Chart Viewer';
            document.documentElement.style.setProperty('--brand-accent', '#0d9488');
            log('Switched to External Patient Viewer mode (read-only)', 'system');
        }
        if (mode === 'gdrive') {
            syncGdriveActionForm();
            resetUI(gdrivePipelineLabelOverride());
        } else if (mode === 'stripe') {
            syncStripeActionForm();
            resetUI(stripePipelineLabelOverride());
        } else if (mode === 'salesforce') {
            syncSalesforceActionForm();
            resetUI(salesforcePipelineLabelOverride());
        } else if (mode === 'ext-patient-viewer') {
            resetUI(pipelineLabels.ext_patient_viewer);
        } else {
            resetUI();
        }

    }

    // Root Tab Switching (MCP Orchestration vs Connectors)
    rootTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const view = tab.dataset.view;
            rootTabs.forEach(t => t.classList.toggle('active', t === tab));

            if (view === 'agent') {
                agentPanel.classList.remove('hidden');
                connectorsView.classList.add('hidden');
                layoutMain.classList.add('agent-mode');
                connectorStatus.textContent = 'AI Agent Online';
                tagline.textContent = 'Autonomous Healthcare Assistant';
                document.documentElement.style.setProperty('--brand-accent', '#8b5cf6');
                log('Switched to AI Agent mode (MCP + LLM)', 'system');
            } else {
                agentPanel.classList.add('hidden');
                connectorsView.classList.remove('hidden');
                layoutMain.classList.remove('agent-mode');
                // By default show the list if we just switched to connectors tab
                connectorsListPanel.classList.remove('hidden');
                playgroundView.classList.add('hidden');

                connectorStatus.textContent = 'Connectors Ready';
                tagline.textContent = 'Enterprise Integration Suite';
                document.documentElement.style.setProperty('--brand-accent', '#2563eb');
                log('Switched to Connectors view', 'system');
            }
        });
    });

    // Connector Card Selection
    connectorCards.forEach(card => {
        card.addEventListener('click', () => {
            const mode = card.dataset.mode;
            connectorsListPanel.classList.add('hidden');
            playgroundView.classList.remove('hidden');
            if (backSelectionBtn) backSelectionBtn.classList.add('hidden');
            if (backToConnectorsBtn) backToConnectorsBtn.classList.remove('hidden');
            setMode(mode);
        });
    });

    // Back to Connectors List
    backToConnectorsBtn.addEventListener('click', () => {
        if (currentMode === 'ext-patient-viewer') {
            returnToHome();
        } else {
            playgroundView.classList.add('hidden');
            connectorsListPanel.classList.remove('hidden');
            if (backSelectionBtn) backSelectionBtn.classList.remove('hidden');
            connectorStatus.textContent = 'Connectors Ready';
            tagline.textContent = 'Enterprise Integration Suite';
            document.documentElement.style.setProperty('--brand-accent', '#2563eb');
            log('Returned to Connectors list', 'system');
        }
    });

    // Google Drive Sub-mode Switching
    gdriveSubTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const mode = tab.dataset.submode;
            if (mode === currentSubMode) return;

            currentSubMode = mode;
            gdriveSubTabs.forEach(t => t.classList.toggle('active', t === tab));

            const gdriveActionResolved = gdriveActionSelect ? resolveGdriveActionValue(gdriveActionSelect) : '';
            if (
                gdriveActionSelect &&
                (gdriveActionResolved === 'files.list' ||
                    gdriveActionResolved === 'files.get' ||
                    gdriveActionResolved === 'files.update')
            ) {
                log(mode === 'note' ? 'Switched to Write Note sub-mode' : 'Switched to Upload File sub-mode', 'system');
                return;
            }

            applyGdriveUploadSubMode();
            log(mode === 'note' ? 'Switched to Write Note sub-mode' : 'Switched to Upload File sub-mode', 'system');
        });
    });

    function onGdriveDriveActionChanged() {
        syncGdriveActionForm();
        if (currentMode === 'gdrive') resetUI(gdrivePipelineLabelOverride());
    }

    function scheduleGdriveDriveActionSyncFromUser() {
        queueMicrotask(() => onGdriveDriveActionChanged());
    }
    if (gdriveActionSelect) {
        gdriveActionSelect.addEventListener('change', scheduleGdriveDriveActionSyncFromUser);
    }

    function getPlaygroundTenantId() {
        const el = document.getElementById('playground-tenant-id');
        return (el && el.value ? el.value : '').trim();
    }

    function getPlaygroundConfigName() {
        const el = document.getElementById('playground-config-name');
        return (el && el.value ? el.value : '').trim();
    }

    function playgroundRequestHeaders(extra = {}) {
        const headers = { 'Content-Type': 'application/json', ...extra };
        const tenantId = getPlaygroundTenantId();
        if (tenantId) {
            headers['X-Tenant-ID'] = tenantId;
        }
        return headers;
    }

    function withTenancyQuery(endpoint) {
        const configName = getPlaygroundConfigName();
        if (!configName) return endpoint;
        const sep = endpoint.includes('?') ? '&' : '?';
        return `${endpoint}${sep}config_name=${encodeURIComponent(configName)}`;
    }

    async function handleSubmission(payload, endpoint, btn, btnLbl, spinner, resetText, pipelineLabelOverride = null) {
        resetUI(pipelineLabelOverride);

        btn.disabled = true;
        spinner.classList.remove('hidden');
        btnLbl.textContent = 'Orchestrating...';

        log(`Initiating intelligent ${currentMode.toUpperCase()} orchestration...`, 'system');
        const tenantId = getPlaygroundTenantId();
        const configName = getPlaygroundConfigName();
        if (tenantId) {
            log(`Tenant: ${tenantId}`, 'system');
        } else {
            log('Tenant: __default__ (no X-Tenant-ID)', 'system');
        }
        if (configName) {
            log(`Config name: ${configName}`, 'system');
            payload = { ...payload, config_name: configName };
        }

        const firstNode = nodes.find((n) => n && !n.classList.contains('hidden'));
        if (firstNode) firstNode.classList.add('active');

        try {
            const response = await fetch(withTenancyQuery(endpoint), {
                method: 'POST',
                headers: playgroundRequestHeaders(),
                body: JSON.stringify(payload)
            });

            const data = await response.json().catch(() => ({}));

            if (!response.ok) {
                const detail = data.detail || data.message || `Server returned ${response.status}`;
                throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
            }

            traceDisplay.textContent = (data.trace_id || '').toUpperCase() || '—';

            for (let i = 0; i < (data.steps || []).length; i++) {
                const step = data.steps[i];
                const node = nodes[i];
                if (!node) continue;

                const statusLabel = node.querySelector('.node-status');
                const label = node.querySelector('.node-label');
                const responseBtn = node.querySelector('.view-response-btn');
                const responseDiv = node.querySelector('.response-json');

                node.classList.remove('pending');
                node.classList.add('active');

                if (step.display_name) {
                    label.textContent = `${i + 1}. ${step.display_name}`;
                }
                statusLabel.textContent = "Processing...";

                await new Promise(r => setTimeout(r, 600));

                node.className = `flow-node ${step.status}`;
                statusLabel.textContent = step.details;

                if (step.retries && step.retries > 0) {
                    statusLabel.textContent += ` (Retries: ${step.retries})`;
                    log(`${step.name}: Retried ${step.retries} time(s)`, 'system');
                }

                if (step.status === 'success') {
                    log(`${step.name}: SUCCESS`, 'success');
                    if (step.data) {
                        if (step.data.beautiful_data && node.querySelector('.beautiful-response')) {
                             const bData = step.data.beautiful_data;
                             const bDiv = node.querySelector('.beautiful-response');

                             bDiv.innerHTML = `
                                <div class="beautiful-doc-card">
                                    <div class="doc-card-header">
                                         <div class="doc-icon" style="background: ${currentMode === 'ehr' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(99, 102, 241, 0.15)'}; color: ${currentMode === 'ehr' ? '#10b981' : '#6366f1'}">
                                             <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                                 <path d="M9 12H15M9 16H15M19 6V18C19 19.1046 18.1046 20 17 20H7C5.89543 20 5 19.1046 5 18V6C5 4.89543 5.89543 4 7 4H13.5M19 6L13.5 4M19 6V4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                             </svg>
                                         </div>
                                         <div class="doc-title-area">
                                             <h4>${escapeHTML(bData.type)}</h4>
                                             <span class="doc-meta">ID: ${escapeHTML(bData.id)} • ${new Date(bData.date).toLocaleString([], {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute:'2-digit'})}</span>
                                         </div>
                                         <div class="doc-trace-badge" title="Orchestrator Trace ID" style="margin-right: 12px; font-family: monospace; font-size: 0.725rem; color: var(--brand-light); background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.2); padding: 4px 8px; border-radius: 6px; font-weight: 600;">
                                             TRC-${data.trace_id.toUpperCase()}
                                         </div>
                                         <div class="doc-status-badge">${escapeHTML(bData.status)}</div>
                                     </div>
                                     <div class="doc-details">
                                         <div class="detail-col">
                                             <span class="d-label">${currentMode === 'ehr' ? 'Patient' : 'Reported By'}</span>
                                             <span class="d-value">${escapeHTML(bData.patient_name)}</span>
                                         </div>
                                         <div class="detail-col">
                                             <span class="d-label">${currentMode === 'ehr' ? 'Author' : 'Orchestrator'}</span>
                                             <span class="d-value">${escapeHTML(bData.author)}</span>
                                         </div>
                                         <div class="detail-col">
                                             <span class="d-label">${currentMode === 'ehr' ? 'Category' : 'Component'}</span>
                                             <span class="d-value">${escapeHTML(bData.category)}</span>
                                         </div>
                                         <div class="detail-col">
                                             <span class="d-label">Description</span>
                                             <span class="d-value">${escapeHTML(bData.description)}</span>
                                         </div>
                                     </div>
                                     <div class="doc-note-preview">
                                         <span class="d-label">${currentMode === 'ehr' ? 'Clinical Content' : 'Incident Log'}</span>
                                         <p>${escapeHTML(bData.content_text)}</p>
                                     </div>
                                </div>
                             `;

                             if (step.data.raw) {
                                  responseDiv.textContent = JSON.stringify(step.data.raw, null, 2);
                                  responseBtn.classList.remove('hidden');
                                  responseBtn.onclick = () => {
                                      const isHidden = responseDiv.classList.contains('hidden');
                                      responseDiv.classList.toggle('hidden');
                                      bDiv.classList.toggle('hidden');
                                      responseBtn.textContent = isHidden ? 'View Formatted' : 'View Raw JSON';
                                  };
                             }
                             bDiv.classList.remove('hidden');
                        } else if (step.data.raw) {
                            responseDiv.textContent = JSON.stringify(step.data.raw, null, 2);
                            responseBtn.classList.remove('hidden');
                            responseBtn.onclick = () => {
                                const isHidden = responseDiv.classList.contains('hidden');
                                responseDiv.classList.toggle('hidden');
                                responseBtn.textContent = isHidden ? 'Hide Response' : 'View Response';
                            };
                        }
                    }
                    if (nodes[i + 1] && i + 1 < data.steps.length) {
                        nodes[i + 1].classList.add('active');
                    }
                } else {
                    log(`${step.name}: FAILED - ${step.details}`, 'error');
                    break;
                }
            }

            const allSuccess = data.steps.every(s => s.status === 'success');
            if (data.success && allSuccess) {
                log('Orchestration complete. Target system updated.', 'success');
                finalResult.classList.remove('hidden');
                finalResult.classList.remove('error-toast');
                summaryText.textContent = data.human_summary;
                const refPrefix = (data.final_resource_id != null && String(data.final_resource_id).trim() !== '')
                    ? `REF: ${escapeHTML(String(data.final_resource_id))} <span style="margin: 0 8px; opacity: 0.5;">|</span> `
                    : '';
                resultTag.innerHTML = `${refPrefix}<span style="font-family: monospace; color: var(--brand-light); font-size: 0.85em;">TRC-${data.trace_id.toUpperCase()}</span>`;
                btn.style.background = 'var(--success)';
                btnLbl.textContent = 'Workflow Active';
            } else {
                log('Workflow encountered issues.', 'error');
                btn.style.background = 'var(--error)';
                btnLbl.textContent = 'Workflow Failed';
            }

        } catch (error) {
            log(`Critical Failure: ${error.message}`, 'error');
            btnLbl.textContent = 'System Error';
        } finally {
            btn.disabled = false;
            spinner.classList.add('hidden');
            setTimeout(() => {
                if (!finalResult.classList.contains('hidden')) return;
                btnLbl.textContent = resetText;
                btn.style.background = '';
            }, 3000);
        }
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(form);
        const payload = Object.fromEntries(formData.entries());
        await handleSubmission(payload, '/scenarios/post-consultation', runBtn, btnText, spinner, 'Sync to Patient Chart');
    });

    itopsForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(itopsForm);
        const payload = Object.fromEntries(formData.entries());
        await handleSubmission(payload, '/scenarios/report-incident', itopsRunBtn, itopsBtnText, itopsSpinner, 'Submit IT Ticket');
    });

    cernerForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(cernerForm);
        const payload = Object.fromEntries(formData.entries());
        await handleSubmission(payload, '/scenarios/cerner-post-consultation', cernerRunBtn, cernerBtnText, cernerSpinner, 'Sync to Cerner Chart');
    });

    stripeForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(stripeForm);
        const payload = Object.fromEntries(formData.entries());
        
        let endpoint = '/scenarios/stripe-charge';
        let submitPayload = {};
        
        if (currentStripeSubMode === 'charge' || !currentStripeSubMode) {
            submitPayload = {
                amount: parseInt(payload.charge_amount, 10),
                currency: payload.charge_currency,
                description: payload.charge_description
            };
            endpoint = '/scenarios/stripe-charge';
        } else if (currentStripeSubMode === 'payment_intent') {
            submitPayload = {
                amount: parseInt(payload.pi_amount, 10),
                currency: payload.pi_currency,
                customer_id: payload.pi_customer || undefined,
                payment_method: payload.pi_payment_method || undefined,
                confirm: payload.pi_confirm === 'on'
            };
            endpoint = '/scenarios/stripe-payment-intent';
        } else if (currentStripeSubMode === 'subscription') {
            submitPayload = {
                customer_id: payload.sub_customer,
                price_id: payload.sub_price,
                card_token: payload.sub_token || undefined
            };
            endpoint = '/scenarios/stripe-subscription';
        } else if (currentStripeSubMode === 'cancel_subscription') {
            submitPayload = {
                subscription_id: payload.cancel_sub_id
            };
            endpoint = '/scenarios/stripe-cancel-subscription';
        } else if (currentStripeSubMode === 'refund') {
            const isPI = payload.refund_target_id.startsWith('pi_');
            submitPayload = {
                charge_id: !isPI && payload.refund_target_id ? payload.refund_target_id : undefined,
                payment_intent_id: isPI ? payload.refund_target_id : undefined,
                amount: payload.refund_amount ? parseInt(payload.refund_amount, 10) : undefined
            };
            endpoint = '/scenarios/stripe-refund';
        }

        await handleSubmission(submitPayload, endpoint, stripeRunBtn, stripeBtnText, stripeSpinner, 'Process Action');
    });

    salesforceForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(salesforceForm);
        const payload = Object.fromEntries(formData.entries());
        
        let endpoint = '/scenarios/salesforce-create-lead';
        let submitPayload = {};
        
        if (currentSalesforceSubMode === 'create_lead') {
            submitPayload = {
                first_name: payload.lead_first_name || undefined,
                last_name: payload.lead_last_name,
                company: payload.lead_company,
                email: payload.lead_email || undefined
            };
            endpoint = '/scenarios/salesforce-create-lead';
        } else if (currentSalesforceSubMode === 'update_lead') {
            submitPayload = {
                record_id: payload.lead_id,
                first_name: payload.lead_first_name || undefined,
                last_name: payload.lead_last_name || undefined,
                company: payload.lead_company || undefined,
                email: payload.lead_email || undefined
            };
            endpoint = '/scenarios/salesforce-update-lead';
        } else if (currentSalesforceSubMode === 'read_lead') {
            submitPayload = { record_id: payload.generic_record_id };
            endpoint = '/scenarios/salesforce-read-lead';
        } else if (currentSalesforceSubMode === 'delete_lead') {
            submitPayload = { record_id: payload.generic_record_id };
            endpoint = '/scenarios/salesforce-delete-lead';
        } else if (currentSalesforceSubMode === 'create_contact') {
            submitPayload = {
                first_name: payload.contact_first_name || undefined,
                last_name: payload.contact_last_name,
                email: payload.contact_email || undefined,
                account_id: payload.contact_account_id || undefined
            };
            endpoint = '/scenarios/salesforce-create-contact';
        } else if (currentSalesforceSubMode === 'update_contact') {
            submitPayload = {
                record_id: payload.contact_id,
                first_name: payload.contact_first_name || undefined,
                last_name: payload.contact_last_name || undefined,
                email: payload.contact_email || undefined,
                account_id: payload.contact_account_id || undefined
            };
            endpoint = '/scenarios/salesforce-update-contact';
        } else if (currentSalesforceSubMode === 'read_contact') {
            submitPayload = { record_id: payload.generic_record_id };
            endpoint = '/scenarios/salesforce-read-contact';
        } else if (currentSalesforceSubMode === 'delete_contact') {
            submitPayload = { record_id: payload.generic_record_id };
            endpoint = '/scenarios/salesforce-delete-contact';
        }

        await handleSubmission(submitPayload, endpoint, salesforceRunBtn, salesforceBtnText, salesforceSpinner, 'Execute Action');
    });


    if (salesforceActionSelect) {
        salesforceActionSelect.addEventListener('change', (e) => {
            const mode = e.target.value;
            if (mode === currentSalesforceSubMode) return;
            currentSalesforceSubMode = mode;
            syncSalesforceActionForm();
            resetUI(salesforcePipelineLabelOverride());
            log(`Switched to Salesforce mode [${currentSalesforceSubMode}]`);
        });
    }


    if (stripeActionSelect) {
        stripeActionSelect.addEventListener('change', (e) => {
            const mode = e.target.value;
            if (mode === currentStripeSubMode) return;
            currentStripeSubMode = mode;
            syncStripeActionForm();
            resetUI(stripePipelineLabelOverride());
            log(`Switched to Stripe mode [${currentStripeSubMode}]`);
        });
    }

    // File Preview Logic
    if (gdriveFileInput && fileChosenPreview && previewName && fileDropZone) {
        gdriveFileInput.addEventListener('change', () => {
            if (gdriveFileInput.files.length > 0) {
                const fileName = gdriveFileInput.files[0].name;
                previewName.textContent = fileName;
                fileChosenPreview.classList.remove('hidden');
                fileDropZone.classList.add('hidden');
            }
        });
    }

    if (removeFileBtn && gdriveFileInput && fileChosenPreview && fileDropZone) {
        removeFileBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            gdriveFileInput.value = '';
            fileChosenPreview.classList.add('hidden');
            fileDropZone.classList.remove('hidden');
        });
    }

    if (fileDropZone) {
        fileDropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            fileDropZone.style.borderColor = 'var(--brand-accent)';
            fileDropZone.style.background = 'rgba(255, 255, 255, 0.08)';
        });

        fileDropZone.addEventListener('dragleave', () => {
            fileDropZone.style.borderColor = '';
            fileDropZone.style.background = '';
        });

        fileDropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            fileDropZone.style.borderColor = '';
            fileDropZone.style.background = '';
            if (gdriveFileInput && e.dataTransfer.files.length > 0) {
                gdriveFileInput.files = e.dataTransfer.files;
                gdriveFileInput.dispatchEvent(new Event('change'));
            }
        });
    }

    gdriveForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(gdriveForm);
        const payload = Object.fromEntries(formData.entries());

        const fileInput = document.getElementById('gdrive-file');

        if (payload.action === 'files.list') {
            const rawPs = payload.list_page_size;
            const ps = parseInt(String(rawPs != null ? rawPs : '10'), 10);
            payload.list_page_size = Number.isFinite(ps) ? Math.min(100, Math.max(1, ps)) : 10;
            const lq = String(payload.list_query || '').trim();
            const lf = String(payload.list_fields || '').trim();
            if (!lq) delete payload.list_query;
            if (!lf) delete payload.list_fields;
            await handleSubmission(
                payload,
                '/scenarios/gdrive-archival',
                gdriveRunBtn,
                gdriveBtnText,
                gdriveSpinner,
                'List files',
                pipelineLabels.gdriveList
            );
            return;
        }

        if (payload.action === 'files.get') {
            payload.get_file_id = String(payload.get_file_id || '').trim();
            const gf = String(payload.get_fields || '').trim();
            if (gf) payload.get_fields = gf;
            else delete payload.get_fields;
            await handleSubmission(
                payload,
                '/scenarios/gdrive-archival',
                gdriveRunBtn,
                gdriveBtnText,
                gdriveSpinner,
                'Get file',
                pipelineLabels.gdriveGet
            );
            return;
        }

        if (payload.action === 'files.update') {
            payload.update_file_id = String(payload.update_file_id || '').trim();
            const un = String(payload.update_name || '').trim();
            const um = String(payload.update_mime_type || '').trim();
            const uap = String(payload.update_add_parents || '').trim();
            const urp = String(payload.update_remove_parents || '').trim();
            if (un) payload.update_name = un;
            else delete payload.update_name;
            if (um) payload.update_mime_type = um;
            else delete payload.update_mime_type;
            if (uap) payload.update_add_parents = uap;
            else delete payload.update_add_parents;
            if (urp) payload.update_remove_parents = urp;
            else delete payload.update_remove_parents;
            await handleSubmission(
                payload,
                '/scenarios/gdrive-archival',
                gdriveRunBtn,
                gdriveBtnText,
                gdriveSpinner,
                'Update file',
                pipelineLabels.gdriveUpdate
            );
            return;
        }

        if (currentSubMode === 'file' && fileInput.files.length > 0) {
            const file = fileInput.files[0];
            const reader = new FileReader();

            // Re-use the UI update logic outside to show "Encrypting" immediately
            resetUI();
            gdriveRunBtn.disabled = true;
            gdriveSpinner.classList.remove('hidden');
            gdriveBtnText.textContent = 'Encrypting File...';

            reader.onload = async (event) => {
                try {
                    const base64Data = event.target.result.split(',')[1];
                    payload.file_base64 = base64Data;
                    payload.file_mime_type = file.type || 'application/octet-stream';

                    // Auto-update document name to the real file name if sending a binary file
                    payload.document_name = file.name;

                    await handleSubmission(payload, '/scenarios/gdrive-archival', gdriveRunBtn, gdriveBtnText, gdriveSpinner, 'Encrypt & Archive');
                } catch (error) {
                    log(`File parsing error: ${error.message}`, 'error');
                    gdriveBtnText.textContent = 'System Error';
                    gdriveRunBtn.disabled = false;
                    gdriveSpinner.classList.add('hidden');
                }
            };

            reader.onerror = () => {
                log('Failed to read binary file from memory.', 'error');
                gdriveBtnText.textContent = 'System Error';
                gdriveRunBtn.disabled = false;
                gdriveSpinner.classList.add('hidden');
            };

            reader.readAsDataURL(file);
        } else {
            // Standard text submission
            await handleSubmission(payload, '/scenarios/gdrive-archival', gdriveRunBtn, gdriveBtnText, gdriveSpinner, 'Encrypt & Archive');
        }
    });

    // --- Google Drive runtime config CRUD (tenant-scoped via Tenancy bar) ---
    const gdriveCfgResult = document.getElementById('gdrive-cfg-result');
    const GDRIVE_CFG_BASE = '/v1/connectors/google_drive/configs';

    function showGdriveCfgResult(obj) {
        if (!gdriveCfgResult) return;
        gdriveCfgResult.classList.remove('hidden');
        gdriveCfgResult.textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
    }

    async function gdriveCfgFetch(method, path = '', body = null) {
        const tenantId = getPlaygroundTenantId();
        if (!tenantId) {
            throw new Error('Set Tenant ID in the Tenancy bar before managing configs (e.g. acme).');
        }
        const opts = { method, headers: playgroundRequestHeaders() };
        if (body != null) {
            opts.body = JSON.stringify(body);
        }
        const response = await fetch(`${GDRIVE_CFG_BASE}${path}`, opts);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = data.detail || `HTTP ${response.status}`;
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return data;
    }

    document.getElementById('gdrive-cfg-create')?.addEventListener('click', async () => {
        try {
            const name = (document.getElementById('gdrive-cfg-name')?.value || 'default').trim();
            const isDefault = document.getElementById('gdrive-cfg-default')?.value === 'true';
            const data = await gdriveCfgFetch('POST', '', {
                name,
                default: isDefault,
                config: {},
                // Same auth shape as connectors.yaml; tenant secrets resolve via
                // NW_{TENANT}_GOOGLE_DRIVE_GOOGLE_DRIVE_SA_JSON (or plain GOOGLE_DRIVE_SA_JSON for __default__).
                auth: {
                    provider: 'service_account',
                    sa_json_secret: 'GOOGLE_DRIVE_SA_JSON',
                    scopes: ['https://www.googleapis.com/auth/drive'],
                },
            });
            showGdriveCfgResult(data);
            log(`Created google_drive config '${name}' for tenant '${getPlaygroundTenantId()}'`, 'success');
        } catch (err) {
            showGdriveCfgResult({ error: err.message });
            log(`Config create failed: ${err.message}`, 'error');
        }
    });

    document.getElementById('gdrive-cfg-list')?.addEventListener('click', async () => {
        try {
            const data = await gdriveCfgFetch('GET');
            showGdriveCfgResult(data);
            log(`Listed google_drive configs for tenant '${getPlaygroundTenantId()}'`, 'success');
        } catch (err) {
            showGdriveCfgResult({ error: err.message });
            log(`Config list failed: ${err.message}`, 'error');
        }
    });

    document.getElementById('gdrive-cfg-set-default')?.addEventListener('click', async () => {
        try {
            const name = (document.getElementById('gdrive-cfg-name')?.value || '').trim();
            if (!name) throw new Error('Config name is required');
            const data = await gdriveCfgFetch('PUT', `/${encodeURIComponent(name)}/default`);
            showGdriveCfgResult(data);
            log(`Set '${name}' as default for tenant '${getPlaygroundTenantId()}'`, 'success');
        } catch (err) {
            showGdriveCfgResult({ error: err.message });
            log(`Set default failed: ${err.message}`, 'error');
        }
    });

    document.getElementById('gdrive-cfg-delete')?.addEventListener('click', async () => {
        try {
            const name = (document.getElementById('gdrive-cfg-name')?.value || '').trim();
            if (!name) throw new Error('Config name is required');
            // If deleting the default while siblings exist, nominate "default" or leave blank for last-config case.
            const newDefault = name === 'default' ? '' : 'default';
            const q = newDefault ? `?new_default=${encodeURIComponent(newDefault)}` : '';
            const data = await gdriveCfgFetch('DELETE', `/${encodeURIComponent(name)}${q}`);
            showGdriveCfgResult(data);
            log(`Deleted google_drive config '${name}' for tenant '${getPlaygroundTenantId()}'`, 'success');
        } catch (err) {
            showGdriveCfgResult({ error: err.message });
            log(`Config delete failed: ${err.message}`, 'error');
        }
    });

    if (slackActionSelect) {
        slackActionSelect.addEventListener('change', () => {
            const action = slackActionSelect.value;
            if (action === 'upload_file') {
                if (slackMessageSection) slackMessageSection.classList.add('hidden');
                if (slackFileSection) slackFileSection.classList.remove('hidden');
            } else {
                if (slackMessageSection) slackMessageSection.classList.remove('hidden');
                if (slackFileSection) slackFileSection.classList.add('hidden');
            }
        });
    }

    if (slackFileInput && slackFileChosenPreview && slackPreviewName && slackFileDropZone) {
        slackFileInput.addEventListener('change', () => {
            if (slackFileInput.files.length > 0) {
                const fileName = slackFileInput.files[0].name;
                slackPreviewName.textContent = fileName;
                slackFileChosenPreview.classList.remove('hidden');
                slackFileDropZone.classList.add('hidden');
            }
        });
    }

    if (slackRemoveFileBtn && slackFileInput && slackFileChosenPreview && slackFileDropZone) {
        slackRemoveFileBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            slackFileInput.value = '';
            slackFileChosenPreview.classList.add('hidden');
            slackFileDropZone.classList.remove('hidden');
        });
    }

    if (slackFileDropZone) {
        slackFileDropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            slackFileDropZone.style.borderColor = 'var(--brand-accent)';
            slackFileDropZone.style.background = 'rgba(255, 255, 255, 0.08)';
        });

        slackFileDropZone.addEventListener('dragleave', () => {
            slackFileDropZone.style.borderColor = '';
            slackFileDropZone.style.background = '';
        });

        slackFileDropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            slackFileDropZone.style.borderColor = '';
            slackFileDropZone.style.background = '';
            if (slackFileInput && e.dataTransfer.files.length > 0) {
                slackFileInput.files = e.dataTransfer.files;
                slackFileInput.dispatchEvent(new Event('change'));
            }
        });
    }

    if (slackForm) {
        slackForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(slackForm);
            const payload = Object.fromEntries(formData.entries());
            
            if (payload.action === 'upload_file' && slackFileInput && slackFileInput.files.length > 0) {
                const file = slackFileInput.files[0];
                const reader = new FileReader();
                
                resetUI();
                if (slackRunBtn) slackRunBtn.disabled = true;
                if (slackSpinner) slackSpinner.classList.remove('hidden');
                if (slackBtnText) slackBtnText.textContent = 'Formatting payload...';
                
                reader.onload = async (event) => {
                    try {
                        const base64Data = event.target.result.split(',')[1];
                        payload.content_base64 = base64Data;
                        // Always override filename with actual file name if uploaded directly
                        payload.filename = file.name;
                        
                        await handleSubmission(payload, '/scenarios/slack-messaging', slackRunBtn, slackBtnText, slackSpinner, 'Send to Slack');
                    } catch (error) {
                        log(`File parsing error: ${error.message}`, 'error');
                        if (slackBtnText) slackBtnText.textContent = 'System Error';
                        if (slackRunBtn) slackRunBtn.disabled = false;
                        if (slackSpinner) slackSpinner.classList.add('hidden');
                    }
                };
                
                reader.onerror = () => {
                    log('Failed to read binary file from memory.', 'error');
                    if (slackBtnText) slackBtnText.textContent = 'System Error';
                    if (slackRunBtn) slackRunBtn.disabled = false;
                    if (slackSpinner) slackSpinner.classList.add('hidden');
                };
                
                reader.readAsDataURL(file);
            } else {
                await handleSubmission(payload, '/scenarios/slack-messaging', slackRunBtn, slackBtnText, slackSpinner, 'Send to Slack');
            }
        });
    }

    // ======================================================
    // AI Agent Chat Logic
    // ======================================================

    function appendChatBubble(role, content) {
        const bubble = document.createElement('div');
        bubble.className = `chat-bubble ${role}`;
        const roleLabel = role === 'user' ? 'You' : 'Agent';
        bubble.innerHTML = `<div class="bubble-content"><span class="bubble-role">${escapeHTML(roleLabel)}</span><p>${escapeHTML(content)}</p></div>`;
        agentChatHistory.appendChild(bubble);
        agentChatHistory.scrollTop = agentChatHistory.scrollHeight;
        return bubble;
    }

    function appendStreamingBubble(label = 'Agent Streaming') {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble assistant streaming-bubble';
        bubble.innerHTML = `
            <div class="bubble-content">
                <span class="bubble-role">${escapeHTML(label)}</span>
                <p class="streaming-text"></p>
                <div class="stream-tail-loader">
                    <span class="typing-dot"></span>
                    <span class="typing-dot"></span>
                    <span class="typing-dot"></span>
                    <span>Streaming response... <strong class="stream-running-timer">0.0s</strong></span>
                </div>
            </div>
        `;
        agentChatHistory.appendChild(bubble);
        agentChatHistory.scrollTop = agentChatHistory.scrollHeight;
        return {
            bubble,
            text: bubble.querySelector('.streaming-text'),
            loader: bubble.querySelector('.stream-tail-loader'),
            timer: bubble.querySelector('.stream-running-timer')
        };
    }

    function appendTraceBadge(traceId, transportLabel = '') {
        if (!traceId) return;
        const badge = document.createElement('div');
        badge.className = 'chat-trace-badge';
        const suffix = transportLabel ? ` | ${transportLabel}` : '';
        badge.textContent = `TRC-${traceId.toUpperCase().slice(0, 8)}${suffix}`;
        agentChatHistory.appendChild(badge);
        agentChatHistory.scrollTop = agentChatHistory.scrollHeight;
    }

    function appendStreamEndMessage(message, success = true, finalTime = null) {
        const end = document.createElement('div');
        end.className = `stream-end-message ${success ? 'success' : 'error'}`;
        let displayMessage = message || (success ? 'Streaming completed.' : 'Streaming ended with an error.');
        if (finalTime) {
            displayMessage += ` (Total Time: ${finalTime}s)`;
        }
        end.textContent = displayMessage;
        agentChatHistory.appendChild(end);
        agentChatHistory.scrollTop = agentChatHistory.scrollHeight;
    }

    function updateAgentTransportStatus() {
        if (!agentTransportStatus) return;
        const label = agentTransportMode === 'streamable-http' ? 'Streamable HTTP' : 'stdio';
        agentTransportStatus.querySelector('.transport-status-label').textContent = `Transport: ${label}`;
    }

    async function loadAgentTransportMode() {
        try {
            const response = await fetch('/scenarios/agent-transport');
            if (!response.ok) throw new Error(`Server returned ${response.status}`);
            const data = await response.json();
            agentTransportMode = data.transport === 'streamable-http' ? 'streamable-http' : 'stdio';
        } catch (error) {
            agentTransportMode = 'stdio';
            log(`Transport status unavailable; using stdio UI mode (${error.message})`, 'system');
        }
        updateAgentTransportStatus();
    }

    async function readNdjsonStream(response, handlers) {
        if (!response.body) throw new Error('Browser did not expose a readable response stream');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pending = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            pending += decoder.decode(value, { stream: true });
            const lines = pending.split('\n');
            pending = lines.pop() || '';

            for (const line of lines) {
                if (!line.trim()) continue;
                const event = JSON.parse(line);
                if (handlers[event.type]) handlers[event.type](event);
            }
        }

        if (pending.trim()) {
            const event = JSON.parse(pending);
            if (handlers[event.type]) handlers[event.type](event);
        }
    }

    function appendStepCard(step) {
        const card = document.createElement('div');
        card.className = 'agent-step-card';

        const isError = (step.result || '').includes('ERROR');
        const resultClass = isError ? 'error' : 'success';
        const resultIcon = isError ? '✗' : '✓';

        let argsStr = '';
        try { argsStr = JSON.stringify(step.args, null, 2); } catch(e) { argsStr = String(step.args); }

        let resultPreview = step.result || '';
        if (resultPreview.length > 200) resultPreview = resultPreview.slice(0, 200) + '…';

        card.innerHTML = `
            <div class="step-card-header">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                Tool: ${escapeHTML(step.tool)}
            </div>
            <div class="step-card-body">${escapeHTML(argsStr)}</div>
            ${resultPreview ? `<div class="step-card-result ${resultClass}">${resultIcon} ${escapeHTML(resultPreview)}</div>` : ''}
        `;
        
        const streamingBubble = agentChatHistory.querySelector('.streaming-bubble');
        if (streamingBubble) {
            agentChatHistory.insertBefore(card, streamingBubble);
        } else {
            agentChatHistory.appendChild(card);
        }
        agentChatHistory.scrollTop = agentChatHistory.scrollHeight;
    }

    async function readNdjsonStream(response, handlers) {
        if (!response.body) throw new Error('Browser did not expose a readable response stream');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pending = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            pending += decoder.decode(value, { stream: true });
            const lines = pending.split('\n');
            pending = lines.pop() || '';

            for (const line of lines) {
                if (!line.trim()) continue;
                const event = JSON.parse(line);
                if (handlers[event.type]) handlers[event.type](event);
            }
        }

        if (pending.trim()) {
            const event = JSON.parse(pending);
            if (handlers[event.type]) handlers[event.type](event);
        }
    }

    async function sendAgentMessage() {
        const message = agentInput.value.trim();
        if (!message || agentBusy) return;

        agentBusy = true;
        agentSendBtn.disabled = true;
        agentInput.value = '';

        // Add user bubble
        appendChatBubble('user', message);
        agentConversationHistory.push({ role: 'user', content: message });

        // Show typing indicator
        agentTyping.classList.remove('hidden');
        agentChatHistory.scrollTop = agentChatHistory.scrollHeight;

        log(`Agent Chat: Sending message...`, 'system');

        let timerInterval = null;
        let streamView = null;
        const startTime = Date.now();

        try {
            if (agentTransportMode === 'streamable-http') {
                // Instantly display the streaming bubble and start the active timer
                streamView = appendStreamingBubble();
                agentTyping.classList.add('hidden'); // Hide generic typing dot loader

                function startRunningTimer() {
                    if (timerInterval) return;
                    timerInterval = setInterval(() => {
                        if (streamView && streamView.timer) {
                            const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
                            streamView.timer.textContent = `${elapsed}s`;
                        }
                    }, 100);
                }
                startRunningTimer();

                const response = await fetch('/scenarios/agent-chat-stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: message,
                        history: agentConversationHistory.slice(0, -1)
                    })
                });

                if (!response.ok) throw new Error(`Server returned ${response.status}`);

                let finalText = '';
                let traceId = '';
                let success = true;
                let doneMessage = '';

                await readNdjsonStream(response, {
                    meta: (event) => {
                        traceId = event.trace_id || traceId;
                    },
                    status: (event) => {
                        log(`Agent Stream: ${event.message}`, 'system');
                    },
                    step: (event) => {
                        agentTyping.classList.add('hidden');
                        appendStepCard({
                            tool: event.tool,
                            args: event.args || {},
                            result: event.result || ''
                        });
                    },
                    final_chunk: (event) => {
                        agentTyping.classList.add('hidden');
                        finalText += event.content || '';
                        streamView.text.textContent = finalText;
                        agentChatHistory.scrollTop = agentChatHistory.scrollHeight;
                    },
                    error: (event) => {
                        success = false;
                        agentTyping.classList.add('hidden');
                        finalText += event.message || '';
                        streamView.text.textContent = finalText;
                    },
                    done: (event) => {
                        traceId = event.trace_id || traceId;
                        success = Boolean(event.success);
                        doneMessage = event.message || `Streaming ${success ? 'completed' : 'failed'}. trace_id=${traceId}`;
                        
                        if (timerInterval) {
                            clearInterval(timerInterval);
                            timerInterval = null;
                        }
                        const finalElapsed = ((Date.now() - startTime) / 1000).toFixed(2);
                        streamView.loader.classList.add('hidden');
                        appendStreamEndMessage(doneMessage, success, finalElapsed);
                    }
                });

                agentTyping.classList.add('hidden');
                if (!doneMessage) {
                    if (timerInterval) {
                        clearInterval(timerInterval);
                        timerInterval = null;
                    }
                    const finalElapsed = ((Date.now() - startTime) / 1000).toFixed(2);
                    streamView.loader.classList.add('hidden');
                    doneMessage = `Streaming connection closed before done event. trace_id=${traceId || 'unknown'}`;
                    appendStreamEndMessage(doneMessage, false, finalElapsed);
                    success = false;
                }
                if (!finalText) {
                    finalText = success ? 'Completed.' : 'The stream ended before a final answer was returned.';
                    if (streamView) streamView.text.textContent = finalText;
                }
                agentConversationHistory.push({ role: 'assistant', content: finalText });
                appendTraceBadge(traceId, 'streamable-http');
                log(`Agent Chat: ${success ? 'Stream complete' : 'Stream failed'} | ${doneMessage}`, success ? 'success' : 'error');
                return;
            }

            const response = await fetch('/scenarios/agent-chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: message,
                    history: agentConversationHistory.slice(0, -1) // Exclude current message (already in payload)
                })
            });

            if (!response.ok) throw new Error(`Server returned ${response.status}`);

            const data = await response.json();

            // Hide typing indicator
            agentTyping.classList.add('hidden');

            // Render tool step cards
            if (data.steps && data.steps.length > 0) {
                for (const step of data.steps) {
                    appendStepCard(step);
                }
            }

            // Render assistant reply
            appendChatBubble('assistant', data.reply);
            agentConversationHistory.push({ role: 'assistant', content: data.reply });

            // Add trace badge
            appendTraceBadge(data.trace_id);

            log(`Agent Chat: ${data.success ? 'Success' : 'Responded'} | steps=${data.steps ? data.steps.length : 0}`, data.success ? 'success' : 'system');

        } catch (error) {
            if (timerInterval) clearInterval(timerInterval);
            if (streamView && streamView.loader) streamView.loader.classList.add('hidden');
            agentTyping.classList.add('hidden');
            appendChatBubble('assistant', `Sorry, I couldn't reach the server: ${error.message}. Please check that the backend is running.`);
            log(`Agent Chat Error: ${error.message}`, 'error');
        } finally {
            agentBusy = false;
            agentSendBtn.disabled = false;
            agentInput.focus();
        }
    }

    // Event listeners for chat
    loadAgentTransportMode();
    agentSendBtn.addEventListener('click', sendAgentMessage);
    agentInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendAgentMessage();
        }
    });

    // ─── External Patient Viewer — form submission ───────────────────────────
    if (extViewerForm) {
        extViewerForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const fd = new FormData(extViewerForm);
            const patientId   = (fd.get('patient_id')       || '').trim();
            const givenName   = (fd.get('patient_given')    || '').trim();
            const familyName  = (fd.get('patient_family')   || '').trim();
            const birthdate   = (fd.get('patient_birthdate')|| '').trim();
            const sourceSystem = fd.get('source_system') || 'epic';
            const maxEnc  = parseInt(fd.get('max_encounters') || '5', 10);
            const maxDocs = parseInt(fd.get('max_documents')  || '10', 10);

            // Client-side guard: need at least one identity field
            if (!patientId && !givenName && !familyName) {
                log('Viewer: provide a Patient ID or at least a given/family name.', 'error');
                return;
            }

            const payload = {
                source_system: sourceSystem,
                max_encounters: maxEnc,
                max_documents: maxDocs,
            };
            if (patientId)  payload.patient_id       = patientId;
            if (givenName)  payload.patient_given    = givenName;
            if (familyName) payload.patient_family   = familyName;
            if (birthdate)  payload.patient_birthdate = birthdate;

            await handleSubmission(
                payload,
                '/scenarios/external-patient-viewer',
                extViewerRunBtn,
                extViewerBtnText,
                extViewerSpinner,
                'Load External Chart',
                pipelineLabels.ext_patient_viewer
            );
        });
    }

    // Initial Load UI State
    resetUI();
});
