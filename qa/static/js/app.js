/**
 * 新能源知识图谱问答系统前端交互逻辑
 *
 * 功能：
 * 1. 示例问题渲染与一键发送
 * 2. 单轮问答模式：调用 /qa 接口并渲染回答
 * 3. KG 对比模式：同时展示纯 LLM 回答与 KG+RAG 回答（第二阶段启用 KG）
 * 4. Markdown 渲染、复制答案、清空对话、加载动画
 */

// ==================== 配置 ====================
const CONFIG = {
    apiEndpoint: '/qa',
    defaultMode: 'single', // 'single' | 'compare'
    exampleQuestions: [
        '磷酸铁锂电池有哪些优点？',
        '纯电动汽车和增程式电动车有什么区别？',
        '氢能的主要应用场景有哪些？',
        '光伏产业链包括哪些环节？',
        '风电技术有哪些关键设备？',
        '当前知识库有多少种新能源技术？',
    ],
};

// ==================== DOM 元素 ====================
const elements = {
    welcomeSection: document.getElementById('welcomeSection'),
    messagesContainer: document.getElementById('messagesContainer'),
    exampleCards: document.getElementById('exampleCards'),
    questionInput: document.getElementById('questionInput'),
    sendBtn: document.getElementById('sendBtn'),
    clearBtn: document.querySelector('.clear-btn'),
    modeBtns: document.querySelectorAll('.mode-btn'),
    appContainer: document.querySelector('.app-container'),
    loadingTemplate: document.getElementById('loadingTemplate'),
};

let currentMode = CONFIG.defaultMode;
let isLoading = false;

// ==================== 初始化 ====================
function init() {
    renderExampleCards();
    bindEvents();
    autoResizeTextarea();
}

// ==================== 事件绑定 ====================
function bindEvents() {
    // 发送按钮
    elements.sendBtn.addEventListener('click', handleSend);

    // 输入框键盘事件
    elements.questionInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    // 自动调整输入框高度
    elements.questionInput.addEventListener('input', autoResizeTextarea);

    // 清空对话
    elements.clearBtn.addEventListener('click', clearConversation);

    // 模式切换
    elements.modeBtns.forEach((btn) => {
        btn.addEventListener('click', () => switchMode(btn.dataset.mode));
    });
}

// ==================== 示例问题 ====================
function renderExampleCards() {
    elements.exampleCards.innerHTML = CONFIG.exampleQuestions
        .map((q) => `<div class="example-card" data-question="${escapeHtml(q)}">${escapeHtml(q)}</div>`)
        .join('');

    elements.exampleCards.querySelectorAll('.example-card').forEach((card) => {
        card.addEventListener('click', () => {
            elements.questionInput.value = card.dataset.question;
            autoResizeTextarea();
            handleSend();
        });
    });
}

// ==================== 模式切换 ====================
function switchMode(mode) {
    currentMode = mode;

    elements.modeBtns.forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });

    if (mode === 'compare') {
        elements.appContainer.classList.add('compare-mode');
        ensureCompareContainer();
    } else {
        elements.appContainer.classList.remove('compare-mode');
    }

    clearConversation();
}

function ensureCompareContainer() {
    if (document.querySelector('.compare-container')) return;

    const compareHtml = `
        <div class="compare-container">
            <div class="compare-panel llm">
                <div class="compare-panel-header">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M12 16v-4M12 8h.01"/>
                    </svg>
                    纯 LLM 回答（基线）
                </div>
                <div class="compare-panel-body" id="llmPanelBody">
                    <div class="compare-placeholder">请在下方输入问题，查看纯 LLM 回答</div>
                </div>
            </div>
            <div class="compare-panel kg">
                <div class="compare-panel-header">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M12 6v6l4 2"/>
                    </svg>
                    KG + LLM 回答（知识图谱增强）
                </div>
                <div class="compare-panel-body" id="kgPanelBody">
                    <div class="compare-placeholder">请在下方输入问题，查看知识图谱增强回答<br>（第二阶段接入 Neo4j 后生效）</div>
                </div>
            </div>
        </div>
    `;

    elements.appContainer.insertBefore(
        htmlToElement(compareHtml),
        elements.messagesContainer.nextSibling
    );
}

// ==================== 发送问题 ====================
async function handleSend() {
    const question = elements.questionInput.value.trim();
    if (!question || isLoading) return;

    // 切换到消息视图
    elements.welcomeSection.style.display = 'none';
    if (currentMode === 'single') {
        elements.messagesContainer.classList.add('active');
    }

    // 显示用户消息
    if (currentMode === 'single') {
        appendUserMessage(question);
    }

    // 清空输入框
    elements.questionInput.value = '';
    autoResizeTextarea();

    if (currentMode === 'single') {
        await sendSingle(question);
    } else {
        await sendCompare(question);
    }
}

async function sendSingle(question) {
    showLoading();
    isLoading = true;
    toggleInput(false);

    try {
        const result = await callQA(question);
        hideLoading();
        appendAssistantMessage(result);
    } catch (error) {
        hideLoading();
        appendErrorMessage(error.message);
    } finally {
        isLoading = false;
        toggleInput(true);
    }
}

async function sendCompare(question) {
    const llmPanel = document.getElementById('llmPanelBody');
    const kgPanel = document.getElementById('kgPanelBody');

    llmPanel.innerHTML = renderPanelLoading();
    kgPanel.innerHTML = renderPanelLoading();
    isLoading = true;
    toggleInput(false);

    try {
        // 当前阶段：两个面板都调用同一个 /qa 接口
        // 第二阶段：可分别为纯 LLM 与 KG+RAG 调用不同接口或参数
        const [llmResult, kgResult] = await Promise.all([
            callQA(question),
            callQA(question),
        ]);

        llmPanel.innerHTML = renderPanelAnswer(llmResult, 'llm');
        kgPanel.innerHTML = renderPanelAnswer(kgResult, 'kg');
    } catch (error) {
        llmPanel.innerHTML = renderPanelError(error.message);
        kgPanel.innerHTML = renderPanelError(error.message);
    } finally {
        isLoading = false;
        toggleInput(true);
    }
}

// ==================== API 调用 ====================
async function callQA(question) {
    const response = await fetch(CONFIG.apiEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `请求失败: ${response.status}`);
    }

    return await response.json();
}

// ==================== 消息渲染 ====================
function appendUserMessage(text) {
    const html = `
        <div class="message user-message">
            <div class="avatar">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                    <circle cx="12" cy="7" r="4"/>
                </svg>
            </div>
            <div class="message-content">${escapeHtml(text)}</div>
        </div>
    `;
    elements.messagesContainer.appendChild(htmlToElement(html));
    scrollToBottom();
}

function appendAssistantMessage(data) {
    const messageEl = createAssistantMessageElement(data);
    elements.messagesContainer.appendChild(messageEl);
    scrollToBottom();
}

function appendErrorMessage(message) {
    const html = `
        <div class="message assistant-message">
            <div class="avatar">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
            </div>
            <div class="message-content" style="color: #dc2626;">
                <p>请求出错：${escapeHtml(message)}</p>
                <p style="font-size: 0.85rem; margin-top: 8px;">请检查后端服务是否已启动（python -m qa.main）。</p>
            </div>
        </div>
    `;
    elements.messagesContainer.appendChild(htmlToElement(html));
    scrollToBottom();
}

function createAssistantMessageElement(data) {
    const answerHtml = renderMarkdown(data.answer);
    const entityTags = (data.entities || [])
        .map((e) => `<span class="meta-tag">${escapeHtml(e.name)}</span>`)
        .join('');

    const sourceText = data.source === 'llm'
        ? `LLM: ${data.llm_model || 'qwen-turbo'}`
        : '本地兜底';

    const html = `
        <div class="message assistant-message">
            <div class="avatar">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <path d="M12 6v6l4 2"/>
                </svg>
            </div>
            <div class="message-content">
                <div class="answer-body">${answerHtml}</div>
                <div class="message-meta">
                    <span class="meta-tag intent">意图：${escapeHtml(data.intent)}</span>
                    ${entityTags}
                    <span class="meta-tag source">来源：${escapeHtml(sourceText)}</span>
                    <span class="meta-tag">耗时：${data.response_time_ms}ms</span>
                    <button class="copy-btn" data-answer="${escapeHtml(data.answer)}">复制</button>
                </div>
            </div>
        </div>
    `;

    const el = htmlToElement(html);
    el.querySelector('.copy-btn').addEventListener('click', handleCopy);
    return el;
}

// ==================== 对比面板渲染 ====================
function renderPanelLoading() {
    return `
        <div class="loading-dots" style="justify-content: center; padding: 40px 0;">
            <span></span><span></span><span></span>
        </div>
    `;
}

function renderPanelAnswer(data, type) {
    const answerHtml = renderMarkdown(data.answer);
    const sourceText = data.source === 'llm'
        ? `${data.llm_model || 'qwen-turbo'} · ${data.response_time_ms}ms`
        : `本地兜底 · ${data.response_time_ms}ms`;

    return `
        <div class="answer-body" style="margin-bottom: 12px;">${answerHtml}</div>
        <div class="message-meta" style="border-top: 1px dashed var(--border); padding-top: 10px;">
            <span class="meta-tag intent">意图：${escapeHtml(data.intent)}</span>
            <span class="meta-tag source">来源：${escapeHtml(sourceText)}</span>
            <button class="copy-btn" data-answer="${escapeHtml(data.answer)}">复制</button>
        </div>
    `;
}

function renderPanelError(message) {
    return `<div style="color: #dc2626; padding: 20px;">请求出错：${escapeHtml(message)}</div>`;
}

// ==================== 工具函数 ====================
function showLoading() {
    const loadingEl = elements.loadingTemplate.content.cloneNode(true).firstElementChild;
    elements.messagesContainer.appendChild(loadingEl);
    scrollToBottom();
}

function hideLoading() {
    const loadingEl = elements.messagesContainer.querySelector('.loading-message');
    if (loadingEl) loadingEl.remove();
}

function toggleInput(enabled) {
    elements.sendBtn.disabled = !enabled;
    elements.questionInput.disabled = !enabled;
    if (enabled) elements.questionInput.focus();
}

function clearConversation() {
    elements.messagesContainer.innerHTML = '';
    elements.messagesContainer.classList.remove('active');
    elements.welcomeSection.style.display = 'block';

    const llmPanel = document.getElementById('llmPanelBody');
    const kgPanel = document.getElementById('kgPanelBody');
    if (llmPanel && kgPanel) {
        llmPanel.innerHTML = '<div class="compare-placeholder">请在下方输入问题，查看纯 LLM 回答</div>';
        kgPanel.innerHTML = '<div class="compare-placeholder">请在下方输入问题，查看知识图谱增强回答<br>（第二阶段接入 Neo4j 后生效）</div>';
    }
}

function scrollToBottom() {
    const main = document.querySelector('.chat-main');
    main.scrollTop = main.scrollHeight;
}

function autoResizeTextarea() {
    const textarea = elements.questionInput;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
}

function renderMarkdown(text) {
    if (!text) return '';
    const rawHtml = marked.parse(text);
    return DOMPurify.sanitize(rawHtml);
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function htmlToElement(html) {
    const template = document.createElement('template');
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
}

async function handleCopy(e) {
    const text = e.target.dataset.answer;
    try {
        await navigator.clipboard.writeText(text);
        e.target.textContent = '已复制';
        e.target.classList.add('copied');
        setTimeout(() => {
            e.target.textContent = '复制';
            e.target.classList.remove('copied');
        }, 2000);
    } catch (err) {
        console.error('复制失败', err);
    }
}

// ==================== 启动 ====================
document.addEventListener('DOMContentLoaded', init);
