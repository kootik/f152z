/**
 * js/api.js
 * Centralized module for all backend API interactions.
 * Version: 2.0 (Refactored with robust CSRF handling)
 */

import { setCurrentPageResults, setAllAbandonedSessions, setPaginationState, resultsPerPage, setDashboardStats } from './state.js'; // <-- ДОБАВЛЕН setDashboardStats
import * as ui from './ui.js';
import { /*...,*/ registrySortKey, registrySortDir } from './state.js'; 

class APIClient {
    constructor(baseURL = '') {
        this.baseURL = baseURL;
        this.socket = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
    }
// --- 👇 НОВЫЙ МЕТОД ДЛЯ ЗАГРУЗКИ СТАТИСТИКИ 👇 ---
    async fetchFilteredStats() {
        try {
            // Получаем текущие фильтры (можно взять из state или с UI)
            const statusFilter = document.getElementById('statusFilter');
            const status = statusFilter ? statusFilter.value : '';
            const presetFilter = document.querySelector('.preset-btn.active');
            const preset = presetFilter ? presetFilter.dataset.preset : 'all';

            // Вызываем новый эндпоинт с фильтрами
            return await this.safeFetch(`/api/get_filtered_stats?status=${status}&preset=${preset}`);
        } catch (error) {
            console.error('Ошибка загрузки статистики:', error);
            // Возвращаем null или объект с нулями, чтобы UI мог обработать ошибку
            return { totalTests: 0, averageScore: 0, anomaliesCount: 0, uniqueUsers: 0 };
        }
    }
    // --- 👆 КОНЕЦ НОВОГО МЕТОДА 👆 ---
    // =========================================================================
    // CORE & WEBSOCKETS
    // =========================================================================

    /**
     * Retrieves the CSRF token.
     * First, it tries to get it from a <meta> tag, which is the most reliable method.
     * As a fallback, it checks for a cookie.
     */
    getCsrfToken() {
        // 1. Try to get token from meta tag (best method)
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) {
            return meta.getAttribute('content');
        }

        // 2. Fallback to cookie method
        console.warn("CSRF meta tag not found. Falling back to cookie method.");
        const value = `; ${document.cookie}`;
        const parts = value.split(`; csrf_token=`); // Note: check the actual cookie name
        if (parts.length === 2) {
            return parts.pop().split(';').shift();
        }
        
        return null; // Return null if not found
    }

    /**
     * A robust wrapper around the fetch API that handles CSRF, retries, and JSON parsing.
     */
    async safeFetch(url, options = {}) {
        // <--- ИЗМЕНЕНИЕ: Используем новую надежную функцию для получения токена --->
        const csrfToken = this.getCsrfToken();
        
        const headers = {
            'Content-Type': 'application/json',
            // Only add the CSRF token header if the token was found
            ...(csrfToken && { 'X-CSRFToken': csrfToken }),
            ...options.headers,
        };

        let lastError;
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                const response = await fetch(`${this.baseURL}${url}`, {
                    ...options,
                    headers,
                    credentials: 'same-origin'
                });

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status} - ${response.statusText}`);
                }

                return response.json(); // Возвращаем сразу JSON
            } catch (error) {
                lastError = error;
                if (attempt < 2) {
                    await new Promise(resolve => setTimeout(resolve, Math.pow(2, attempt) * 1000));
                }
            }
        }
        throw lastError;
    }

    connectWebSocket(onUpdate) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}`;
        
        this.socket = io(wsUrl, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionDelayMax: 5000,
            reconnectionAttempts: this.maxReconnectAttempts
        });

        this.socket.on('connect', () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
        });

        this.socket.on('update_needed', (data) => {
            if (onUpdate) onUpdate(data);
        });

        this.socket.on('disconnect', () => {
            console.log('WebSocket disconnected');
        });

        this.socket.on('connect_error', (error) => {
            console.error('WebSocket connection error:', error);
            this.reconnectAttempts++;
        });

        return this.socket;
    }
    
    disconnectWebSocket() {
        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }
    }


    // =========================================================================
    // API METHODS WITH UI INTERACTION (These remain unchanged)
    // =========================================================================

async loadInitialData(page = 1) {
    ui.showLoading();
    try {
        const status = document.getElementById('statusFilter')?.value || '';
        const preset = document.querySelector('.preset-btn.active')?.dataset.preset || 'all';

        // Запускаем оба запроса параллельно
        const [data, stats] = await Promise.all([
            this.safeFetch(`/api/get_results?page=${page}&per_page=${resultsPerPage}&status=${status}&preset=${preset}`),
            this.fetchDashboardStats()
        ]);

        // Обрабатываем результаты первого запроса
        setCurrentPageResults(data.results);
        setPaginationState(data.page, data.per_page, data.total);

        // Обрабатываем результаты второго запроса
        setDashboardStats(stats); 
        ui.renderDashboardWidgets(stats);

        // Рендерим UI с данными из обоих запросов
        ui.renderDataTable(data.results); 
        ui.renderPaginationControls();
        ui.renderDashboardCharts();
        ui.applyFiltersAndRender(); 

    } catch (error) {
        // ... (ваша текущая логика обработки ошибок) ...
        console.error('Ошибка загрузки результатов:', error);
        ui.renderDataTable([]);
        ui.renderPaginationControls();
        ui.renderDashboardCharts();
        ui.renderDashboardWidgets(null); // Показать заглушки виджетов при ошибке
        } finally {
            ui.hideLoading();
        }
    } 




    async loadAndRenderAbandonedSessions() {
        const container = document.getElementById('abandoned-sessions-container');
        container.innerHTML = '<div class="loading">Загрузка прерванных сессий...</div>';
        try {
            const sessions = await this.safeFetch('/api/get_abandoned_sessions');
            setAllAbandonedSessions(sessions);
            ui.renderAbandonedSessions('all');

        } catch (error) {
            console.error("Failed to load abandoned sessions:", error);
            container.innerHTML = '<p class="error-message">Не удалось загрузить данные.</p>';
        }
    }

    async loadAndRenderBehaviorAnalysis() {
        const container = document.getElementById('behavior-analysis-container');
        container.innerHTML = '<div class="loading">Выполняется сложный анализ...</div>';
        try {
            const sessions = await this.safeFetch('/api/get_behavior_analysis');
            ui.renderBehaviorAnalysis(sessions);
        } catch (error) {
            console.error("Failed to load behavior analysis:", error);
            container.innerHTML = '<p class="error-message">Не удалось выполнить анализ.</p>';
        }
    }

    async loadAndRenderCertificates(page = 1) {
            const container = document.getElementById('registry-container');
            container.innerHTML = '<div class="loading">Загрузка реестра...</div>';
        
        // ❗️ ВНЕШНИЙ 'try {' БЫЛ УДАЛЕН ОТСЮДА (он был лишним и вызывал ошибку)

        // --- 👇 ИЗМЕНЕНИЕ: Добавляем параметры сортировки в URL 👇 ---
        // ПРИМЕЧАНИЕ: Бэкенд должен быть обновлен, чтобы обрабатывать sort_key и sort_dir
        const sortParams = `&sort_key=${registrySortKey}&sort_dir=${registrySortDir}`;
        const yearFilter = document.getElementById('registryYearFilter')?.value || '';
        const monthFilter = document.getElementById('registryMonthFilter')?.value || '';

        // Используем URLSearchParams
        const params = new URLSearchParams({
            page: page,
            per_page: 50,
            sort_key: registrySortKey,
            sort_dir: registrySortDir
        });

        // .append() безопасно добавляет, только если значение не пустое
        if (yearFilter) params.append('year', yearFilter);
        if (monthFilter) params.append('month', monthFilter);

        // ❗️ Это ЕДИНСТВЕННЫЙ 'try...catch', который здесь нужен
        try {
            // Передаем .toString() в safeFetch
            const data = await this.safeFetch(`/api/get_certificates?${params.toString()}`);
            ui.renderCertificatesTable(data);
        } catch (error) {
            console.error("Failed to load certificates:", error);
            container.innerHTML = '<p class="error-message">Не удалось загрузить реестр.</p>';
        }
    } // <-- ❗️ ЭТА ЗАКРЫВАЮЩАЯ СКОБКА БЫЛА ПРОПУЩЕНА






    async showEventLog(sessionId) {
        ui.openEventLogModal(sessionId);
        const content = document.getElementById('eventLogContent');
        content.innerHTML = '<div class="loading">Загрузка событий...</div>';
        try {
            const events = await this.safeFetch(`/api/get_events/${sessionId}`);
            ui.renderEventLog(events);
        } catch (error) {
            console.error("Failed to fetch event log:", error);
            content.innerHTML = '<p class="error-message">Не удалось загрузить журнал событий.</p>';
        }
    }
    async runServerDtwAnalysis(sessionIds) {
        const dtwContainer = document.getElementById('dtw-analysis-results');
        if (dtwContainer) {
            dtwContainer.innerHTML = `<div class="loading">Выполняется сложный анализ DTW на сервере...</div>`;
        }
        try {
            const data = await this.safeFetch('/api/analyze_mouse', {
                method: 'POST',
                body: JSON.stringify({ session_ids: sessionIds })
            });
            return data;
        } catch (error) {
            console.error("Ошибка при выполнении серверного анализа:", error);
            if (dtwContainer) {
                dtwContainer.innerHTML = `<div class="comparison-analysis-placeholder error-message"><h4>Ошибка анализа</h4><p>Не удалось получить результаты с сервера.</p></div>`;
            }
            return null;
        }
    }
    
    async logEvent(eventData) {
        try {
            return await this.safeFetch('/api/log_event', {
                method: 'POST',
                body: JSON.stringify(eventData)
            });
        } catch (error) {
            console.error('Failed to log event:', error);
            return null;
        }
    }
    
    async saveResults(data) {
        try {
            return await this.safeFetch('/api/save_results', {
                method: 'POST',
                body: JSON.stringify(data)
            });
        } catch(error) {
            console.error('Failed to save results:', error);
            throw error;
        }
    }

    async fetchDashboardStats() {

        try {
            return await this.safeFetch('/api/get_dashboard_stats');
        } catch (error) {
            console.error('Ошибка загрузки статистики для виджетов:', error);
            return null; // Возвращаем null, чтобы UI мог обработать
        }
    }
    async fetchGlobalSearch(query) {
        try {
            return await this.safeFetch(`/api/global_search?q=${encodeURIComponent(query)}`);
        } catch (error) {
            console.error('Ошибка глобального поиска:', error);
            return { users: [], sessions: [] }; // Возвращаем пустой объект при ошибке
        }
    }
/**
     * Fetches the full, detailed results for a specific session.
     * @param {string} sessionId The ID of the session to fetch.
     */
    async fetchFullResultDetails(sessionId) {
        try {
            return await this.safeFetch(`/api/get_full_result/${sessionId}`);
        } catch (error) {
            console.error(`Ошибка при загрузке полных данных для сессии ${sessionId}:`, error);
            return null;
        }
    }
    async fetchSettings() {
        try {
            return await this.safeFetch('/api/settings');
        } catch (error) {
            console.error('Ошибка загрузки системных настроек:', error);
            ui.showNotification('Не удалось загрузить настройки', 'danger');
            return null;
        }
    }

    /**
     * Сохраняет системные настройки
     * @param {Object<string, string>} settingsData 
     */
    async saveSettings(settingsData) {
        try {
            return await this.safeFetch('/api/settings', {
                method: 'POST',
                body: JSON.stringify(settingsData)
            });
        } catch (error) {
            console.error('Ошибка сохранения системных настроек:', error);
            ui.showNotification('Ошибка при сохранении настроек', 'danger');
            throw error; // Передаем ошибку дальше, чтобы кнопка не разблокировалась
        }
    }
    // --- 👆 ---
}

const apiClient = new APIClient();
export default apiClient;
window.apiClient = apiClient;

// --- ИСПРАВЛЕНИЕ: Лишняя '}' в конце файла УДАЛЕНА ---
