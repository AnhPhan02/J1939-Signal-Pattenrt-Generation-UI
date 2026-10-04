/**
 * J1939 Intelligent Waveform & Signal Verification Hub Frontend Controller
 * Creators: Vishal Meyyappan R (3rd Year ECE - ACT) & Srikar (4th year ECE)
 * Institution: Chennai Institute of Technology (CIT Chennai) & STUST Taiwan
 */

document.addEventListener('DOMContentLoaded', () => {
    // Application State
    const state = {
    spns: [],
    selectedSpns: new Set(),
    spnConfigs: new Map(), // spn -> { type, min, max, param1 }
    currentCategory: 'all',
    searchQuery: '',
    activeVisualizerSpn: 190, // Default to SPN 190 Engine Speed
    timingMode: 0, // 0 = SAE Standards, 1 = Stress Testing
    testDurationSec: 0, // 0 = Continuous
    canBaudKbps: 250, // firmware boots at APP_DEFAULT_CAN_BAUD_KBPS (250)
    isConnected: false,
    viewMode: 'cards', // 'cards', 'matrix', 'pcan', 'validation'
    pcanTracePaused: false,
    pcanAutoScroll: true,
    pcanPgnFilter: 'all',
    pcanSelectedPgn: 61444, // Default to EEC1
    stats: {
        framesSent: 0,
        activePgns: 0,
        mode: 'SAE'
    },
    inputLog: [], // Stores snapshots of input configuration
    isRecordingTxtLog: false, // Flag for browser-simulated .txt recording
    recordedTxtLines: [], // Continuous .txt log buffer
    recordingStartTime: 0
};

    // DOM Elements
    const comPortSelect = document.getElementById('comPortSelect');
    const refreshPortsBtn = document.getElementById('refreshPortsBtn');
    const serialBaudSelect = document.getElementById('serialBaudSelect');
    const canBaudToggle = document.getElementById('canBaudToggle');
    const connectBtn = document.getElementById('connectBtn');
    const connStatusPill = document.getElementById('connStatusPill');

    const modeSmoothBtn = document.getElementById('modeSmoothBtn');
    const modeSaeBtn = document.getElementById('modeSaeBtn');
    const modeStressBtn = document.getElementById('modeStressBtn');
    const customDurationInput = document.getElementById('customDurationInput');

    const startGeneratorBtn = document.getElementById('startGeneratorBtn');
    const stopGeneratorBtn = document.getElementById('stopGeneratorBtn');
    const resetSystemBtn = document.getElementById('resetSystemBtn');

    const spnSearchInput = document.getElementById('spnSearchInput');
    const categoryTabs = document.getElementById('categoryTabs');
    const spnCardsContainer = document.getElementById('spnCardsContainer');
    const selectedCountEl = document.getElementById('selectedCount');
    const totalSpnCountEl = document.getElementById('totalSpnCount');
    const catCountAllEl = document.getElementById('catCountAll');

    const terminalScreen = document.getElementById('terminalScreen');
    const autoScrollToggle = document.getElementById('autoScrollToggle');
    const clearTerminalBtn = document.getElementById('clearTerminalBtn');
    const copyLogsBtn = document.getElementById('copyLogsBtn');
    const manualCmdInput = document.getElementById('manualCmdInput');
    const sendCmdBtn = document.getElementById('sendCmdBtn');

    const waveformCanvas = document.getElementById('waveformCanvas');
    const activeWaveformLabel = document.getElementById('activeWaveformLabel');
    const canvasMinBadge = document.getElementById('canvasMinBadge');
    const canvasCurrentBadge = document.getElementById('canvasCurrentBadge');
    const canvasMaxBadge = document.getElementById('canvasMaxBadge');

    const statFramesEl = document.getElementById('statFrames');
    const statBaudEl = document.getElementById('statBaud');
    const statActivePgnsEl = document.getElementById('statActivePgns');
    const statModeEl = document.getElementById('statMode');

    // Canvas Context
    const ctx = waveformCanvas.getContext('2d');
    let animFrameId = null;

    // =========================================================================
    // INITIALIZATION
    // =========================================================================
    async function init() {
        await loadPorts();
        await loadSpns();
        initSseStream();
        initPcanAnalyzer();
        startWaveformAnimation();
        setupEventListeners();
        loadSavedInputLogs();

        // Auto-detect and connect to STM32 port
        try {
            const sRes = await fetch('/api/status');
            const sData = await sRes.json();
            if (sData.connected) {
                state.isConnected = true;
                connectBtn.innerHTML = '<span class="btn-icon">🔌</span><span class="btn-text">Disconnect</span>';
                connectBtn.classList.replace('primary-btn', 'stop-btn');
                connStatusPill.className = 'status-pill online';
                connStatusPill.innerHTML = '<span class="status-dot"></span><span class="status-label">ONLINE: ' + sData.port + '</span>';
            } else if (comPortSelect.value && (comPortSelect.value === 'COM9' || comPortSelect.value.includes('STLink'))) {
                connectBtn.click();
            }
        } catch (e) {
            console.warn(e);
        }
    }

    // =========================================================================
    // LOAD PORTS & HARDWARE
    // =========================================================================
    async function loadPorts() {
        try {
            const res = await fetch('/api/ports');
            const data = await res.json();
            comPortSelect.innerHTML = '';

            if (data.ports.length === 0) {
                const opt = document.createElement('option');
                opt.value = '';
                opt.textContent = 'No COM ports detected (Plug STM32 USB)';
                comPortSelect.appendChild(opt);
                return;
            }

            data.ports.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.device;
                opt.textContent = `${p.device} (${p.description})`;
                if (p.description.includes('STLink') || p.description.includes('STMicroelectronics') || p.device === 'COM9') {
                    opt.selected = true;
                }
                comPortSelect.appendChild(opt);
            });
        } catch (err) {
            console.error('Failed to load ports:', err);
        }
    }

    // =========================================================================
    // LOAD 52 SPN DATABASE
    // =========================================================================
    async function loadSpns() {
        try {
            const res = await fetch('/api/spns');
            const data = await res.json();
            state.spns = data.spns || [];

            totalSpnCountEl.textContent = state.spns.length;
            catCountAllEl.textContent = state.spns.length;

            // Default configuration for each SPN (clean, continuous Sine Wave across all signals)
            state.spns.forEach(s => {
                state.spnConfigs.set(s.spn, {
                    type: 2, // Smooth Sine Wave default
                    min: s.min_physical,
                    max: s.max_physical,
                    param1: 10.0, // 10s period
                    timeframe_ms: 20, // default 20ms (50Hz)
                    t_start: 0.0,
                    t_dur: 0.0 // 0 = continuous
                });
            });

            // Pre-select SPN 190 (Engine Speed) as default
            state.selectedSpns.add(190);
            updateSelectionCount();
            renderActiveView();
            updateBusLoadMeter();
            updateVisualizerStats(190);
        } catch (err) {
            console.error('Failed to load SPNs:', err);
            spnCardsContainer.innerHTML = `<div class="empty-loading-state text-danger"><p>Failed to load SPN database.</p></div>`;
        }
    }

    // =========================================================================
    // FILTERED SPN HELPER
    // =========================================================================
    function getFilteredSpns() {
        return state.spns.filter(s => {
            // Category filter
            if (state.currentCategory !== 'all' && s.category !== state.currentCategory) {
                return false;
            }
            // Search filter
            if (state.searchQuery.trim() !== '') {
                const q = state.searchQuery.toLowerCase();
                const matchSpn = s.spn.toString().includes(q);
                const matchPgn = s.pgn.toString().includes(q);
                const matchName = s.name.toLowerCase().includes(q);
                const matchUnit = s.unit.toLowerCase().includes(q);
                return matchSpn || matchPgn || matchName || matchUnit;
            }
            return true;
        });
    }

    function renderActiveView() {
        if (state.viewMode === 'cards' || !state.viewMode) {
            renderSpnCards();
        } else if (state.viewMode === 'matrix') {
            renderMatrixTable();
        } else if (state.viewMode === 'pcan') {
            // PCAN Analyzer runs continuous live frame simulation
            const pcanContainer = document.getElementById('spnPcanContainer');
            if (pcanContainer) pcanContainer.classList.remove('hidden');
        }
    }

    // =========================================================================
    // RENDER SPN CARDS (VIEW 1)
    // =========================================================================
    // Start / Duration inputs: always shown with one decimal (0.0, 0.5, 1.0 ...).
    // A native number spinner first writes "1" and only then could be reformatted
    // to "1.0", so it flickers; our own ▲▼ buttons write the final text once.
    const SECONDS_STEP = 0.5;

    function parseSeconds(value) {
        const v = parseFloat(String(value).replace(',', '.'));
        return (isNaN(v) || v < 0) ? 0 : v;
    }

    function fmtSeconds(value) {
        return parseSeconds(value).toFixed(1);
    }

    function secondsStepperHtml(inputClass, value, spn, title) {
        return `<div class="sec-stepper">` +
            `<input type="text" inputmode="decimal" autocomplete="off" class="${inputClass} sec-input" ` +
            `value="${fmtSeconds(value)}" data-spn="${spn}" title="${title}">` +
            `<div class="sec-step-btns">` +
            `<button type="button" class="sec-step" data-dir="1" tabindex="-1" title="+${SECONDS_STEP} s">▲</button>` +
            `<button type="button" class="sec-step" data-dir="-1" tabindex="-1" title="-${SECONDS_STEP} s">▼</button>` +
            `</div></div>`;
    }

    function bindSecondsInput(input, onValue, minValue = 0) {
        const stepBy = (dir) => {
            // Move to the next 0.5 mark in that direction: 2.3 ▲ 2.5, 2.3 ▼ 2.0
            const units = parseSeconds(input.value) / SECONDS_STEP;
            const nextUnits = dir > 0 ? Math.floor(units + 1e-9) + 1 : Math.ceil(units - 1e-9) - 1;
            const next = Math.max(minValue, nextUnits * SECONDS_STEP);
            input.value = fmtSeconds(next);
            onValue(next);
        };

        input.addEventListener('input', () => onValue(parseSeconds(input.value)));
        input.addEventListener('blur', () => { input.value = fmtSeconds(input.value); });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault();
                stepBy(e.key === 'ArrowUp' ? 1 : -1);
            }
        });
        input.parentElement.querySelectorAll('.sec-step').forEach(btn => {
            btn.addEventListener('mousedown', (e) => e.preventDefault()); // keep caret/focus
            btn.addEventListener('click', (e) => {
                e.stopPropagation();   // do not trigger card/row click
                stepBy(parseInt(btn.dataset.dir, 10));
            });
        });
    }

    function renderSpnCards() {
        const filtered = getFilteredSpns();

        if (filtered.length === 0) {
            spnCardsContainer.innerHTML = `<div class="empty-loading-state"><p>No matching SPNs found for "${state.searchQuery}".</p></div>`;
            return;
        }

        spnCardsContainer.innerHTML = '';
        filtered.forEach(s => {
            const isSelected = state.selectedSpns.has(s.spn);
            const cfg = state.spnConfigs.get(s.spn);

            const card = document.createElement('div');
            card.className = `spn-card ${isSelected ? 'selected' : ''}`;
            card.dataset.spn = s.spn;

            card.innerHTML = `
                <div class="spn-top-row">
                    <div class="spn-identity">
                        <span class="spn-tag">SPN ${s.spn}</span>
                        <span class="pgn-tag">PGN ${s.pgn}</span>
                    </div>
                    <div class="spn-toggle-wrap">
                        <input type="checkbox" class="spn-checkbox" ${isSelected ? 'checked' : ''} data-spn="${s.spn}">
                    </div>
                </div>
                <div class="spn-title-row">
                    <h4 class="spn-name">${s.name}</h4>
                    <span class="spn-unit-info">Range: [${s.min_physical} to ${s.max_physical} ${s.unit}] &bull; Res: ${s.resolution}</span>
                </div>
                <div class="spn-pattern-config">
                    <div class="pattern-select-row">
                        <label>Waveform:</label>
                        <select class="neu-select pattern-dropdown" data-spn="${s.spn}">
                            <option value="2" ${cfg.type === 2 ? 'selected' : ''}>∿ Sine Wave</option>
                            <option value="1" ${cfg.type === 1 ? 'selected' : ''}>📈 Ramp (Sawtooth)</option>
                            <option value="3" ${cfg.type === 3 ? 'selected' : ''}>🔺 Triangle Wave</option>
                            <option value="4" ${cfg.type === 4 ? 'selected' : ''}>🪜 Step Function</option>
                            <option value="5" ${cfg.type === 5 ? 'selected' : ''}>⎍ Square Wave</option>
                            <option value="6" ${cfg.type === 6 ? 'selected' : ''}>〰 Pure Random Signal</option>
                            <option value="0" ${cfg.type === 0 ? 'selected' : ''}>━ Constant Value</option>
                        </select>
                    </div>
                    <div class="pattern-params-grid">
                        <div class="param-cell">
                            <span>Min (${s.unit || '-'})</span>
                            <input type="number" step="any" class="neu-input cfg-min" value="${cfg.min}" data-spn="${s.spn}">
                        </div>
                        <div class="param-cell">
                            <span>Max (${s.unit || '-'})</span>
                            <input type="number" step="any" class="neu-input cfg-max" value="${cfg.max}" data-spn="${s.spn}">
                        </div>
                        <div class="param-cell">
                            <span class="param1-label">${getParam1Label(cfg.type)}</span>
                            <input type="number" step="any" class="neu-input cfg-param1" value="${cfg.param1}" data-spn="${s.spn}">
                        </div>
                    </div>
                    <div class="spn-timeframe-grid">
                        <div class="timeframe-input-cell">
                            <span>Timeframe Cycle</span>
                            <select class="neu-select cfg-timeframe" data-spn="${s.spn}">
                                <option value="10" ${cfg.timeframe_ms === 10 ? 'selected' : ''}>10ms (100Hz)</option>
                                <option value="20" ${cfg.timeframe_ms === 20 ? 'selected' : ''}>20ms (50Hz - Smooth)</option>
                                <option value="50" ${cfg.timeframe_ms === 50 ? 'selected' : ''}>50ms (20Hz)</option>
                                <option value="100" ${cfg.timeframe_ms === 100 ? 'selected' : ''}>100ms (10Hz)</option>
                                <option value="500" ${cfg.timeframe_ms === 500 ? 'selected' : ''}>500ms (2Hz)</option>
                                <option value="1000" ${cfg.timeframe_ms === 1000 ? 'selected' : ''}>1000ms (1Hz - SAE)</option>
                            </select>
                        </div>
                        <div class="timeframe-input-cell">
                            <span>Start (s)</span>
                            ${secondsStepperHtml('neu-input cfg-t-start', cfg.t_start, s.spn, 'Execution start window in seconds')}
                        </div>
                        <div class="timeframe-input-cell">
                            <span>Dur (s)</span>
                            ${secondsStepperHtml('neu-input cfg-t-dur', cfg.t_dur, s.spn, 'Execution duration in seconds (0 = Continuous)')}
                        </div>
                    </div>
                </div>
            `;

            // Card click focuses visualizer
            card.addEventListener('click', (e) => {
                if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') {
                    focusVisualizer(s.spn);
                }
            });

            // Checkbox change
            const chk = card.querySelector('.spn-checkbox');
            chk.addEventListener('change', (e) => {
                if (e.target.checked) {
                    state.selectedSpns.add(s.spn);
                    card.classList.add('selected');
                } else {
                    state.selectedSpns.delete(s.spn);
                    card.classList.remove('selected');
                }
                updateSelectionCount();
                updateBusLoadMeter();
                focusVisualizer(s.spn);
            });

            function autoSelectSpn() {
                if (!state.selectedSpns.has(s.spn)) {
                    state.selectedSpns.add(s.spn);
                    chk.checked = true;
                    card.classList.add('selected');
                    updateSelectionCount();
                    updateBusLoadMeter();
                }
            }

            // Waveform type change
            const pDropdown = card.querySelector('.pattern-dropdown');
            pDropdown.addEventListener('change', (e) => {
                const newType = parseInt(e.target.value);
                const currentCfg = state.spnConfigs.get(s.spn);
                currentCfg.type = newType;
                card.querySelector('.param1-label').textContent = getParam1Label(newType);
                autoSelectSpn();
                focusVisualizer(s.spn);
            });

            // Param inputs
            const minIn = card.querySelector('.cfg-min');
            const maxIn = card.querySelector('.cfg-max');
            const param1In = card.querySelector('.cfg-param1');
            const tfIn = card.querySelector('.cfg-timeframe');
            const tStartIn = card.querySelector('.cfg-t-start');
            const tDurIn = card.querySelector('.cfg-t-dur');

            minIn.addEventListener('input', (e) => {
                const val = parseFloat(e.target.value);
                state.spnConfigs.get(s.spn).min = isNaN(val) ? 0 : val;
                autoSelectSpn();
                if (state.activeVisualizerSpn === s.spn) updateVisualizerStats(s.spn);
            });
            maxIn.addEventListener('input', (e) => {
                const val = parseFloat(e.target.value);
                state.spnConfigs.get(s.spn).max = isNaN(val) ? 100 : val;
                autoSelectSpn();
                if (state.activeVisualizerSpn === s.spn) updateVisualizerStats(s.spn);
            });
            param1In.addEventListener('input', (e) => {
                const val = parseFloat(e.target.value);
                state.spnConfigs.get(s.spn).param1 = isNaN(val) ? 10 : val;
                autoSelectSpn();
                if (state.activeVisualizerSpn === s.spn) updateVisualizerStats(s.spn);
            });
            tfIn.addEventListener('change', (e) => {
                const val = parseInt(e.target.value);
                state.spnConfigs.get(s.spn).timeframe_ms = isNaN(val) ? 20 : val;
                autoSelectSpn();
                updateBusLoadMeter();
                if (state.activeVisualizerSpn === s.spn) updateVisualizerStats(s.spn);
            });
            bindSecondsInput(tStartIn, (val) => {
                state.spnConfigs.get(s.spn).t_start = val;
                autoSelectSpn();
            });
            bindSecondsInput(tDurIn, (val) => {
                state.spnConfigs.get(s.spn).t_dur = val;
                autoSelectSpn();
            });

            spnCardsContainer.appendChild(card);
        });
    }

    // =========================================================================
    // RENDER PRO TIMEFRAME MATRIX (VIEW 2 - DENSE ENGINEERING TABLE)
    // =========================================================================
    function renderMatrixTable() {
        const matrixBody = document.getElementById('matrixTableBody');
        if (!matrixBody) return;

        const filtered = getFilteredSpns();
        if (filtered.length === 0) {
            matrixBody.innerHTML = `<tr><td colspan="12" style="text-align:center; padding: 24px; color: #64748b;">No matching SPNs found for "${state.searchQuery}".</td></tr>`;
            return;
        }

        matrixBody.innerHTML = '';
        filtered.forEach(s => {
            const isSelected = state.selectedSpns.has(s.spn);
            const cfg = state.spnConfigs.get(s.spn);
            const tr = document.createElement('tr');
            if (isSelected) tr.classList.add('row-selected');

            const rateHz = cfg.timeframe_ms > 0 ? (1000 / cfg.timeframe_ms).toFixed(0) + ' Hz' : '50 Hz';

            tr.innerHTML = `
                <td><input type="checkbox" class="matrix-chk" data-spn="${s.spn}" ${isSelected ? 'checked' : ''}></td>
                <td><span class="matrix-spn-badge">${s.spn}</span></td>
                <td><span class="matrix-pgn-badge">${s.pgn}</span></td>
                <td>
                    <span class="matrix-sig-name" title="${s.name}">${s.name}</span>
                    <span class="matrix-sig-unit">${s.unit || '-'}</span>
                </td>
                <td>
                    <select class="matrix-select matrix-type" data-spn="${s.spn}">
                        <option value="2" ${cfg.type === 2 ? 'selected' : ''}>∿ Sine Wave</option>
                        <option value="1" ${cfg.type === 1 ? 'selected' : ''}>📈 Ramp (Sawtooth)</option>
                        <option value="3" ${cfg.type === 3 ? 'selected' : ''}>🔺 Triangle</option>
                        <option value="4" ${cfg.type === 4 ? 'selected' : ''}>🪜 Step Staircase</option>
                        <option value="5" ${cfg.type === 5 ? 'selected' : ''}>⎍ Square Pulse</option>
                        <option value="6" ${cfg.type === 6 ? 'selected' : ''}>〰 Pure Random Signal</option>
                        <option value="0" ${cfg.type === 0 ? 'selected' : ''}>━ Constant Flat</option>
                    </select>
                </td>
                <td><input type="number" step="any" class="matrix-input matrix-min" value="${cfg.min}" data-spn="${s.spn}"></td>
                <td><input type="number" step="any" class="matrix-input matrix-max" value="${cfg.max}" data-spn="${s.spn}"></td>
                <td><input type="number" step="any" class="matrix-input matrix-param1" value="${cfg.param1}" data-spn="${s.spn}"></td>
                <td>
                    <select class="matrix-select matrix-timeframe" data-spn="${s.spn}">
                        <option value="10" ${cfg.timeframe_ms === 10 ? 'selected' : ''}>10ms (100Hz)</option>
                        <option value="20" ${cfg.timeframe_ms === 20 ? 'selected' : ''}>20ms (50Hz - Smooth)</option>
                        <option value="50" ${cfg.timeframe_ms === 50 ? 'selected' : ''}>50ms (20Hz)</option>
                        <option value="100" ${cfg.timeframe_ms === 100 ? 'selected' : ''}>100ms (10Hz)</option>
                        <option value="500" ${cfg.timeframe_ms === 500 ? 'selected' : ''}>500ms (2Hz)</option>
                        <option value="1000" ${cfg.timeframe_ms === 1000 ? 'selected' : ''}>1000ms (1Hz - SAE)</option>
                    </select>
                </td>
                <td>${secondsStepperHtml('matrix-input matrix-tstart', cfg.t_start, s.spn, 'Start (s)')}</td>
                <td>${secondsStepperHtml('matrix-input matrix-tdur', cfg.t_dur, s.spn, 'Duration (s), 0 = continuous')}</td>
                <td><span class="matrix-rate-pill">${rateHz}</span></td>
            `;

            const chk = tr.querySelector('.matrix-chk');
            chk.addEventListener('change', (e) => {
                if (e.target.checked) {
                    state.selectedSpns.add(s.spn);
                    tr.classList.add('row-selected');
                } else {
                    state.selectedSpns.delete(s.spn);
                    tr.classList.remove('row-selected');
                }
                updateSelectionCount();
                updateBusLoadMeter();
                focusVisualizer(s.spn);
            });

            function autoSelectRow() {
                if (!state.selectedSpns.has(s.spn)) {
                    state.selectedSpns.add(s.spn);
                    chk.checked = true;
                    tr.classList.add('row-selected');
                    updateSelectionCount();
                    updateBusLoadMeter();
                }
            }

            tr.querySelector('.matrix-type').addEventListener('change', (e) => {
                cfg.type = parseInt(e.target.value);
                autoSelectRow();
                focusVisualizer(s.spn);
            });
            tr.querySelector('.matrix-min').addEventListener('input', (e) => {
                cfg.min = parseFloat(e.target.value) || 0;
                autoSelectRow();
            });
            tr.querySelector('.matrix-max').addEventListener('input', (e) => {
                cfg.max = parseFloat(e.target.value) || 100;
                autoSelectRow();
            });
            tr.querySelector('.matrix-param1').addEventListener('input', (e) => {
                cfg.param1 = parseFloat(e.target.value) || 10;
                autoSelectRow();
            });
            tr.querySelector('.matrix-timeframe').addEventListener('change', (e) => {
                cfg.timeframe_ms = parseInt(e.target.value) || 20;
                tr.querySelector('.matrix-rate-pill').textContent = (1000 / cfg.timeframe_ms).toFixed(0) + ' Hz';
                autoSelectRow();
                updateBusLoadMeter();
            });
            bindSecondsInput(tr.querySelector('.matrix-tstart'), (val) => {
                cfg.t_start = val;
                autoSelectRow();
            });
            bindSecondsInput(tr.querySelector('.matrix-tdur'), (val) => {
                cfg.t_dur = val;
                autoSelectRow();
            });

            tr.addEventListener('click', (e) => {
                if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') {
                    focusVisualizer(s.spn);
                }
            });

            matrixBody.appendChild(tr);
        });
    }

    // =========================================================================
    // TSMASTER & PCAN-VIEW CAN BUS ANALYZER & DATA FIELD INSPECTOR (VIEW 3)
    // =========================================================================
    const PGN_METADATA = {
        61444: { acronym: 'EEC1', name: 'Electronic Engine Controller 1', prio: 3, sa: 0, defaultCycle: 20 },
        61443: { acronym: 'EEC2', name: 'Electronic Engine Controller 2', prio: 3, sa: 0, defaultCycle: 50 },
        65265: { acronym: 'CCVS1', name: 'Cruise Control / Vehicle Speed 1', prio: 6, sa: 0, defaultCycle: 100 },
        61441: { acronym: 'EBC1', name: 'Electronic Brake Controller 1', prio: 6, sa: 11, defaultCycle: 100 },
        61445: { acronym: 'ETC2', name: 'Electronic Transmission Controller 2', prio: 3, sa: 3, defaultCycle: 100 },
        65262: { acronym: 'ET1', name: 'Engine Temperature 1', prio: 6, sa: 0, defaultCycle: 1000 },
        65263: { acronym: 'EFL_P1', name: 'Engine Fluid Level / Pressure 1', prio: 6, sa: 0, defaultCycle: 500 },
        65266: { acronym: 'LFE', name: 'Fuel Economy (Liquid)', prio: 6, sa: 0, defaultCycle: 100 },
        65271: { acronym: 'VEP', name: 'Vehicle Electrical Power', prio: 6, sa: 0, defaultCycle: 1000 },
        65276: { acronym: 'DD', name: 'Dash Display', prio: 6, sa: 0, defaultCycle: 1000 },
        65269: { acronym: 'AMB', name: 'Ambient Conditions', prio: 6, sa: 0, defaultCycle: 1000 },
        65270: { acronym: 'IC1', name: 'Inlet / Exhaust Conditions 1', prio: 6, sa: 0, defaultCycle: 500 },
        65272: { acronym: 'TF', name: 'Transmission Fluids', prio: 6, sa: 3, defaultCycle: 1000 },
        65274: { acronym: 'AIR1', name: 'Brake Application Air Pressure', prio: 6, sa: 11, defaultCycle: 1000 },
        65279: { acronym: 'WFI', name: 'Water in Fuel Indicator', prio: 6, sa: 0, defaultCycle: 10000 },
        64947: { acronym: 'DPFT1', name: 'DPF Temperature 1', prio: 6, sa: 0, defaultCycle: 100 },
        61475: { acronym: 'AT1S', name: 'Aftertreatment 1 SCR Dosing', prio: 6, sa: 0, defaultCycle: 20 },
        65110: { acronym: 'AT1T', name: 'Aftertreatment 1 SCR Tank', prio: 6, sa: 0, defaultCycle: 1000 },
        65190: { acronym: 'T1', name: 'Turbocharger 1', prio: 6, sa: 0, defaultCycle: 100 },
        65213: { acronym: 'FD', name: 'Fan Drive', prio: 6, sa: 0, defaultCycle: 1000 },
        61440: { acronym: 'ERC1', name: 'Electronic Retarder Controller 1', prio: 6, sa: 15, defaultCycle: 100 },
        61449: { acronym: 'VDS', name: 'Vehicle Dynamic Stability', prio: 6, sa: 11, defaultCycle: 100 },
        65254: { acronym: 'TD', name: 'Time / Date', prio: 6, sa: 0, defaultCycle: 1000 }
    };

    const SPN_COLOR_PALETTE = [
        { stroke: '#38bdf8', bg: 'rgba(56, 189, 248, 0.15)', class: 'spn-col-0' },
        { stroke: '#10b981', bg: 'rgba(16, 185, 129, 0.15)', class: 'spn-col-1' },
        { stroke: '#f59e0b', bg: 'rgba(245, 158, 11, 0.15)', class: 'spn-col-2' },
        { stroke: '#a855f7', bg: 'rgba(168, 85, 247, 0.15)', class: 'spn-col-3' },
        { stroke: '#ec4899', bg: 'rgba(236, 72, 153, 0.15)', class: 'spn-col-4' },
        { stroke: '#14b8a6', bg: 'rgba(20, 184, 166, 0.15)', class: 'spn-col-5' }
    ];

    let pcanSimulationStartTime = Date.now();
    let pcanLastPgnTxTimes = new Map();
    let pcanTotalFramesSent = 0;
    let pcanLastFpsCalcTime = Date.now();
    let pcanFpsCounter = 0;
    let pcanTraceTimerId = null;
    let isGeneratorRunning = true; // Auto-active preview mode by default

    function getPatternStartValue(cfg) {
        switch (cfg.type) {
            case 1: // Ramp
            case 3: // Triangle
            case 4: // Step
                return cfg.min;
            case 5: // Square
                return cfg.max;
            case 2: // Sine
            case 6: // Noise
                return (cfg.min + cfg.max) * 0.5;
            case 0: // Constant
            default:
                return (cfg.param1 >= cfg.min && cfg.param1 <= cfg.max) ? cfg.param1 : (cfg.min + cfg.max) * 0.5;
        }
    }

    /**
     * Compute instantaneous physical signal value:
     * - Startup 3-second lead-in: smoothly ramps from 0.0 to pattern starting value
     * - Continuous loop mode (t >= 3.0s): seamless loop of active pattern with zero glitches/constants
     * - Stopped/ended mode: returns 0.0 cleanly
     */
    function calcSpnPhysicalValue(spn, elapsedSec, isRunning) {
        const cfg = state.spnConfigs.get(spn) || { type: 2, min: 0, max: 100, param1: 10 };
        if (!isRunning) {
            return 0.0;
        }

        const STARTUP_SEC = 5.0;
        const startVal = getPatternStartValue(cfg);
        const range = (cfg.max > cfg.min) ? (cfg.max - cfg.min) : 100.0;

        if (elapsedSec < STARTUP_SEC) {
            /* Pure 0.0 constant for 5 seconds lead-in */
            return 0.0;
        }

        const tActive = elapsedSec - STARTUP_SEC;
        const period = (cfg.param1 > 0.1) ? cfg.param1 : 10.0;

        switch (cfg.type) {
            case 1: { // Ramp linear sawtooth sweep
                const phase = (tActive % period) / period;
                return cfg.min + phase * range;
            }
            case 2: { // Pure continuous Sine wave
                const phase = 2.0 * Math.PI * (tActive % period) / period;
                return (cfg.min + cfg.max) * 0.5 + (range * 0.5) * Math.sin(phase);
            }
            case 3: { // Triangle wave (symmetric linear 0 -> 1 -> 0)
                const phase = (tActive % period) / period;
                const u = (phase < 0.5) ? (phase * 2.0) : (2.0 - phase * 2.0);
                return cfg.min + u * range;
            }
            case 4: { // Step function (discrete staircase plateaus)
                const steps = (cfg.param1 >= 2 && cfg.param1 <= 50) ? Math.floor(cfg.param1) : 8;
                const phase = (tActive % period) / period;
                const stepIdx = Math.min(steps - 1, Math.floor(phase * steps));
                return cfg.min + (stepIdx / (steps - 1)) * range;
            }
            case 5: { // Square wave (50% duty cycle)
                const phase = (tActive % period) / period;
                return (phase < 0.5) ? cfg.max : cfg.min;
            }
            case 6: { // Pure stochastic random signal (white noise)
                const seedTime = Math.floor(tActive * 80);
                let hVal = ((spn * 2654435761) ^ (seedTime * 1664525 + 1013904223)) >>> 0;
                hVal ^= hVal << 13; hVal ^= hVal >>> 17; hVal ^= hVal << 5;
                const rnd = (hVal >>> 0) / 4294967296.0;
                return cfg.min + rnd * range;
            }
            case 0: // Constant
            default: {
                if (cfg.param1 >= cfg.min && cfg.param1 <= cfg.max) return cfg.param1;
                return (cfg.min + cfg.max) * 0.5;
            }
        }
    }

    function formatCanId29Bit(prio, pgn, sa) {
        const id = ((prio & 0x07) << 26) | ((pgn & 0x1FFFF) << 8) | (sa & 0xFF);
        return '0x' + (id >>> 0).toString(16).toUpperCase().padStart(8, '0');
    }

    function encodePgnDataField(pgn, elapsedSec, isRunning) {
        const payload = new Uint8Array([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]);
        const signals = state.spns.filter(s => s.pgn === pgn);
        const signalDetails = [];

        signals.forEach((s, idx) => {
            const isSelected = state.selectedSpns.has(s.spn);
            const physVal = calcSpnPhysicalValue(s.spn, elapsedSec, isRunning && isSelected);
            const clampedPhys = Math.max(s.min_physical, Math.min(s.max_physical, physVal));
            
            // Raw integer calculation: Raw = (Phys - Offset) / Resolution
            let rawInt = Math.round((clampedPhys - s.offset) / s.resolution);
            const maxRaw = (s.num_bits >= 32) ? 0xFFFFFFFF : ((1 << s.num_bits) - 1);
            rawInt = Math.max(0, Math.min(maxRaw, rawInt));

            // Intel (little-endian) bit packaging
            const startBitAbs = (s.start_byte - 1) * 8 + (s.start_bit - 1);
            for (let b = 0; b < s.num_bits; b++) {
                const bitVal = (s.num_bits > 30) 
                    ? Number((BigInt(rawInt) >> BigInt(b)) & 1n) 
                    : ((rawInt >> b) & 1);
                const absPos = startBitAbs + b;
                const byteIdx = Math.floor(absPos / 8);
                const bitInByte = absPos % 8;
                if (byteIdx < 8) {
                    if (bitVal) {
                        payload[byteIdx] |= (1 << bitInByte);
                    } else {
                        payload[byteIdx] &= ~(1 << bitInByte);
                    }
                }
            }

            signalDetails.push({
                spn: s.spn,
                name: s.name,
                unit: s.unit,
                physVal: clampedPhys,
                rawInt: rawInt,
                start_byte: s.start_byte,
                start_bit: s.start_bit,
                num_bits: s.num_bits,
                resolution: s.resolution,
                offset: s.offset,
                min_physical: s.min_physical,
                max_physical: s.max_physical,
                color: SPN_COLOR_PALETTE[idx % SPN_COLOR_PALETTE.length],
                isSelected: isSelected
            });
        });

        return { payload, signalDetails };
    }

    function initPcanAnalyzer() {
        populatePcanFilterOptions();
        setupPcanTraceControls();
        startPcanSimulationLoop();
    }

    function populatePcanFilterOptions() {
        const filterSelect = document.getElementById('pcanPgnFilterSelect');
        if (!filterSelect) return;

        const uniquePgns = new Set(state.spns.map(s => s.pgn));
        const sortedPgns = Array.from(uniquePgns).sort((a, b) => a - b);

        filterSelect.innerHTML = '<option value="all">All simulated PGNs</option>';
        sortedPgns.forEach(pgn => {
            const meta = PGN_METADATA[pgn] || { acronym: `PGN_${pgn}`, name: `J1939 PGN ${pgn}` };
            const opt = document.createElement('option');
            opt.value = pgn;
            opt.textContent = `PGN ${pgn} (${meta.acronym} - ${meta.name})`;
            filterSelect.appendChild(opt);
        });

        filterSelect.addEventListener('change', (e) => {
            state.pcanPgnFilter = e.target.value;
            const filterVal = e.target.value;
            const rows = document.querySelectorAll('#pcanTraceBody tr');
            rows.forEach(row => {
                if (filterVal === 'all' || row.dataset.pgn === filterVal) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
        });
    }

    function setupPcanTraceControls() {
        const pauseBtn = document.getElementById('pcanPauseTraceBtn');
        const clearBtn = document.getElementById('pcanClearTraceBtn');
        const autoScrollBtn = document.getElementById('pcanAutoScrollBtn');

        if (pauseBtn) {
            pauseBtn.addEventListener('click', () => {
                state.pcanTracePaused = !state.pcanTracePaused;
                const icon = document.getElementById('pcanPauseIcon');
                const text = document.getElementById('pcanPauseText');
                if (state.pcanTracePaused) {
                    if (icon) icon.textContent = '▶';
                    if (text) text.textContent = 'Resume Trace';
                    pauseBtn.classList.add('active');
                } else {
                    if (icon) icon.textContent = '⏸';
                    if (text) text.textContent = 'Pause Trace';
                    pauseBtn.classList.remove('active');
                }
            });
        }

        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                const tbody = document.getElementById('pcanTraceBody');
                if (tbody) tbody.innerHTML = '';
                pcanTotalFramesSent = 0;
                const counterBadge = document.getElementById('pcanFrameCounterBadge');
                if (counterBadge) counterBadge.textContent = 'Total Frames: 0';
            });
        }

        if (autoScrollBtn) {
            autoScrollBtn.addEventListener('click', () => {
                state.pcanAutoScroll = !state.pcanAutoScroll;
                autoScrollBtn.classList.toggle('active', state.pcanAutoScroll);
            });
        }
    }

    function startPcanSimulationLoop() {
        if (pcanTraceTimerId) clearInterval(pcanTraceTimerId);

        pcanTraceTimerId = setInterval(() => {
            const now = Date.now();
            const elapsedSec = (now - pcanSimulationStartTime) / 1000.0;
            const activePgns = new Set();

            state.selectedSpns.forEach(spn => {
                const s = state.spns.find(item => item.spn === spn);
                if (s) activePgns.add(s.pgn);
            });

            // Default to EEC1, CCVS1, EEC2 if no signals selected
            if (activePgns.size === 0) {
                [61444, 65265, 61443].forEach(p => activePgns.add(p));
            }

            activePgns.forEach(pgn => {
                const meta = PGN_METADATA[pgn] || { acronym: `PGN_${pgn}`, name: 'J1939 Parameter Group', prio: 6, sa: 0, defaultCycle: 50 };
                const cycleMs = (state.timingMode === 2) ? 10 : (meta.defaultCycle || 50);
                const lastTx = pcanLastPgnTxTimes.get(pgn) || 0;

                if (now - lastTx >= cycleMs) {
                    const dtMs = lastTx === 0 ? cycleMs : (now - lastTx);
                    pcanLastPgnTxTimes.set(pgn, now);

                    const encoded = encodePgnDataField(pgn, elapsedSec, isGeneratorRunning);
                    const canIdHex = formatCanId29Bit(meta.prio, pgn, meta.sa);

                    pcanTotalFramesSent++;
                    pcanFpsCounter++;

                    const frameData = {
                        index: pcanTotalFramesSent,
                        timeMs: elapsedSec * 1000.0,
                        dtMs: dtMs,
                        canIdHex: canIdHex,
                        pgn: pgn,
                        pgnAcronym: meta.acronym,
                        pgnFullName: meta.name,
                        prio: meta.prio,
                        sa: meta.sa,
                        cycleMs: cycleMs,
                        payload: Array.from(encoded.payload),
                        signalDetails: encoded.signalDetails
                    };

                    if (!state.pcanTracePaused) {
                        appendPcanTraceRow(frameData);
                    }

                    // Real-Time .TXT Log Continuous Recording
                    if (state.isRecordingTxtLog) {
                        const payloadHexStr = Array.from(encoded.payload).map(b => b.toString(16).padStart(2, '0').toUpperCase()).join(' ');
                        const sigStr = encoded.signalDetails.map(s => `SPN ${s.spn} ${s.name}: ${s.physVal.toFixed(2)} ${s.unit}`).join(', ');
                        const logLine = `  ${String(pcanTotalFramesSent).padStart(6, ' ')}  ${(elapsedSec * 1000.0).toFixed(3).padStart(10, ' ')}  Tx  ${canIdHex}  ${String(pgn).padStart(5, ' ')}  8  ${payloadHexStr}  [${sigStr}]`;
                        state.recordedTxtLines.push(logLine);

                        const recStatusPill = document.getElementById('recStatusPill');
                        const recStatusText = document.getElementById('recStatusText');
                        if (recStatusPill && recStatusText) {
                            recStatusPill.classList.remove('hidden');
                            const recElapsedSec = (now - state.recordingStartTime) / 1000.0;
                            recStatusText.textContent = `SIMULATED PREVIEW: ${state.recordedTxtLines.length} lines (${recElapsedSec.toFixed(1)}s)`;
                        }
                    }

                    // Keep inspector synchronized with currently selected PGN
                    if (state.pcanSelectedPgn === pgn || !state.pcanSelectedPgn) {
                        renderPcanInspector(frameData);
                    }
                }
            });

            // FPS counter update (every 500ms)
            if (now - pcanLastFpsCalcTime >= 500) {
                const fps = Math.round((pcanFpsCounter / (now - pcanLastFpsCalcTime)) * 1000);
                const fpsBadge = document.getElementById('pcanFpsBadge');
                if (fpsBadge) fpsBadge.textContent = `Rate: ${fps} fps`;
                pcanFpsCounter = 0;
                pcanLastFpsCalcTime = now;
            }

            const counterBadge = document.getElementById('pcanFrameCounterBadge');
            if (counterBadge) counterBadge.textContent = `Total Frames: ${pcanTotalFramesSent}`;
        }, 10);
    }

    function appendPcanTraceRow(frame) {
        const tbody = document.getElementById('pcanTraceBody');
        const wrap = document.getElementById('pcanTraceTableWrap');
        if (!tbody) return;

        const isFilteredOut = (state.pcanPgnFilter !== 'all' && String(frame.pgn) !== state.pcanPgnFilter);
        const tr = document.createElement('tr');
        tr.dataset.pgn = frame.pgn;
        if (state.pcanSelectedPgn === frame.pgn) tr.classList.add('active-inspect');
        if (isFilteredOut) tr.style.display = 'none';

        const bytesHtml = frame.payload.map((b) => {
            const hex = b.toString(16).padStart(2, '0').toUpperCase();
            return `<span class="hex-byte-chip">${hex}</span>`;
        }).join(' ');

        tr.innerHTML = `
            <td class="pcan-row-seq">${frame.index}</td>
            <td class="pcan-row-time">${frame.timeMs.toFixed(1)}</td>
            <td class="pcan-row-dt">+${frame.dtMs.toFixed(1)}</td>
            <td class="pcan-row-id">${frame.canIdHex}</td>
            <td class="pcan-row-pgn">${frame.pgn}</td>
            <td class="pcan-row-name" title="${frame.pgnFullName}">${frame.pgnAcronym}</td>
            <td class="pcan-row-dir">Tx</td>
            <td class="pcan-row-dlc">8</td>
            <td class="pcan-row-bytes">${bytesHtml}</td>
        `;

        tr.addEventListener('click', () => {
            document.querySelectorAll('#pcanTraceBody tr').forEach(r => r.classList.remove('active-inspect'));
            tr.classList.add('active-inspect');
            state.pcanSelectedPgn = frame.pgn;
            renderPcanInspector(frame);
        });

        tbody.appendChild(tr);

        // Cap table rows at 120 for smooth 60 FPS performance without memory bloat
        if (tbody.children.length > 120) {
            tbody.removeChild(tbody.firstElementChild);
        }

        if (state.pcanAutoScroll && wrap) {
            wrap.scrollTop = wrap.scrollHeight;
        }
    }

    function renderPcanInspector(frame) {
        state.pcanSelectedPgn = frame.pgn;

        // Header Badges
        const prioBadge = document.getElementById('insPrioBadge');
        const pgnBadge = document.getElementById('insPgnBadge');
        const saBadge = document.getElementById('insSaBadge');
        const cycleBadge = document.getElementById('insCycleBadge');
        const titleEl = document.getElementById('insPgnTitle');

        if (prioBadge) prioBadge.textContent = `PRIO: ${frame.prio}`;
        if (pgnBadge) pgnBadge.textContent = `PGN ${frame.pgn} (0x${frame.pgn.toString(16).toUpperCase()})`;
        if (saBadge) saBadge.textContent = `SA: 0x${frame.sa.toString(16).padStart(2, '0').toUpperCase()}`;
        if (cycleBadge) cycleBadge.textContent = `Cycle: ${frame.cycleMs} ms (${(1000/frame.cycleMs).toFixed(0)} Hz)`;
        if (titleEl) titleEl.textContent = `${frame.pgnAcronym} - ${frame.pgnFullName}`;

        // Raw 8-Byte Hex Strip
        const strip = document.getElementById('insRawBytesStrip');
        if (strip) {
            strip.innerHTML = frame.payload.map((byteVal, i) => {
                const hexStr = '0x' + byteVal.toString(16).padStart(2, '0').toUpperCase();
                const binStr = byteVal.toString(2).padStart(8, '0');
                return `
                    <div class="byte-card">
                        <span class="byte-pos-label">BYTE ${i + 1}</span>
                        <span class="byte-hex-val">${hexStr}</span>
                        <span class="byte-bin-val">${binStr}</span>
                    </div>
                `;
            }).join('');
        }

        // 64-Bit Data Field Matrix (TSMaster & PCAN Signature Grid)
        // 8 columns (Bytes 1..8) x 8 rows (Bit 7 down to Bit 0)
        const grid = document.getElementById('bitMatrixGrid');
        if (grid) {
            grid.innerHTML = '';
            for (let bitRow = 7; bitRow >= 0; bitRow--) {
                for (let byteCol = 0; byteCol < 8; byteCol++) {
                    const absBit = byteCol * 8 + bitRow;
                    const byteVal = frame.payload[byteCol];
                    const bitVal = (byteVal >> bitRow) & 1;

                    // Check which SPN occupies this bit
                    let occupyingSpn = null;
                    for (const sig of frame.signalDetails) {
                        const sigStartBit = (sig.start_byte - 1) * 8 + (sig.start_bit - 1);
                        const sigEndBit = sigStartBit + sig.num_bits - 1;
                        if (absBit >= sigStartBit && absBit <= sigEndBit) {
                            occupyingSpn = sig;
                            break;
                        }
                    }

                    const cell = document.createElement('div');
                    cell.className = `bit-cell ${bitVal === 1 ? 'bit-1' : 'bit-0'}`;
                    if (occupyingSpn) {
                        cell.classList.add(occupyingSpn.color.class);
                        cell.title = `Bit ${absBit} (Byte ${byteCol + 1}, Bit ${bitRow})\nSPN ${occupyingSpn.spn}: ${occupyingSpn.name}\nValue: ${bitVal}`;
                    } else {
                        cell.classList.add('spn-col-unassigned');
                        cell.title = `Bit ${absBit} (Byte ${byteCol + 1}, Bit ${bitRow})\nUnassigned (J1939 Default 1)\nValue: ${bitVal}`;
                    }

                    cell.innerHTML = `
                        <span class="bit-cell-num">${absBit}</span>
                        <span class="bit-cell-val">${bitVal}</span>
                    `;
                    grid.appendChild(cell);
                }
            }
        }

        // SPN Physical Signals & Mathematical Formula Breakdown List
        const sigList = document.getElementById('insSignalsList');
        if (sigList) {
            sigList.innerHTML = frame.signalDetails.map(sig => {
                const endByte = sig.start_byte + Math.ceil(sig.num_bits / 8) - 1;
                const byteSpanText = (endByte === sig.start_byte) ? `Byte ${sig.start_byte}` : `Bytes ${sig.start_byte}-${endByte}`;
                const rawHex = '0x' + sig.rawInt.toString(16).toUpperCase();
                return `
                    <div class="ins-signal-card" style="border-left-color: ${sig.color.stroke};">
                        <div class="ins-sig-top">
                            <div class="ins-sig-name-wrap">
                                <span class="ins-spn-pill" style="color: ${sig.color.stroke}; background: ${sig.color.bg};">SPN ${sig.spn}</span>
                                <span class="ins-sig-name">${sig.name}</span>
                            </div>
                            <span class="ins-sig-bitspan">${byteSpanText}, Bits ${sig.num_bits} (Intel)</span>
                        </div>
                        <div class="ins-sig-formula-row">
                            <span class="ins-formula-text">Raw: <strong>${sig.rawInt}</strong> (${rawHex}) &bull; (${sig.rawInt} &times; ${sig.resolution}) + ${sig.offset}</span>
                            <span class="ins-physical-val">${sig.physVal.toFixed(2)} ${sig.unit}</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        // Update Step-by-Step Non-CAN Visual Guide Pipeline (Steps 1 to 5)
        const firstSig = frame.signalDetails && frame.signalDetails[0];
        if (firstSig) {
            const step1 = document.getElementById('expStep1Val');
            const step2 = document.getElementById('expStep2Val');
            const step3 = document.getElementById('expStep3Val');
            const step4 = document.getElementById('expStep4Val');
            const step5 = document.getElementById('expStep5Val');

            if (step1) step1.textContent = `SPN ${firstSig.spn}: ${firstSig.physVal.toFixed(2)} ${firstSig.unit}`;
            if (step2) step2.textContent = `Raw = 0x${firstSig.rawInt.toString(16).toUpperCase()} (${firstSig.rawInt})`;
            if (step3) step3.textContent = `PGN ${frame.pgn} Bytes ${firstSig.start_byte}..${firstSig.start_byte + Math.ceil(firstSig.num_bits / 8) - 1}`;
            if (step4) step4.textContent = `${frame.canIdHex} (Prio:${frame.prio}, SA:0x${frame.sa.toString(16).padStart(2,'0')})`;
            if (step5) step5.textContent = `t = ${frame.timeMs.toFixed(1)} ms (Δt = ${frame.dtMs.toFixed(1)}ms)`;
        }
    }

    // =========================================================================
    // DYNAMIC CAN BUS LOAD CALCULATION
    // =========================================================================
    function updateBusLoadMeter() {
        const activeSignals = Array.from(state.selectedSpns);
        const fill = document.getElementById('loadBarFill');
        const text = document.getElementById('loadPercentText');
        const tag = document.getElementById('loadStatusTag');
        const stat = document.getElementById('statBusLoad');

        if (activeSignals.length === 0) {
            if (fill) fill.style.width = '0%';
            if (text) text.textContent = '0.0%';
            if (stat) stat.textContent = '0.0%';
            if (tag) {
                tag.className = 'load-status normal';
                tag.textContent = 'IDLE';
            }
            return;
        }

        const pgnTimeframes = new Map();
        activeSignals.forEach(spn => {
            const s = state.spns.find(item => item.spn === spn);
            if (!s) return;
            const cfg = state.spnConfigs.get(spn) || { timeframe_ms: 20 };
            const tf = cfg.timeframe_ms > 0 ? cfg.timeframe_ms : 20;
            const pgn = s.pgn;
            if (!pgnTimeframes.has(pgn) || tf < pgnTimeframes.get(pgn)) {
                pgnTimeframes.set(pgn, tf);
            }
        });

        let totalFramesPerSec = 0;
        pgnTimeframes.forEach((tf_ms) => {
            totalFramesPerSec += (1000.0 / tf_ms);
        });

        const baudBitsPerSec = (state.canBaudKbps || 500) * 1000.0;
        const busLoadPercent = Math.min(100.0, (totalFramesPerSec * 128.0) / baudBitsPerSec * 100.0);

        if (text) text.textContent = `${busLoadPercent.toFixed(1)}%`;
        if (stat) stat.textContent = `${busLoadPercent.toFixed(1)}%`;

        if (fill) {
            fill.style.width = `${Math.max(4, Math.min(100, busLoadPercent))}%`;
            fill.className = busLoadPercent > 70 ? 'load-bar-fill heavy' : 'load-bar-fill';
        }

        if (tag) {
            if (busLoadPercent > 75) {
                tag.className = 'load-status danger';
                tag.textContent = 'HIGH LOAD';
            } else if (busLoadPercent > 40) {
                tag.className = 'load-status heavy';
                tag.textContent = 'ELEVATED';
            } else {
                tag.className = 'load-status normal';
                tag.textContent = 'OPTIMAL';
            }
        }
    }

    function getParam1Label(type) {
        switch (type) {
            case 1: return 'Period (s)';
            case 2: return 'Period (s)';
            case 3: return 'Period (s)';
            case 4: return 'Steps (N)';
            case 5: return 'Period (s)';
            case 6: return 'Period (s)';
            case 0: default: return 'Value';
        }
    }

    function updateSelectionCount() {
        selectedCountEl.textContent = state.selectedSpns.size;
    }

    function focusVisualizer(spn) {
        state.activeVisualizerSpn = spn;
        updateVisualizerStats(spn);
    }

    function updateVisualizerStats(spn) {
        const s = state.spns.find(item => item.spn === spn);
        if (!s) return;
        const cfg = state.spnConfigs.get(spn) || { min: 0, max: 100, type: 2, param1: 10, timeframe_ms: 20 };
        const typeNames = ['Constant', 'Ramp (Sawtooth)', 'Sine Wave', 'Triangle Wave', 'Step Function', 'Square Wave', 'Pure Random Signal', 'State Sequence'];
        const tfText = cfg.timeframe_ms ? `${cfg.timeframe_ms}ms (${(1000/cfg.timeframe_ms).toFixed(0)} Hz)` : '20ms';
        activeWaveformLabel.innerHTML = `SPN ${s.spn} (${s.name}) &bull; <strong>${typeNames[cfg.type] || 'Waveform'}</strong> [Cycle: ${tfText}]`;
        canvasMinBadge.textContent = `Min: ${cfg.min} ${s.unit}`;
        canvasMaxBadge.textContent = `Max: ${cfg.max} ${s.unit}`;
    }

    // =========================================================================
    // WAVEFORM CANVAS ANIMATION
    // =========================================================================
    let animTime = 0;
    function startWaveformAnimation() {
        function renderCanvas() {
            const w = waveformCanvas.width;
            const h = waveformCanvas.height;

            // Background
            ctx.fillStyle = '#090d16';
            ctx.fillRect(0, 0, w, h);

            // Subtle Grid lines
            ctx.strokeStyle = 'rgba(51, 65, 85, 0.4)';
            ctx.lineWidth = 1;
            for (let x = 0; x < w; x += 40) {
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, h);
                ctx.stroke();
            }
            for (let y = 0; y < h; y += 35) {
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(w, y);
                ctx.stroke();
            }

            const cfg = state.spnConfigs.get(state.activeVisualizerSpn) || { type: 2, min: 0, max: 100, param1: 10 };
            const s = state.spns.find(item => item.spn === state.activeVisualizerSpn);
            const unit = s ? s.unit : '';
            const range = (cfg.max > cfg.min) ? (cfg.max - cfg.min) : 100.0;

            // Draw wave path
            ctx.beginPath();
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 2.5;
            ctx.shadowBlur = 10;
            ctx.shadowColor = 'rgba(56, 189, 248, 0.6)';

            const padding = 25;
            const plotH = h - padding * 2;
            const points = [];

            for (let px = 0; px < w; px += 2) {
                const t = (animTime + px * 0.035);
                const physVal = calcSpnPhysicalValue(state.activeVisualizerSpn, t, isGeneratorRunning);
                const normalized = (range > 0) ? Math.max(0, Math.min(1.0, (physVal - cfg.min) / range)) : 0.5;

                const py = h - padding - (normalized * plotH);
                if (px === 0) ctx.moveTo(px, py);
                else ctx.lineTo(px, py);

                if (px === Math.floor(w * 0.75)) {
                    points.push({ x: px, y: py, norm: normalized, val: physVal });
                }
            }
            ctx.stroke();
            ctx.shadowBlur = 0; // reset shadow

            // Draw current active dot
            if (points.length > 0) {
                const pt = points[0];
                ctx.fillStyle = '#10b981';
                ctx.beginPath();
                ctx.arc(pt.x, pt.y, 6, 0, Math.PI * 2);
                ctx.fill();
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 2;
                ctx.stroke();

                canvasCurrentBadge.textContent = `Instant: ${pt.val.toFixed(2)} ${unit}`;
            }

            animTime += 0.03;
            animFrameId = requestAnimationFrame(renderCanvas);
        }
        renderCanvas();
    }


    // =========================================================================
    // TX <-> RX VALIDATION VIEW (data from /api/validation/*)
    // =========================================================================
    let validationTimerId = null;

    function escapeHtml(text) {
        return String(text == null ? '' : text).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
    }

    function fmtNum(v, digits = 2) {
        return (v === null || v === undefined || Number.isNaN(v)) ? '–' : Number(v).toFixed(digits);
    }

    function kpiTile(label, value, hint, tone) {
        return `<div class="val-kpi ${tone || ''}"><span class="k-label">${label}</span>` +
               `<span class="k-value">${value}</span><span class="k-hint">${hint || ''}</span></div>`;
    }

    function renderValidation(s, events) {
        const v = s.verdicts || {};
        const tx = s.tx || {};
        const rx = s.rx || {};
        const jit = s.latency_jitter_ms || {};
        const rate = s.match_rate_pct;

        const lockBadge = document.getElementById('valLockBadge');
        if (lockBadge) {
            lockBadge.textContent = s.clock_locked
                ? `Clock locked · offset ${fmtNum(s.offset_ms, 1)} ms`
                : (tx.logged ? 'Clock: waiting for RX frames…' : 'Clock: waiting for $TX log…');
        }
        const sub = document.getElementById('valSub');
        if (sub && s.run_dir) {
            sub.innerHTML = `STM32 <code>$TX</code> log ↔ TSMaster RX (bridge ${escapeHtml(s.rx_bridge)}) · evidence: <code>${escapeHtml(s.run_dir)}</code>`;
        }

        document.getElementById('valKpis').innerHTML = [
            kpiTile('Match rate', rate === null ? '–' : `${fmtNum(rate, 2)}%`, 'match / (match+lost+mismatch)',
                    rate === null ? '' : (rate >= 99.99 ? 'good' : (rate >= 99 ? 'warn' : 'bad'))),
            kpiTile('Matched', v.match || 0, 'same ID + 8 bytes', 'good'),
            kpiTile('Lost', v.lost || 0, 'logged TX, no RX', v.lost ? 'bad' : ''),
            kpiTile('Mismatch', v.mismatch || 0, 'RX bytes differ', v.mismatch ? 'bad' : ''),
            kpiTile('Unexpected', v.unexpected || 0, 'RX with no TX', v.unexpected ? 'bad' : ''),
            kpiTile('Unlogged', v.unlogged || 0, `UART gap (${tx.log_gap_lines || 0} lines)`, v.unlogged ? 'warn' : ''),
            kpiTile('TX busy / err', `${tx.busy || 0} / ${tx.error || 0}`, 'not queued by bxCAN', (tx.busy || tx.error) ? 'warn' : ''),
            kpiTile('Jitter σ', jit.n ? `${fmtNum(jit.std, 3)} ms` : '–', jit.n ? `range ${fmtNum(jit.min, 2)}..${fmtNum(jit.max, 2)}` : ''),
            kpiTile('Clock drift', s.clock_drift_ppm === null ? '–' : `${s.clock_drift_ppm} ppm`, 'STM32 vs TSMaster'),
            kpiTile('RX total', rx.total || 0, `foreign ${rx.foreign || 0} · waiting ${rx.waiting || 0}`)
        ].join('');

        const periodRows = (s.per_id || []).map(p => {
            const t = p.tx_period_ms || {};
            const r = p.rx_period_ms || {};
            const drift = (t.n && r.n) ? Math.abs(r.mean - t.mean) : null;
            const cls = drift === null ? '' : (drift <= 1 ? 'v-ok' : (drift <= 5 ? 'v-warn' : 'v-bad'));
            return `<tr><td>${p.can_id}</td><td>${p.pgn}</td><td>${fmtNum(t.mean, 2)}</td>` +
                   `<td class="${cls}">${fmtNum(r.mean, 2)}</td><td>${fmtNum(r.std, 2)}</td>` +
                   `<td>${r.n ? fmtNum(r.min, 1) + '..' + fmtNum(r.max, 1) : '–'}</td><td>${r.n ? r.n + 1 : 0}</td></tr>`;
        });
        document.getElementById('valPeriodBody').innerHTML = periodRows.join('') ||
            '<tr><td colspan="7" class="val-empty">No $TX frames yet</td></tr>';

        const spnRows = (s.spn_checks || []).map(c => {
            const last = c.last || {};
            const cls = c.fail ? 'v-bad' : 'v-ok';
            return `<tr><td>${c.spn}</td><td>${escapeHtml(c.name)}</td>` +
                   `<td>${fmtNum(last.value, 3)} ${escapeHtml(last.unit || '')}</td>` +
                   `<td>${last.expected === undefined ? '<span title="Random pattern: range check only">range</span>' : fmtNum(last.expected, 3)}</td>` +
                   `<td class="v-ok">${c.value_ok + c.range_only}</td><td class="${cls}">${c.fail}</td></tr>`;
        });
        document.getElementById('valSpnBody').innerHTML = spnRows.join('') ||
            '<tr><td colspan="6" class="val-empty">Start a run to check decoded SPN values</td></tr>';

        const byteCells = (hex, diff) => (hex || '').split(' ').filter(Boolean)
            .map((b, i) => diff.includes(i) ? `<span class="diff-byte">${b}</span>` : b).join(' ');
        const eventRows = (events || []).slice().reverse().map(e => {
            const cls = e.verdict === 'UNLOGGED' ? 'v-warn' : 'v-bad';
            const diff = e.diff_bytes || [];
            return `<tr><td class="${cls}">${e.verdict}</td><td>${e.seq ?? '–'}</td><td>${e.can_id}</td>` +
                   `<td>${e.t_tx_ms ?? '–'}</td><td>${byteCells(e.tx_data, diff)}</td>` +
                   `<td>${byteCells(e.rx_data, diff)}</td><td>${diff.length ? diff.map(i => i + 1).join(',') : ''}</td></tr>`;
        });
        document.getElementById('valEventBody').innerHTML = eventRows.join('') ||
            '<tr><td colspan="7" class="val-empty">No anomalies</td></tr>';
    }

    async function refreshValidation() {
        try {
            const [sRes, eRes] = await Promise.all([
                fetch('/api/validation/summary'),
                fetch('/api/validation/events?limit=200&errors_only=true')
            ]);
            if (!sRes.ok || !eRes.ok) return;
            renderValidation(await sRes.json(), (await eRes.json()).events);
        } catch (err) {
            // Server not reachable: keep last view
        }
    }

    function setupValidationView() {
        const resetBtn = document.getElementById('valResetBtn');
        const exportBtn = document.getElementById('valExportBtn');
        if (resetBtn) resetBtn.addEventListener('click', async () => {
            await fetch('/api/validation/reset', { method: 'POST' });
            refreshValidation();
        });
        if (exportBtn) exportBtn.addEventListener('click', () => {
            window.location.href = '/api/validation/export';
        });
        if (validationTimerId === null) {
            validationTimerId = setInterval(() => {
                if (state.viewMode === 'validation' && !document.hidden) refreshValidation();
            }, 1000);
        }
    }

    // =========================================================================
    // LIVE SSE SERIAL MONITOR
    // =========================================================================
    function initSseStream() {
        const eventSource = new EventSource('/api/serial_stream');

        eventSource.onmessage = (event) => {
            const line = event.data;
            if (line && line.trim() !== '') {
                appendTerminalLine(line);
                parseTelemetryForStats(line);
            }
        };

        eventSource.onerror = (err) => {
            // Reconnects automatically
        };
    }

    function appendTerminalLine(text) {
        const div = document.createElement('div');
        div.className = 'term-line';

        if (text.includes('[TX]')) div.classList.add('tx');
        else if (text.includes('[ACK]')) div.classList.add('ack');
        else if (text.includes('[ERR]') || text.includes('[WARN]')) div.classList.add('err');
        else if (text.includes('[CMD OUT]')) div.classList.add('cmd');
        else div.classList.add('sys');

        div.textContent = text;
        terminalScreen.appendChild(div);

        if (state.autoScroll) {
            terminalScreen.scrollTop = terminalScreen.scrollHeight;
        }
    }

    function parseTelemetryForStats(line) {
        // [TX] Frames: 1240 | ...
        const frameMatch = line.match(/Frames:\s*(\d+)/i);
        if (frameMatch) {
            state.stats.framesSent = parseInt(frameMatch[1]);
            statFramesEl.textContent = state.stats.framesSent;
        }

        const pgnMatch = line.match(/Active PGNs:\s*(\d+)/i);
        if (pgnMatch) {
            state.stats.activePgns = parseInt(pgnMatch[1]);
            statActivePgnsEl.textContent = state.stats.activePgns;
        }

        if (line.includes('STRESS TESTING')) {
            statModeEl.textContent = 'STRESS';
        } else if (line.includes('SAE STANDARDS')) {
            statModeEl.textContent = 'SAE';
        }
    }

    // =========================================================================
    // CONFIGURATION SNAPSHOT LOGGING & COMPARISON (PERSISTED IN LOCALSTORAGE)
    // =========================================================================
    function loadSavedInputLogs() {
        try {
            const saved = localStorage.getItem('j1939_input_log');
            if (saved) {
                state.inputLog = JSON.parse(saved);
            }
        } catch (e) {
            console.warn('Failed to load saved logs:', e);
            state.inputLog = [];
        }
        renderInputLogPanel();
    }

    function saveInputLogsToStorage() {
        try {
            localStorage.setItem('j1939_input_log', JSON.stringify(state.inputLog));
        } catch (e) {
            console.warn('Failed to persist logs:', e);
        }
    }

    function logCurrentInput() {
        const timestamp = new Date().toLocaleString();
        const isoTime = new Date().toISOString();
        
        const selectedList = Array.from(state.selectedSpns);
        const configs = {};
        selectedList.forEach(spn => {
            const cfg = state.spnConfigs.get(spn);
            if (cfg) {
                configs[spn] = { ...cfg };
            }
        });

        const modeNames = ['Smooth Waveform (50 Hz)', 'SAE Standard J1939-71', 'Stress Testing (100 Hz)'];
        const snapshot = {
            id: 'SNAP_' + Date.now(),
            timestamp: timestamp,
            isoTime: isoTime,
            timingMode: state.timingMode,
            timingModeName: modeNames[state.timingMode] || 'Standard',
            testDurationSec: state.testDurationSec,
            canBaudKbps: state.canBaudKbps,
            activeSpnCount: selectedList.length,
            selectedSpns: selectedList,
            spnConfigs: configs
        };

        state.inputLog.unshift(snapshot);
        saveInputLogsToStorage();
        renderInputLogPanel();

        // Flash button text
        const btns = [document.getElementById('topLogInputBtn'), document.getElementById('logInputBtn')];
        btns.forEach(b => {
            if (b) {
                const orig = b.innerHTML;
                b.innerHTML = '<span class="btn-icon">✅</span><span class="btn-text">Log Saved!</span>';
                setTimeout(() => b.innerHTML = orig, 1200);
            }
        });
    }

    function downloadInputLogs() {
        if (state.inputLog.length === 0) {
            alert('No snapshot logs recorded yet. Click "Log Input State" to record a snapshot.');
            return;
        }

        const choice = confirm('Click OK to download full JSON format, or CANCEL for CSV format.');
        if (choice) {
            const jsonStr = JSON.stringify(state.inputLog, null, 2);
            const blob = new Blob([jsonStr], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `j1939_input_configuration_logs_${Date.now()}.json`;
            document.body.appendChild(a);
            a.click();
            URL.revokeObjectURL(url);
            document.body.removeChild(a);
        } else {
            let csv = 'Timestamp,Log_ID,Timing_Mode,Duration_Sec,Baud_Kbps,Active_SPN_Count,Active_SPNs\n';
            state.inputLog.forEach(s => {
                const spnsStr = `"${s.selectedSpns.join('; ')}"`;
                csv += `"${s.timestamp}","${s.id}","${s.timingModeName}",${s.testDurationSec},${s.canBaudKbps},${s.activeSpnCount},${spnsStr}\n`;
            });
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `j1939_input_configuration_logs_${Date.now()}.csv`;
            document.body.appendChild(a);
            a.click();
            URL.revokeObjectURL(url);
            document.body.removeChild(a);
        }
    }

    function renderInputLogPanel() {
        const panel = document.getElementById('topInputLogPanel');
        const list = document.getElementById('logHistoryList');
        if (!panel || !list) return;

        if (state.inputLog.length === 0) {
            panel.classList.add('hidden');
            list.innerHTML = '';
            return;
        }

        panel.classList.remove('hidden');
        list.innerHTML = `
            <div class="log-panel-header">
                <strong>Saved Input Snapshots (${state.inputLog.length})</strong>
                <span class="log-hint">Select 2 snapshots and click "Compare Snapshots" to highlight differences</span>
            </div>
            <div class="log-grid">
                ${state.inputLog.map((snap, idx) => `
                    <div class="log-item-card neu-flat" data-idx="${idx}">
                        <div class="log-item-top">
                            <input type="checkbox" class="snap-check" data-idx="${idx}">
                            <span class="snap-time">📸 ${snap.timestamp}</span>
                            <span class="snap-badge">${snap.activeSpnCount} SPNs</span>
                        </div>
                        <div class="log-item-body">
                            <span>Mode: <strong>${snap.timingModeName}</strong></span> &bull;
                            <span>Baud: <strong>${snap.canBaudKbps} kbps</strong></span> &bull;
                            <span>Dur: <strong>${snap.testDurationSec === 0 ? 'Continuous' : snap.testDurationSec + 's'}</strong></span>
                        </div>
                        <div class="log-item-spns" title="${snap.selectedSpns.join(', ')}">
                            SPNs: ${snap.selectedSpns.slice(0, 8).join(', ')}${snap.selectedSpns.length > 8 ? '...' : ''}
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    }

    function compareLoggedInputs() {
        const checkedBoxes = document.querySelectorAll('.snap-check:checked');
        if (checkedBoxes.length !== 2) {
            alert('Please check EXACTLY 2 snapshots in the list to compare side-by-side.');
            return;
        }

        const idx1 = parseInt(checkedBoxes[0].dataset.idx);
        const idx2 = parseInt(checkedBoxes[1].dataset.idx);
        const snap1 = state.inputLog[idx1];
        const snap2 = state.inputLog[idx2];

        const diffContainer = document.getElementById('logDiffContainer');
        if (!diffContainer) return;

        diffContainer.classList.remove('hidden');

        let rows = '';

        const fields = [
            { label: 'Timestamp', v1: snap1.timestamp, v2: snap2.timestamp },
            { label: 'Timing Mode', v1: snap1.timingModeName, v2: snap2.timingModeName },
            { label: 'CAN Baud Rate', v1: `${snap1.canBaudKbps} kbps`, v2: `${snap2.canBaudKbps} kbps` },
            { label: 'Test Duration', v1: snap1.testDurationSec === 0 ? 'Continuous' : `${snap1.testDurationSec}s`, v2: snap2.testDurationSec === 0 ? 'Continuous' : `${snap2.testDurationSec}s` },
            { label: 'Active SPN Count', v1: snap1.activeSpnCount, v2: snap2.activeSpnCount },
            { label: 'Active SPNs List', v1: snap1.selectedSpns.join(', '), v2: snap2.selectedSpns.join(', ') }
        ];

        fields.forEach(f => {
            const isDiff = String(f.v1) !== String(f.v2);
            rows += `
                <tr class="${isDiff ? 'diff-changed' : ''}">
                    <td><strong>${f.label}</strong></td>
                    <td>${f.v1}</td>
                    <td>${f.v2}</td>
                    <td>${isDiff ? '<span class="diff-tag">CHANGED</span>' : 'Match'}</td>
                </tr>
            `;
        });

        const allSpns = new Set([...snap1.selectedSpns, ...snap2.selectedSpns]);
        allSpns.forEach(spn => {
            const c1 = snap1.spnConfigs[spn];
            const c2 = snap2.spnConfigs[spn];
            const waveNames = ['Constant', 'Ramp', 'Sine', 'Triangle', 'Step', 'Square', 'Random'];

            const w1 = c1 ? `${waveNames[c1.type] || c1.type} (Period/Val: ${c1.param1})` : 'Not Selected';
            const w2 = c2 ? `${waveNames[c2.type] || c2.type} (Period/Val: ${c2.param1})` : 'Not Selected';
            const isDiff = w1 !== w2;

            rows += `
                <tr class="${isDiff ? 'diff-changed' : ''}">
                    <td><strong>SPN ${spn} Waveform</strong></td>
                    <td>${w1}</td>
                    <td>${w2}</td>
                    <td>${isDiff ? '<span class="diff-tag">CHANGED</span>' : 'Match'}</td>
                </tr>
            `;
        });

        diffContainer.innerHTML = `
            <div class="diff-header">
                <h3>📊 Snapshot Comparison Diff Table</h3>
                <button id="closeDiffBtn" class="neu-btn icon-btn">✕ Close Diff</button>
            </div>
            <div class="diff-table-wrap">
                <table class="diff-table neu-inset">
                    <thead>
                        <tr>
                            <th>Parameter / SPN</th>
                            <th>Snapshot 1 (${snap1.timestamp})</th>
                            <th>Snapshot 2 (${snap2.timestamp})</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rows}
                    </tbody>
                </table>
            </div>
        `;

        document.getElementById('closeDiffBtn').addEventListener('click', () => {
            diffContainer.classList.add('hidden');
        });
    }

    function clearInputLogs() {
        if (state.inputLog.length === 0) return;
        if (confirm('Are you sure you want to clear all logged input snapshots?')) {
            state.inputLog = [];
            localStorage.removeItem('j1939_input_log');
            renderInputLogPanel();
            const diffContainer = document.getElementById('logDiffContainer');
            if (diffContainer) diffContainer.classList.add('hidden');
        }
    }

    // =========================================================================
    // REAL-TIME CONTINUOUS .TXT CAN BUS LOG RECORDER
    // =========================================================================
    function toggleRecordTxtLog() {
        state.isRecordingTxtLog = !state.isRecordingTxtLog;
        const recBtn = document.getElementById('topToggleRecBtn');
        const recIcon = document.getElementById('topRecIcon');
        const recText = document.getElementById('topRecText');
        const recStatusPill = document.getElementById('recStatusPill');

        if (state.isRecordingTxtLog) {
            state.recordedTxtLines = [];
            state.recordingStartTime = Date.now();
            if (recIcon) recIcon.textContent = '⏹️';
            if (recText) recText.textContent = 'Stop Preview Recording';
            if (recBtn) recBtn.classList.add('recording-active');
            if (recStatusPill) recStatusPill.classList.remove('hidden');
        } else {
            if (recIcon) recIcon.textContent = '🔴';
            if (recText) recText.textContent = 'Record Preview (.txt)';
            if (recBtn) recBtn.classList.remove('recording-active');
            
            if (state.recordedTxtLines.length > 0) {
                downloadRecordedTxtLog();
            } else {
                alert('Recording stopped. No CAN frames were captured during recording window.');
            }
        }
    }

    function downloadRecordedTxtLog() {
        if (state.recordedTxtLines.length === 0) {
            alert('Log buffer is empty. Start recording first to capture real-time CAN bus frames into .txt!');
            return;
        }

        const dateStr = new Date().toLocaleString();
        const modeNames = ['Smooth Waveform (50 Hz)', 'SAE Standard J1939-71', 'Stress Testing (100 Hz)'];
        const modeText = modeNames[state.timingMode] || 'Standard';

        let txtContent = `================================================================================\n`;
        txtContent += `J1939 INTELLIGENT WAVEFORM & SIGNAL VERIFICATION HUB - CAN BUS TRACE LOG (.TXT)\n`;
        txtContent += `Creators: Vishal Meyyappan R (3rd Year ECE - ACT) & Srikar (4th year ECE)\n`;
        txtContent += `Institution: Chennai Institute of Technology (CIT Chennai) & STUST Taiwan\n`;
        txtContent += `--------------------------------------------------------------------------------\n`;
        txtContent += `Log Timestamp    : ${dateStr}\n`;
        txtContent += `CAN Bus Bitrate  : ${state.canBaudKbps} kbps (J1939-21 Extended 29-bit CAN ID)\n`;
        txtContent += `Timing Mode      : ${modeText}\n`;
        txtContent += `Total Logged     : ${state.recordedTxtLines.length} frames\n`;
        txtContent += `================================================================================\n\n`;
        txtContent += `  #Frame     Time(ms)  Dir  CAN_ID_29b  PGN    DLC  Data_Bytes_Hex (D0..D7)    Decoded_Physical_SPN_Values\n`;
        txtContent += `------------------------------------------------------------------------------------------------------------------------\n`;
        txtContent += state.recordedTxtLines.join('\n');
        txtContent += `\n\n=================================== END OF LOG ===================================\n`;

        const blob = new Blob([txtContent], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const filename = `j1939_simulated_preview_${Date.now()}.txt`;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        URL.revokeObjectURL(url);
        document.body.removeChild(a);
    }

    // =========================================================================
    // EVENT LISTENERS & USER CONTROLS
    // =========================================================================
    function setupEventListeners() {
        // TOP LOG TOOLBAR BUTTONS
        const topToggleRecBtn = document.getElementById('topToggleRecBtn');
        const topDownloadTxtLogBtn = document.getElementById('topDownloadTxtLogBtn');
        const topLogConfigBtn = document.getElementById('topLogConfigBtn');
        const topCompareLogBtn = document.getElementById('topCompareLogBtn');
        const topClearLogBtn = document.getElementById('topClearLogBtn');
        const logInputBtn = document.getElementById('logInputBtn');

        if (topToggleRecBtn) topToggleRecBtn.addEventListener('click', toggleRecordTxtLog);
        if (topDownloadTxtLogBtn) topDownloadTxtLogBtn.addEventListener('click', downloadRecordedTxtLog);
        if (topLogConfigBtn) topLogConfigBtn.addEventListener('click', logCurrentInput);
        if (logInputBtn) logInputBtn.addEventListener('click', logCurrentInput);
        if (topCompareLogBtn) topCompareLogBtn.addEventListener('click', compareLoggedInputs);
        if (topClearLogBtn) topClearLogBtn.addEventListener('click', () => {
            state.recordedTxtLines = [];
            clearInputLogs();
        });

        // Refresh Ports
        refreshPortsBtn.addEventListener('click', async () => {
            refreshPortsBtn.classList.add('pressed');
            await loadPorts();
            setTimeout(() => refreshPortsBtn.classList.remove('pressed'), 200);
        });

        // Connect Button
        connectBtn.addEventListener('click', async () => {
            const port = comPortSelect.value;
            const baud = parseInt(serialBaudSelect.value) || 115200;

            if (!state.isConnected) {
                if (!port) {
                    alert('Please select a valid COM port.');
                    return;
                }
                try {
                    connectBtn.disabled = true;
                    connectBtn.innerHTML = 'Connecting...';
                    const res = await fetch('/api/connect', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ port, baudrate: baud })
                    });
                    const data = await res.json();
                    if (data.success) {
                        state.isConnected = true;
                        connectBtn.innerHTML = '<span class="btn-icon">🔌</span><span class="btn-text">Disconnect</span>';
                        connectBtn.classList.replace('primary-btn', 'stop-btn');
                        connStatusPill.className = 'status-pill online';
                        connStatusPill.innerHTML = '<span class="status-dot"></span><span class="status-label">ONLINE: ' + port + '</span>';
                    } else {
                        throw new Error(data.detail || 'Connection failed');
                    }
                } catch (err) {
                    alert('Connection Error: ' + err.message);
                } finally {
                    connectBtn.disabled = false;
                }
            } else {
                // Disconnect
                await fetch('/api/disconnect', { method: 'POST' });
                state.isConnected = false;
                connectBtn.innerHTML = '<span class="btn-icon">⚡</span><span class="btn-text">Connect Device</span>';
                connectBtn.classList.replace('stop-btn', 'primary-btn');
                connStatusPill.className = 'status-pill offline';
                connStatusPill.innerHTML = '<span class="status-dot"></span><span class="status-label">DISCONNECTED</span>';
            }
        });

        // CAN Baud Rate Toggle (250 vs 500 kbps)
        canBaudToggle.querySelectorAll('.seg-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                canBaudToggle.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                state.canBaudKbps = parseInt(btn.dataset.baud);
                statBaudEl.textContent = state.canBaudKbps;

                if (state.isConnected) {
                    await fetch('/api/baud', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ baud_kbps: state.canBaudKbps })
                    });
                }
            });
        });

        // Timing Mode Switch (Smooth 50 Hz vs SAE vs Stress)
        if (modeSmoothBtn) {
            modeSmoothBtn.addEventListener('click', () => {
                modeSmoothBtn.classList.add('active');
                modeSaeBtn.classList.remove('active');
                modeStressBtn.classList.remove('active');
                state.timingMode = 0;
                state.stats.mode = 'SMOOTH';
                statModeEl.textContent = 'SMOOTH';
            });
        }
        modeSaeBtn.addEventListener('click', () => {
            modeSaeBtn.classList.add('active');
            if (modeSmoothBtn) modeSmoothBtn.classList.remove('active');
            modeStressBtn.classList.remove('active');
            state.timingMode = 1;
            state.stats.mode = 'SAE';
            statModeEl.textContent = 'SAE';
        });
        modeStressBtn.addEventListener('click', () => {
            modeStressBtn.classList.add('active');
            if (modeSmoothBtn) modeSmoothBtn.classList.remove('active');
            modeSaeBtn.classList.remove('active');
            state.timingMode = 2;
            state.stats.mode = 'STRESS';
            statModeEl.textContent = 'STRESS';
        });

        // Duration Presets
        document.querySelectorAll('.duration-presets .neu-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                document.querySelectorAll('.duration-presets .neu-chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                state.testDurationSec = parseInt(chip.dataset.dur);
                customDurationInput.value = '';
            });
        });
        customDurationInput.addEventListener('input', () => {
            const val = parseInt(customDurationInput.value);
            if (!isNaN(val) && val > 0) {
                document.querySelectorAll('.duration-presets .neu-chip').forEach(c => c.classList.remove('active'));
                state.testDurationSec = val;
            }
        });

        // Category Tabs
        categoryTabs.querySelectorAll('.cat-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                categoryTabs.querySelectorAll('.cat-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                state.currentCategory = tab.dataset.cat;
                renderActiveView();
            });
        });

        // Search Input
        spnSearchInput.addEventListener('input', () => {
            state.searchQuery = spnSearchInput.value;
            renderActiveView();
        });

        function applyBatchWaveformToAll(type, param) {
            state.spns.forEach(s => {
                const cfg = state.spnConfigs.get(s.spn) || { min: s.min_physical, max: s.max_physical, timeframe_ms: 20 };
                cfg.type = type;
                if (type === 0) {
                    cfg.param1 = (param !== undefined && !isNaN(param) && param >= cfg.min && param <= cfg.max)
                        ? param : Math.round(((cfg.min + cfg.max) * 0.5) * 100) / 100;
                } else {
                    cfg.param1 = param;
                }
                state.spnConfigs.set(s.spn, cfg);
                state.selectedSpns.add(s.spn);
            });
            const bSelect = document.getElementById('batchWaveformSelect');
            const bParam = document.getElementById('batchParamInput');
            if (bSelect) bSelect.value = type;
            if (bParam) bParam.value = fmtSeconds(param);
            updateSelectionCount();
            renderActiveView();
            updateBusLoadMeter();
            focusVisualizer(190);
        }

        // BATCH CONTROLLER BUTTONS
        // Period: same flicker-free ▲▼ stepper as Start/Duration, never below 0.5 s
        const batchParamInput = document.getElementById('batchParamInput');
        if (batchParamInput) bindSecondsInput(batchParamInput, () => {}, SECONDS_STEP);
        const batchApplyAllBtn = document.getElementById('batchApplyAllBtn');
        if (batchApplyAllBtn) {
            batchApplyAllBtn.addEventListener('click', () => {
                const type = parseInt(document.getElementById('batchWaveformSelect').value);
                const param = parseFloat(document.getElementById('batchParamInput').value) || 10.0;
                applyBatchWaveformToAll(type, param);
            });
        }

        const batchApplySelectedBtn = document.getElementById('batchApplySelectedBtn');
        if (batchApplySelectedBtn) {
            batchApplySelectedBtn.addEventListener('click', () => {
                if (state.selectedSpns.size === 0) {
                    alert('Please select at least one SPN first.');
                    return;
                }
                const type = parseInt(document.getElementById('batchWaveformSelect').value);
                const param = parseFloat(document.getElementById('batchParamInput').value) || 10.0;
                state.selectedSpns.forEach(spn => {
                    const cfg = state.spnConfigs.get(spn);
                    if (cfg) {
                        cfg.type = type;
                        if (type === 0) {
                            cfg.param1 = (param >= cfg.min && param <= cfg.max)
                                ? param : Math.round(((cfg.min + cfg.max) * 0.5) * 100) / 100;
                        } else {
                            cfg.param1 = param;
                        }
                    }
                });
                renderActiveView();
                updateBusLoadMeter();
            });
        }

        // PRO BATCH TIMEFRAME CONTROLS
        let selectedBatchTf = 20;
        document.querySelectorAll('.tf-quick-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tf-quick-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                selectedBatchTf = parseInt(btn.dataset.tf);
                const customIn = document.getElementById('batchCustomTfInput');
                if (customIn) customIn.value = '';
            });
        });

        const batchCustomTfInput = document.getElementById('batchCustomTfInput');
        if (batchCustomTfInput) {
            batchCustomTfInput.addEventListener('input', () => {
                const val = parseInt(batchCustomTfInput.value);
                if (!isNaN(val) && val >= 5) {
                    document.querySelectorAll('.tf-quick-btn').forEach(b => b.classList.remove('active'));
                    selectedBatchTf = val;
                }
            });
        }

        const batchApplyTfAllBtn = document.getElementById('batchApplyTfAllBtn');
        if (batchApplyTfAllBtn) {
            batchApplyTfAllBtn.addEventListener('click', () => {
                state.spns.forEach(s => {
                    const cfg = state.spnConfigs.get(s.spn);
                    if (cfg) cfg.timeframe_ms = selectedBatchTf;
                });
                renderActiveView();
                updateBusLoadMeter();
            });
        }

        // PRO VIEW MODE SWITCHER
        const viewCardsBtn = document.getElementById('viewCardsBtn');
        const viewMatrixBtn = document.getElementById('viewMatrixBtn');
        const viewPcanBtn = document.getElementById('viewPcanBtn');
        const cardsContainer = document.getElementById('spnCardsContainer');
        const matrixContainer = document.getElementById('spnMatrixContainer');
        const pcanContainer = document.getElementById('spnPcanContainer');
        const viewValidationBtn = document.getElementById('viewValidationBtn');
        const validationContainer = document.getElementById('spnValidationContainer');

        function switchView(mode) {
            state.viewMode = mode;
            [viewCardsBtn, viewMatrixBtn, viewPcanBtn, viewValidationBtn].forEach(b => b && b.classList.remove('active'));
            [cardsContainer, matrixContainer, pcanContainer, validationContainer].forEach(c => c && c.classList.add('hidden'));

            if (mode === 'cards') {
                if (viewCardsBtn) viewCardsBtn.classList.add('active');
                if (cardsContainer) cardsContainer.classList.remove('hidden');
                renderSpnCards();
            } else if (mode === 'matrix') {
                if (viewMatrixBtn) viewMatrixBtn.classList.add('active');
                if (matrixContainer) matrixContainer.classList.remove('hidden');
                renderMatrixTable();
            } else if (mode === 'pcan') {
                if (viewPcanBtn) viewPcanBtn.classList.add('active');
                if (pcanContainer) pcanContainer.classList.remove('hidden');
            } else if (mode === 'validation') {
                if (viewValidationBtn) viewValidationBtn.classList.add('active');
                if (validationContainer) validationContainer.classList.remove('hidden');
                refreshValidation();
            }
        }

        if (viewValidationBtn) viewValidationBtn.addEventListener('click', () => switchView('validation'));
        setupValidationView();
        if (window.location.hash === '#validation') switchView('validation');

        if (viewCardsBtn) viewCardsBtn.addEventListener('click', () => switchView('cards'));
        if (viewMatrixBtn) viewMatrixBtn.addEventListener('click', () => switchView('matrix'));
        if (viewPcanBtn) viewPcanBtn.addEventListener('click', () => switchView('pcan'));

        const matrixSelectAll = document.getElementById('matrixSelectAll');
        if (matrixSelectAll) {
            matrixSelectAll.addEventListener('change', (e) => {
                const filtered = getFilteredSpns();
                if (e.target.checked) {
                    filtered.forEach(s => state.selectedSpns.add(s.spn));
                } else {
                    filtered.forEach(s => state.selectedSpns.delete(s.spn));
                }
                updateSelectionCount();
                renderActiveView();
                updateBusLoadMeter();
            });
        }

        // PRESET SUITES
        const pSine = document.getElementById('presetAll52Sine');
        if (pSine) pSine.addEventListener('click', () => applyBatchWaveformToAll(2, 10.0));

        const pRamp = document.getElementById('presetAll52Ramp');
        if (pRamp) pRamp.addEventListener('click', () => applyBatchWaveformToAll(1, 10.0));

        const pTri = document.getElementById('presetAll52Triangle');
        if (pTri) pTri.addEventListener('click', () => applyBatchWaveformToAll(3, 10.0));

        const pStep = document.getElementById('presetAll52Step');
        if (pStep) pStep.addEventListener('click', () => applyBatchWaveformToAll(4, 8.0));

        const pSquare = document.getElementById('presetAll52Square');
        if (pSquare) pSquare.addEventListener('click', () => applyBatchWaveformToAll(5, 10.0));

        const pNoise = document.getElementById('presetAll52Noise');
        if (pNoise) pNoise.addEventListener('click', () => applyBatchWaveformToAll(6, 10.0));

        const pConst = document.getElementById('presetAll52Constant');
        if (pConst) pConst.addEventListener('click', () => applyBatchWaveformToAll(0, 50.0));

        document.getElementById('presetPowertrain').addEventListener('click', () => {
            state.selectedSpns.clear();
            [190, 91, 84, 523].forEach(spn => state.selectedSpns.add(spn));
            updateSelectionCount();
            renderActiveView();
            updateBusLoadMeter();
            focusVisualizer(190);
        });

        document.getElementById('presetSpn190Stress').addEventListener('click', () => {
            state.selectedSpns.clear();
            state.selectedSpns.add(190);
            modeStressBtn.click();
            updateSelectionCount();
            renderActiveView();
            updateBusLoadMeter();
            focusVisualizer(190);
        });

        document.getElementById('presetEmissions').addEventListener('click', () => {
            state.selectedSpns.clear();
            [4331, 1761, 3031, 3250, 3246].forEach(spn => state.selectedSpns.add(spn));
            updateSelectionCount();
            renderActiveView();
            updateBusLoadMeter();
            focusVisualizer(4331);
        });

        document.getElementById('presetSelectAll').addEventListener('click', () => {
            state.spns.forEach(s => state.selectedSpns.add(s.spn));
            updateSelectionCount();
            renderActiveView();
            updateBusLoadMeter();
        });

        document.getElementById('presetClearAll').addEventListener('click', () => {
            state.selectedSpns.clear();
            updateSelectionCount();
            renderActiveView();
            updateBusLoadMeter();
        });

        // START GENERATOR
        startGeneratorBtn.addEventListener('click', async () => {
            if (!state.isConnected) {
                const port = comPortSelect.value;
                if (!port) {
                    alert('Please select a valid COM port and connect first.');
                    return;
                }
                try {
                    const baud = parseInt(serialBaudSelect.value) || 115200;
                    const cRes = await fetch('/api/connect', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ port, baudrate: baud })
                    });
                    const cData = await cRes.json();
                    if (cData.success) {
                        state.isConnected = true;
                        connectBtn.innerHTML = '<span class="btn-icon">🔌</span><span class="btn-text">Disconnect</span>';
                        connectBtn.classList.replace('primary-btn', 'stop-btn');
                        connStatusPill.className = 'status-pill online';
                        connStatusPill.innerHTML = '<span class="status-dot"></span><span class="status-label">ONLINE: ' + port + '</span>';
                    } else {
                        throw new Error(cData.detail || 'Connection failed');
                    }
                } catch (err) {
                    alert('Connection Error: ' + err.message);
                    return;
                }
            }

            if (state.selectedSpns.size === 0) {
                alert('Please select at least one SPN to generate.');
                return;
            }

            try {
                startGeneratorBtn.disabled = true;
                startGeneratorBtn.classList.add('pressed');

                // 1. Keep firmware CAN bitrate in sync with the UI before every run
                await fetch('/api/baud', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ baud_kbps: state.canBaudKbps })
                });

                // 2. Build configuration payload
                const signals = buildActiveSignalsPayload();

                // Send configure request
                const cfgRes = await fetch('/api/configure', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ signals })
                });
                if (!cfgRes.ok) throw new Error('Failed to configure SPN patterns.');

                // Send start request
                const startRes = await fetch('/api/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        duration_sec: state.testDurationSec,
                        mode: state.timingMode,
                        tx_log: !!(document.getElementById('txLogToggle') || {}).checked
                    })
                });
                if (!startRes.ok) throw new Error('Failed to start generator.');

                isGeneratorRunning = true;
                pcanSimulationStartTime = Date.now();
                statActivePgnsEl.textContent = state.selectedSpns.size;
            } catch (err) {
                alert('Launch Error: ' + err.message);
            } finally {
                startGeneratorBtn.disabled = false;
                startGeneratorBtn.classList.remove('pressed');
            }
        });

        // STOP GENERATOR
        stopGeneratorBtn.addEventListener('click', async () => {
            isGeneratorRunning = false;
            if (!state.isConnected) return;
            try {
                await fetch('/api/stop', { method: 'POST' });
            } catch (err) {
                console.error(err);
            }
        });

        // RESET SYSTEM
        resetSystemBtn.addEventListener('click', async () => {
            isGeneratorRunning = false;
            pcanTotalFramesSent = 0;
            if (!state.isConnected) return;
            try {
                await fetch('/api/send_raw', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: 'RESET' })
                });
            } catch (err) {
                console.error(err);
            }
        });

        // TERMINAL TOOLS
        autoScrollToggle.addEventListener('click', () => {
            state.autoScroll = !state.autoScroll;
            autoScrollToggle.classList.toggle('active', state.autoScroll);
        });

        clearTerminalBtn.addEventListener('click', () => {
            terminalScreen.innerHTML = '';
        });

        copyLogsBtn.addEventListener('click', () => {
            const text = terminalScreen.innerText;
            navigator.clipboard.writeText(text).then(() => {
                const orig = copyLogsBtn.textContent;
                copyLogsBtn.textContent = 'Copied!';
                setTimeout(() => copyLogsBtn.textContent = orig, 1200);
            });
        });

        // DBC EXPORT & PREVIEW CONTROLS
        const downloadActiveDbcBtn = document.getElementById('downloadActiveDbcBtn');
        const downloadFullDbcBtn = document.getElementById('downloadFullDbcBtn');
        const previewDbcBtn = document.getElementById('previewDbcBtn');
        const dbcModal = document.getElementById('dbcModal');
        const closeDbcModalBtn = document.getElementById('closeDbcModalBtn');
        const dbcCodeBlock = document.getElementById('dbcCodeBlock');
        const copyDbcTextBtn = document.getElementById('copyDbcTextBtn');
        const modalDownloadDbcBtn = document.getElementById('modalDownloadDbcBtn');
        const dbcMetaScope = document.getElementById('dbcMetaScope');
        const dbcMetaSignals = document.getElementById('dbcMetaSignals');
        const dbcMetaTiming = document.getElementById('dbcMetaTiming');
        const dbcMetaBaud = document.getElementById('dbcMetaBaud');

        let currentDbcText = '';
        let currentDbcFilename = 'j1939_active_patterns.dbc';

        function buildActiveSignalsPayload() {
            const signals = [];
            const targets = state.selectedSpns.size > 0 ? state.selectedSpns : new Set([190, 91, 84]);
            targets.forEach(spn => {
                const cfg = state.spnConfigs.get(spn) || { type: 1, min: 0, max: 100, param1: 10.0, timeframe_ms: 20, t_start: 0, t_dur: 0 };
                signals.push({
                    spn: spn,
                    pattern_type: cfg.type,
                    min_value: cfg.min,
                    max_value: cfg.max,
                    param1: cfg.param1,
                    timeframe_ms: cfg.timeframe_ms || 20,
                    t_start: cfg.t_start || 0.0,
                    t_dur: cfg.t_dur || 0.0
                });
            });
            return signals;
        }

        // 1. Download Active DBC Button
        downloadActiveDbcBtn.addEventListener('click', async () => {
            const signals = buildActiveSignalsPayload();
            try {
                downloadActiveDbcBtn.disabled = true;
                downloadActiveDbcBtn.classList.add('pressed');

                const res = await fetch('/api/dbc/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        signals: signals,
                        mode: state.timingMode,
                        baud_kbps: state.canBaudKbps,
                        scope: 'active',
                        title: 'J1939 Active Pattern Generator'
                    })
                });

                if (!res.ok) throw new Error('Failed to generate active DBC');

                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = `j1939_active_pattern_generator_${state.canBaudKbps}kbps.dbc`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
            } catch (err) {
                alert('DBC Download Error: ' + err.message);
            } finally {
                downloadActiveDbcBtn.disabled = false;
                downloadActiveDbcBtn.classList.remove('pressed');
            }
        });

        // 2. Download Full 52-SPN DBC Button
        downloadFullDbcBtn.addEventListener('click', async () => {
            try {
                downloadFullDbcBtn.disabled = true;
                downloadFullDbcBtn.classList.add('pressed');

                const res = await fetch(`/api/dbc/download_full?mode=${state.timingMode}&baud=${state.canBaudKbps}`);
                if (!res.ok) throw new Error('Failed to generate full DBC');

                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = `j1939_full_52spn_database_${state.canBaudKbps}kbps.dbc`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
            } catch (err) {
                alert('DBC Download Error: ' + err.message);
            } finally {
                downloadFullDbcBtn.disabled = false;
                downloadFullDbcBtn.classList.remove('pressed');
            }
        });

        // 3. Preview DBC Button & Modal Logic
        previewDbcBtn.addEventListener('click', async () => {
            const signals = buildActiveSignalsPayload();
            dbcCodeBlock.textContent = 'Generating standard Vector DBC syntax...';
            dbcModal.classList.remove('hidden');

            try {
                const res = await fetch('/api/dbc/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        signals: signals,
                        mode: state.timingMode,
                        baud_kbps: state.canBaudKbps,
                        scope: 'active',
                        title: 'J1939 Intelligent Waveform & Signal Verification Hub'
                    })
                });

                const data = await res.json();
                if (data.success) {
                    currentDbcText = data.dbc_text;
                    currentDbcFilename = data.filename;
                    dbcCodeBlock.textContent = data.dbc_text;
                    dbcMetaScope.textContent = `Scope: Active (${data.total_signals} SPNs)`;
                    dbcMetaSignals.textContent = `Signals: ${data.total_signals}`;
                    dbcMetaTiming.textContent = `Timing: ${state.timingMode === 1 ? 'Stress Test' : 'SAE Standard'}`;
                    dbcMetaBaud.textContent = `Baud: ${state.canBaudKbps} kbps`;
                } else {
                    throw new Error('DBC generation failed');
                }
            } catch (err) {
                dbcCodeBlock.textContent = 'Error generating DBC preview: ' + err.message;
            }
        });

        closeDbcModalBtn.addEventListener('click', () => {
            dbcModal.classList.add('hidden');
        });

        dbcModal.addEventListener('click', (e) => {
            if (e.target === dbcModal) {
                dbcModal.classList.add('hidden');
            }
        });

        copyDbcTextBtn.addEventListener('click', () => {
            if (!currentDbcText) return;
            navigator.clipboard.writeText(currentDbcText).then(() => {
                const orig = copyDbcTextBtn.innerHTML;
                copyDbcTextBtn.innerHTML = '<span>✅ Copied to Clipboard!</span>';
                setTimeout(() => copyDbcTextBtn.innerHTML = orig, 1500);
            });
        });

        modalDownloadDbcBtn.addEventListener('click', () => {
            if (!currentDbcText) return;
            const blob = new Blob([currentDbcText], { type: 'text/plain;charset=utf-8' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = currentDbcFilename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        });

        // MANUAL COMMAND SENDER
        sendCmdBtn.addEventListener('click', sendManualCommand);
        manualCmdInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') sendManualCommand();
        });

        async function sendManualCommand() {
            const cmd = manualCmdInput.value.trim();
            if (!cmd) return;
            if (!state.isConnected) {
                alert('Please connect device first.');
                return;
            }
            await fetch('/api/send_raw', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd })
            });
            manualCmdInput.value = '';
        }
    }

    // Launch Application
    init();
});
