/**
 * js/api.js
 * Этот модуль является единственной точкой для взаимодействия с бэкенд API.
 * Все функции здесь используют `fetch` для получения или отправки данных.
 * Они НЕ изменяют DOM напрямую, а возвращают полученные данные, которые
 * затем передаются в модуль ui.js для отрисовки.
 */

import { setCurrentPageResults, setAllAbandonedSessions, setPaginationState, resultsPerPage } from './state.js';
import * as ui from './ui.js';

// =============================================================================
// CORE API FUNCTIONS (Fetch Wrapper with CSRF Protection)
// =============================================================================

/**
 * Вспомогательная функция для получения значения cookie по имени.
 * @param {string} name - Имя cookie для поиска.
 * @returns {string|undefined} Значение cookie или undefined, если не найдено.
 */
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
}

/**
 * Безопасная обертка для `fetch`, которая автоматически добавляет
 * CSRF-токен в заголовки для POST, PUT, DELETE запросов.
 * @param {string} url - URL для запроса.
 * @param {object} options - Опции для fetch (method, body, etc.).
 * @returns {Promise<Response>}
 */
async function safeFetch(url, options = {}) {
    // Получаем CSRF-токен из cookie, который устанавливает сервер.
    const csrfToken = getCookie('csrf_token');

    const headers = {
        'Content-Type': 'application/json',
        // 'X-CSRFToken' - это стандартный заголовок, который проверяет Flask-WTF
        'X-CSRFToken': csrfToken,
        ...options.headers,
    };

    const response = await fetch(url, { ...options, headers });
    
    if (!response.ok) {
        // В случае ошибки создаем кастомное сообщение для лучшей отладки.
        throw new Error(`HTTP ${response.status} - ${response.statusText}`);
    }
    
    return response;
}

// =============================================================================
// API ENDPOINT HANDLERS
// =============================================================================

/**
 * Получает основные данные о результатах тестов с пагинацией.
 * @param {number} page - Номер страницы для загрузки.
 */
export async function loadInitialData(page = 1) {
    ui.showLoading();
    try {
        const response = await fetch(`/api/get_results?page=${page}&per_page=${resultsPerPage}`);
        if (!response.ok) throw new Error(`HTTP ${response.status} - ${response.statusText}`);
        
        const data = await response.json();
        
        // Обновляем глобальное состояние
        setCurrentPageResults(data.results);
        setPaginationState(data.page, data.per_page, data.total);

        // Передаем данные в UI модуль для отрисовки
        ui.renderDashboardWidgets();
        ui.renderDataTable(data.results);
        ui.renderPaginationControls();
        
    } catch (error) {
        console.error('Ошибка загрузки результатов:', error);
        const tbody = document.getElementById('results-table-body');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" class="loading" style="color:var(--danger)">⚠️ Ошибка загрузки данных. Убедитесь, что бэкенд запущен.</td></tr>`;
        }
    } finally {
        ui.hideLoading();
    }
}

/**
 * Загружает и инициирует отрисовку прерванных сессий.
 */
export async function loadAndRenderAbandonedSessions() {
    const container = document.getElementById('abandoned-sessions-container');
    container.innerHTML = '<div class="loading">Загрузка прерванных сессий...</div>';
    try {
        const response = await fetch('/api/get_abandoned_sessions');
        if (!response.ok) throw new Error('Network error');
        
        const sessions = await response.json();
        setAllAbandonedSessions(sessions);
        ui.renderAbandonedSessions('all'); // По умолчанию показываем все

    } catch (error) {
        console.error("Failed to load abandoned sessions:", error);
        container.innerHTML = '<p style="color: var(--danger); text-align:center;">Не удалось загрузить данные.</p>';
    }
}

/**
 * Загружает и инициирует отрисовку результатов поведенческого анализа.
 */
export async function loadAndRenderBehaviorAnalysis() {
    const container = document.getElementById('behavior-analysis-container');
    container.innerHTML = '<div class="loading">Выполняется сложный анализ...</div>';
    try {
        const response = await fetch('/api/get_behavior_analysis');
        if (!response.ok) throw new Error('Network error');
        const sessions = await response.json();
        ui.renderBehaviorAnalysis(sessions);
    } catch (error) {
        console.error("Failed to load behavior analysis:", error);
        container.innerHTML = '<p style="color: var(--danger); text-align:center;">Не удалось выполнить анализ.</p>';
    }
}

/**
 * Загружает и инициирует отрисовку реестра аттестатов.
 */
export async function loadAndRenderCertificates() {
    const container = document.getElementById('registry-container');
    container.innerHTML = '<div class="loading">Загрузка реестра...</div>';
    try {
        const response = await fetch('/api/get_certificates');
        if (!response.ok) throw new Error('Network error');
        const certificates = await response.json();
        ui.renderCertificatesTable(certificates);
    } catch (error) {
        console.error("Failed to load certificates:", error);
        container.innerHTML = '<p style="color: var(--danger); text-align:center;">Не удалось загрузить реестр.</p>';
    }
}

/**
 * Загружает и отображает журнал событий для конкретной сессии.
 * @param {string} sessionId - ID сессии, для которой нужен журнал.
 */
export async function showEventLog(sessionId) {
    ui.openEventLogModal(sessionId);
    const content = document.getElementById('eventLogContent');
    content.innerHTML = '<div class="loading">Загрузка событий...</div>';
    try {
        const response = await fetch(`/api/get_events/${sessionId}`);
        if (!response.ok) throw new Error('Network response was not ok');
        const events = await response.json();
        ui.renderEventLog(events);
    } catch (error) {
        console.error("Failed to fetch event log:", error);
        content.innerHTML = '<p style="color: var(--danger);">Не удалось загрузить журнал событий.</p>';
    }
}

/**
 * Запускает сложный DTW анализ движений мыши на сервере.
 * @param {string[]} sessionIds - Массив ID сессий для анализа.
 * @returns {Promise<object|null>} Результаты анализа или null в случае ошибки.
 */
export async function runServerDtwAnalysis(sessionIds) {
    const dtwContainer = document.getElementById('dtw-analysis-results');
    if (dtwContainer) {
        dtwContainer.innerHTML = `<div class="loading">Выполняется сложный анализ DTW на сервере...</div>`;
    }
    
    try {
        // Используем safeFetch для POST-запроса с CSRF-токеном
        const response = await safeFetch('/api/analyze_mouse_from_files', {
            method: 'POST',
            body: JSON.stringify({ session_ids: sessionIds })
        });
        return await response.json();
    } catch (error) {
        console.error("Ошибка при выполнении серверного анализа:", error);
        if (dtwContainer) {
            dtwContainer.innerHTML = `<div class="comparison-analysis-placeholder" style="color:var(--danger);"><h4>Ошибка анализа</h4><p>Не удалось получить результаты с сервера.</p></div>`;
        }
        return null;
    }
}
// Вставьте этот код в конец файла api.js

/**
 * Загружает полный, детальный объект результата для одной сессии.
 * @param {string} sessionId - ID сессии.
 * @returns {Promise<object>} - Полный объект результата.
 */
export async function fetchFullResultDetails(sessionId) {
    try {
        const response = await fetch(`/api/get_full_result/${sessionId}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`Ошибка при загрузке полных данных для сессии ${sessionId}:`, error);
        // Возвращаем null или пустой объект, чтобы Promise.all не прервался
        return null;
    }
}