/**
 * js/api.js
 * Centralized module for all backend API interactions.
 * Version: 2.0 (Refactored with robust CSRF handling)
 */

import { setCurrentPageResults, setAllAbandonedSessions, setPaginationState, resultsPerPage } from './state.js';
import * as ui from './ui.js';

class APIClient {
    constructor(baseURL = '') {
        this.baseURL = baseURL;
        this.socket = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
    }

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
            // --- 👇 НОВЫЙ КОД: Читаем значение фильтра статуса 👇 ---
            const statusFilter = document.getElementById('statusFilter');
            const status = statusFilter ? statusFilter.value : '';
            // --- 👇 НОВЫЙ КОД: Получаем активный пресет 👇 ---
            const presetFilter = document.querySelector('.preset-btn.active');
            const preset = presetFilter ? presetFilter.dataset.preset : 'all';

            // --- 👇 ИЗМЕНЕНИЕ: Добавляем &status=... в URL 👇 ---
            const data = await this.safeFetch(`/api/get_results?page=${page}&per_page=${resultsPerPage}&status=${status}&preset=${preset}`);
            
            setCurrentPageResults(data.results);
            setPaginationState(data.page, data.per_page, data.total);

            // Эти функции теперь отработают с отфильтрованными с сервера данными
            // ui.renderDashboardWidgets(); // <-- Эта строка вызывала ошибку
            ui.renderDataTable(data.results); 
            ui.renderPaginationControls();
            
            // Запускаем клиентскую фильтрацию (если в полях ФИО и т.д. что-то введено)
            // Это отфильтрует уже загруженную И отфильтрованную сервером страницу
            ui.renderDashboardCharts();
            ui.applyFiltersAndRender(); 

        } catch (error) {
            console.error('Ошибка загрузки результатов:', error);
            ui.renderDataTable([]); // Показать пустую таблицу при ошибке
            ui.renderPaginationControls(); // Сбросить пагинацию
            ui.renderDashboardCharts(); // Очистить графики
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

    async loadAndRenderCertificates(page = 1) { // <-- ИЗМЕНЕНИЕ: Принимаем номер страницы
        const container = document.getElementById('registry-container');
        container.innerHTML = '<div class="loading">Загрузка реестра...</div>';
        try {
            // --- ИЗМЕНЕНИЕ: Добавляем параметры page и per_page в запрос ---
            // (Установил 50, можете изменить на другое значение по умолчанию)
            const data = await this.safeFetch(`/api/get_certificates?page=${page}&per_page=50`); 
            ui.renderCertificatesTable(data); 
        } catch (error) {
            console.error("Failed to load certificates:", error);
            container.innerHTML = '<p class="error-message">Не удалось загрузить реестр.</p>';
        }
    }

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
     * NEW: Fetches global search results.
     */
    async fetchFullResultDetails(sessionId) {
        try {
            return await this.safeFetch(`/api/get_full_result/${sessionId}`);
        } catch (error) {
            console.error(`Ошибка при загрузке полных данных для сессии ${sessionId}:`, error);
            return null;
        }
    }
}

const apiClient = new APIClient();
export default apiClient;
window.apiClient = apiClient;
