const API = '/api/v1';
const tokenKey = 'impact_access';
const refreshKey = 'impact_refresh';

let projects = [];
let selectedProject = null;
let registerMode = false;
let currentTab = 'studio'; // 'studio' | 'projects'

const $ = (id) => document.getElementById(id);

const setMessage = (id, message, kind = '') => {
    const el = $(id);
    if (!el) return;
    el.textContent = message;
    el.className = `form-message ${kind}`;
};

const escapeHtml = (value) => {
    return String(value ?? '').replace(/[&<>'"]/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[c]));
};

const authHeaders = () => {
    const token = localStorage.getItem(tokenKey);
    return token ? { Authorization: `Bearer ${token}` } : {};
};

async function api(path, options = {}) {
    const headers = { ...authHeaders(), ...(options.headers || {}) };
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }

    let response = await fetch(`${API}${path}`, { ...options, headers });

    // Handle token expiration: try refreshing access token automatically
    if (response.status === 401 && localStorage.getItem(refreshKey)) {
        try {
            const refreshRes = await fetch(`${API}/auth/token/refresh/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh: localStorage.getItem(refreshKey) })
            });
            if (refreshRes.ok) {
                const refreshData = await refreshRes.json();
                localStorage.setItem(tokenKey, refreshData.access);
                headers['Authorization'] = `Bearer ${refreshData.access}`;
                response = await fetch(`${API}${path}`, { ...options, headers });
            } else {
                localStorage.removeItem(tokenKey);
                localStorage.removeItem(refreshKey);
                updateAuthStatus();
            }
        } catch {
            localStorage.removeItem(tokenKey);
            localStorage.removeItem(refreshKey);
            updateAuthStatus();
        }
    }

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        let msg = data.detail;
        if (!msg && typeof data === 'object') {
            msg = Object.values(data).flat().map(v => typeof v === 'object' ? JSON.stringify(v) : v).join(' ');
        }
        throw new Error(msg || 'Request failed.');
    }
    return data;
}

// -------------------------------------------------------------
// Tab Switching
// -------------------------------------------------------------
$('tab-quick-analyze').addEventListener('click', () => switchTab('studio'));
$('tab-projects').addEventListener('click', () => switchTab('projects'));

function switchTab(tab) {
    currentTab = tab;
    if (tab === 'studio') {
        $('tab-quick-analyze').classList.add('active');
        $('tab-projects').classList.remove('active');
        $('studio-view').classList.remove('hidden');
        $('projects-view').classList.add('hidden');
    } else {
        $('tab-projects').classList.add('active');
        $('tab-quick-analyze').classList.remove('active');
        $('projects-view').classList.remove('hidden');
        $('studio-view').classList.add('hidden');
        
        // Check if user is logged in
        if (!localStorage.getItem(tokenKey)) {
            openAuthModal();
        } else {
            loadProjects();
        }
    }
}

// -------------------------------------------------------------
// Sample Presets & Code Studio Handlers
// -------------------------------------------------------------
$('load-calc-sample').addEventListener('click', () => {
    $('live-source-code').value = 
`def add(a, b):
    """Calculates sum of two numbers."""
    return a + b

def divide(a, b):
    """Divides a by b (potential ZeroDivisionError if b is 0)."""
    return a / b`;
    setMessage('studio-message', 'Loaded Calculator sample code.', 'success');
});

$('load-string-sample').addEventListener('click', () => {
    $('live-source-code').value = 
`def reverse_string(text):
    """Reverses the provided text."""
    return text[::-1]

def is_palindrome(text):
    """Checks whether a string reads the same backward as forward."""
    cleaned = text.lower().replace(" ", "")
    return cleaned == cleaned[::-1]`;
    setMessage('studio-message', 'Loaded String Parser sample code.', 'success');
});

$('clear-code').addEventListener('click', () => {
    $('live-source-code').value = '';
    setMessage('studio-message', '');
});

$('toggle-custom-tests').addEventListener('click', () => {
    const container = $('custom-tests-container');
    container.classList.toggle('hidden');
    const isHidden = container.classList.contains('hidden');
    $('toggle-custom-tests').textContent = isHidden ? 'Custom test cases' : 'Hide custom test cases';
});

// -------------------------------------------------------------
// Run Tests & Explain Code Form Submit
// -------------------------------------------------------------
$('quick-code-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const sourceCode = $('live-source-code').value.trim();
    if (!sourceCode) {
        setMessage('studio-message', 'Please paste or type Python code first.', 'error');
        return;
    }

    const customTestsEl = $('live-test-code');
    const customTests = !$('custom-tests-container').classList.contains('hidden') ? customTestsEl.value.trim() : '';

    setMessage('studio-message', 'Analyzing AST and executing pytest test cases...');
    $('analysis-spinner').classList.remove('hidden');
    $('btn-run-analysis').disabled = true;

    try {
        const response = await fetch(`${API}/code/analyze/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_code: sourceCode,
                test_code: customTests
            })
        });

        const report = await response.json();
        if (!response.ok) {
            throw new Error(report.detail || 'Analysis request failed.');
        }

        renderStudioResults(report);
        setMessage('studio-message', 'Analysis and test execution complete.', 'success');
    } catch (err) {
        setMessage('studio-message', err.message, 'error');
    } finally {
        $('analysis-spinner').classList.add('hidden');
        $('btn-run-analysis').disabled = false;
    }
});

function renderStudioResults(report) {
    const container = $('studio-result-content');
    const statusPill = $('status-pill');
    const scorePill = $('score-pill');

    container.className = 'result-content';

    // 1. Handle Syntax Error
    if (!report.valid_syntax) {
        statusPill.className = 'pill failed';
        statusPill.textContent = 'SYNTAX ERROR';
        scorePill.className = 'pill critical';
        scorePill.textContent = `RISK: ${report.risk_level}`;

        const err = report.syntax_error || {};
        container.innerHTML = `
            <div class="result-block">
                <h4>🚫 Code Syntax Compilation Error</h4>
                <div class="risk-card CRITICAL">
                    <div class="risk-card-top">
                        <span class="risk-title">Line ${err.line ?? '?'}: ${escapeHtml(err.message)}</span>
                        <span class="pill critical">CRITICAL</span>
                    </div>
                    <p class="risk-desc">
                        Python was unable to compile this code. ${escapeHtml(err.advice || '')}
                    </p>
                    ${err.text ? `<div class="code-preview-box" style="margin-top: 10px;">${escapeHtml(err.text)}</div>` : ''}
                </div>
            </div>
            <div class="result-block">
                <h4>💡 What Happens in This Code</h4>
                <p>Because of the syntax error above, this Python script cannot be executed by the interpreter or test runner. Fixing line ${err.line ?? 1} is required before runtime behavior or test cases can execute.</p>
            </div>
        `;
        return;
    }

    // 2. Compute Test Status & Pills
    const testRun = report.test_run || {};
    const totalTests = testRun.total || 0;
    const passedTests = testRun.passed || 0;
    const failedTests = testRun.failed || 0;

    if (failedTests > 0) {
        statusPill.className = 'pill failed';
        statusPill.textContent = `${failedTests} TEST(S) FAILED`;
    } else if (totalTests > 0) {
        statusPill.className = 'pill passed';
        statusPill.textContent = `ALL ${passedTests} TESTS PASSED`;
    } else {
        statusPill.className = 'pill ready';
        statusPill.textContent = 'NO TESTS RUN';
    }

    scorePill.className = `pill ${report.risk_level.toLowerCase()}`;
    scorePill.textContent = `RISK: ${report.risk_level} (${report.risk_score}/100)`;

    // 3. Build HTML Output
    let html = '';

    // Overview
    html += `
        <div class="result-block">
            <h4>🧠 Code Behavior & Execution Overview</h4>
            <div class="overview-banner">
                ${escapeHtml(report.overview)}
            </div>
        </div>
    `;

    // Functions Breakdown
    if (report.functions && report.functions.length > 0) {
        html += `
            <div class="result-block">
                <h4>🔍 Function Breakdown (${report.functions.length})</h4>
                ${report.functions.map(fn => `
                    <div class="fn-card">
                        <div class="fn-header">
                            <span class="fn-name">def ${escapeHtml(fn.name)}(${escapeHtml((fn.parameters || []).join(', '))})</span>
                            <span class="fn-params">Lines ${fn.line_start}-${fn.line_end}</span>
                        </div>
                        <p class="fn-explanation">${escapeHtml(fn.explanation)}</p>
                    </div>
                `).join('')}
            </div>
        `;
    }

    // Risks & Edge Cases
    if (report.risks && report.risks.length > 0) {
        html += `
            <div class="result-block">
                <h4>⚠️ Potential Risks & Edge Cases (${report.risks.length})</h4>
                ${report.risks.map(r => `
                    <div class="risk-card ${r.severity}">
                        <div class="risk-card-top">
                            <span class="risk-title">${escapeHtml(r.title)}</span>
                            <span class="pill ${r.severity.toLowerCase()}">${r.severity}</span>
                        </div>
                        <p class="risk-desc">${escapeHtml(r.description)}</p>
                    </div>
                `).join('')}
            </div>
        `;
    }

    // Test Results
    if (testRun.cases && testRun.cases.length > 0) {
        html += `
            <div class="result-block">
                <h4>🧪 Test Execution Results</h4>
                <div class="test-summary-bar">
                    <div class="test-metric">
                        <strong>${totalTests}</strong>
                        <span>Total Tests</span>
                    </div>
                    <div class="test-metric">
                        <strong style="color: var(--green);">${passedTests}</strong>
                        <span>Passed</span>
                    </div>
                    <div class="test-metric">
                        <strong style="color: ${failedTests ? 'var(--red)' : 'var(--muted)'};">${failedTests}</strong>
                        <span>Failed</span>
                    </div>
                    <div class="test-metric">
                        <strong>${testRun.duration ?? 0}s</strong>
                        <span>Duration</span>
                    </div>
                </div>

                ${testRun.cases.map(c => `
                    <div class="test-case-card ${c.status}">
                        <div class="test-case-top">
                            <span class="test-case-name">${c.status === 'PASSED' ? '✓' : '✗'} ${escapeHtml(c.name)}</span>
                            <span class="test-case-status ${c.status}">${c.status}</span>
                        </div>
                        ${c.details ? `<div class="test-case-details">${escapeHtml(c.details)}</div>` : ''}
                    </div>
                `).join('')}
            </div>
        `;
    }

    // Generated Pytest Suite Code
    if (report.generated_tests) {
        html += `
            <div class="result-block">
                <h4>📋 Pytest Suite Code</h4>
                <div class="code-preview-box">${escapeHtml(report.generated_tests)}</div>
            </div>
        `;
    }

    container.innerHTML = html;
}

// -------------------------------------------------------------
// Auth & Modal Management
// -------------------------------------------------------------
$('auth-toggle-btn').addEventListener('click', () => {
    if (localStorage.getItem(tokenKey)) {
        logout();
    } else {
        openAuthModal();
    }
});

$('close-auth-modal').addEventListener('click', closeAuthModal);

$('toggle-auth').addEventListener('click', () => {
    registerMode = !registerMode;
    document.querySelectorAll('.register-only').forEach(el => el.classList.toggle('hidden', !registerMode));
    $('auth-submit').textContent = registerMode ? 'Create account' : 'Sign in';
    $('toggle-auth').textContent = registerMode ? 'Already have an account? Sign in' : 'Create account';
    $('auth-modal-title').textContent = registerMode ? 'Create a new account' : 'Sign in to your account';
    $('auth-form').reset();
    setMessage('auth-message', '');
});

$('toggle-password').addEventListener('click', () => {
    const password = $('password');
    const isVisible = password.type === 'text';
    password.type = isVisible ? 'password' : 'text';
    $('toggle-password').textContent = isVisible ? 'Show' : 'Hide';
});

$('auth-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    setMessage('auth-message', 'Authenticating...');
    const payload = { email: $('email').value, password: $('password').value };
    if (registerMode) {
        payload.username = $('username').value || $('email').value.split('@')[0];
        payload.password_confirm = payload.password;
    }
    try {
        const endpoint = registerMode ? '/auth/register/' : '/auth/login/';
        const data = await api(endpoint, {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        localStorage.setItem(tokenKey, data.tokens?.access || data.access);
        localStorage.setItem(refreshKey, data.tokens?.refresh || data.refresh);
        closeAuthModal();
        updateAuthStatus();
        if (currentTab === 'projects') loadProjects();
    } catch (error) {
        setMessage('auth-message', error.message, 'error');
    }
});

function openAuthModal() {
    $('auth-modal').classList.remove('hidden');
}

function closeAuthModal() {
    $('auth-modal').classList.add('hidden');
    setMessage('auth-message', '');
}

function logout() {
    localStorage.removeItem(tokenKey);
    localStorage.removeItem(refreshKey);
    selectedProject = null;
    updateAuthStatus();
    if (currentTab === 'projects') switchTab('studio');
}

$('logout').addEventListener('click', logout);

function updateAuthStatus() {
    const hasToken = !!localStorage.getItem(tokenKey);
    $('auth-toggle-btn').textContent = hasToken ? 'Sign out' : 'Sign in';
    $('user-display').classList.toggle('hidden', !hasToken);
    if (hasToken) {
        $('user-display').textContent = 'Workspace Active';
    }
}

// -------------------------------------------------------------
// Projects Workspace Logic
// -------------------------------------------------------------
$('new-project').addEventListener('click', () => $('project-form').classList.toggle('hidden'));

$('subtab-diff').addEventListener('click', () => {
    $('subtab-diff').classList.add('active');
    $('subtab-audit').classList.remove('active');
    $('analysis-form').classList.remove('hidden');
    $('audit-form').classList.add('hidden');
});

$('subtab-audit').addEventListener('click', () => {
    $('subtab-audit').classList.add('active');
    $('subtab-diff').classList.remove('active');
    $('audit-form').classList.remove('hidden');
    $('analysis-form').classList.add('hidden');
});

async function loadProjects() {
    try {
        projects = await api('/projects/');
        renderProjects();
        if (projects.length && !selectedProject) {
            selectProject(projects[0].id);
        }
    } catch (error) {
        setMessage('project-message', error.message, 'error');
    }
}

function renderProjects() {
    const list = $('project-list');
    list.innerHTML = projects.length
        ? projects.map((p) => `
            <button class="project-item ${selectedProject?.id === p.id ? 'active' : ''}" data-id="${p.id}">
                <strong>${escapeHtml(p.name)}</strong>
                <span>${p.language} · ${p.files_count ?? 0} files</span>
            </button>
        `).join('')
        : '<p style="padding: 10px; color: var(--muted); font-size: 13px;">No projects yet. Click + to add one.</p>';

    list.querySelectorAll('[data-id]').forEach((btn) => {
        btn.addEventListener('click', () => selectProject(btn.dataset.id));
    });
}

function selectProject(id) {
    selectedProject = projects.find((p) => p.id === id);
    if (!selectedProject) return;
    $('selected-project').textContent = selectedProject.name;
    $('selected-project-meta').textContent = `${selectedProject.language} project · ${selectedProject.analyses_count ?? 0} analyses`;
    renderProjects();
}

$('project-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
        const project = await api('/projects/', {
            method: 'POST',
            body: JSON.stringify({
                name: $('project-name').value,
                language: $('project-language').value,
                description: $('project-description').value
            })
        });
        $('project-form').reset();
        $('project-form').classList.add('hidden');
        projects.unshift(project);
        selectProject(project.id);
        setMessage('project-message', 'Project created successfully.', 'success');
    } catch (error) {
        setMessage('project-message', error.message, 'error');
    }
});

$('analysis-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!selectedProject) {
        setMessage('analysis-message', 'Please select or create a project first.', 'error');
        return;
    }
    setMessage('analysis-message', 'Mapping dependencies and running impact analysis...');
    try {
        const job = await api(`/projects/${selectedProject.id}/analyses/`, {
            method: 'POST',
            body: JSON.stringify({
                analysis_type: 'CUSTOM_DIFF',
                diff_content: $('diff-content').value
            })
        });
        renderDiffResult(job.result);
        setMessage('analysis-message', 'Analysis complete.', 'success');
        $('risk-pill').textContent = job.result.overall_risk_score;
    } catch (error) {
        setMessage('analysis-message', error.message, 'error');
    }
});

$('audit-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!selectedProject) {
        setMessage('audit-message', 'Please select or create a project first.', 'error');
        return;
    }
    setMessage('audit-message', 'Scanning source and running test audit...');
    try {
        const formData = new FormData();
        const archive = $('project-archive').files[0];
        if (archive) formData.append('archive', archive);
        if ($('source-code').value.trim()) {
            formData.append('source_path', $('source-path').value || 'main.py');
            formData.append('source_code', $('source-code').value);
            formData.append('test_path', $('test-path').value || 'test_main.py');
            formData.append('test_code', $('test-code').value);
        }
        const job = await api(`/projects/${selectedProject.id}/audit/`, {
            method: 'POST',
            body: formData
        });
        renderAuditResult(job.result);
        setMessage('audit-message', 'Audit complete.', 'success');
        $('risk-pill').textContent = job.result.overall_risk_score;
    } catch (error) {
        setMessage('audit-message', error.message, 'error');
    }
});

function renderDiffResult(result) {
    const summary = result.summary || {};
    const rationales = summary.risk_rationales || [];
    $('result').className = 'result';
    $('result').innerHTML = `
        <div class="section-top">
            <div>
                <span class="kicker">IMPACT REPORT</span>
                <h3>${result.overall_risk_score} risk · ${result.risk_score_numeric}/100</h3>
            </div>
        </div>
        <div class="test-summary-bar" style="margin-top: 15px;">
            <div class="test-metric"><strong>${result.total_changed_files}</strong><span>Changed files</span></div>
            <div class="test-metric"><strong>${result.total_changed_components}</strong><span>Changed symbols</span></div>
            <div class="test-metric"><strong>${result.total_impacted_components}</strong><span>Impacted symbols</span></div>
            <div class="test-metric"><strong>${result.total_affected_tests}</strong><span>Affected tests</span></div>
        </div>
        <h4>Risk Rationales</h4>
        ${rationales.map((item) => `<div class="risk-card MEDIUM"><p class="risk-desc">${escapeHtml(item)}</p></div>`).join('') || '<p>No additional risk rationale recorded.</p>'}
    `;
}

function renderAuditResult(result) {
    const audit = result.summary?.audit;
    if (!audit) {
        renderDiffResult(result);
        return;
    }
    const failures = audit.failures || [];
    $('result').className = 'result';
    $('result').innerHTML = `
        <div class="section-top">
            <div>
                <span class="kicker">PROJECT AUDIT</span>
                <h3>${escapeHtml(audit.conclusion)}</h3>
            </div>
        </div>
        <div class="test-summary-bar" style="margin-top: 15px;">
            <div class="test-metric"><strong>${audit.source_files}</strong><span>Source files</span></div>
            <div class="test-metric"><strong>${audit.tests_collected}</strong><span>Tests found</span></div>
            <div class="test-metric"><strong style="color: var(--green);">${audit.tests_passed}</strong><span>Passed</span></div>
            <div class="test-metric"><strong style="color: var(--red);">${audit.tests_failed + audit.tests_errors}</strong><span>Failures</span></div>
        </div>
        ${failures.length ? `
            <h4>Failure Details</h4>
            ${failures.map((f) => `
                <div class="risk-card HIGH">
                    <span class="risk-title">${escapeHtml(f.test_name)} (${escapeHtml(f.file_path)}${f.line_number ? `:${f.line_number}` : ''})</span>
                    <p class="risk-desc">${escapeHtml(f.error_type)}: ${escapeHtml(f.message)}</p>
                    <div style="font-size: 12px; margin-top: 6px; color: var(--muted);">
                        <b>Suggested fix:</b> ${escapeHtml(f.suggested_fix || 'Check assertion and return values')}
                    </div>
                </div>
            `).join('')}
        ` : '<p style="color: var(--green); font-weight: 600;">All discovered tests passed successfully.</p>'}
    `;
}

// Initialize on page load
updateAuthStatus();
// Default sample code on startup
if (!$('live-source-code').value) {
    $('live-source-code').value = 
`def add(a, b):
    """Calculates the sum of two numbers."""
    return a + b

def divide(a, b):
    """Divides a by b."""
    return a / b`;
}
