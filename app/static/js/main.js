/**
 * main.js
 * Application orchestrator with secure event handling.
 */

import { settings, updateSettings, selectedForComparison, currentPageResults, currentPage, setResultsPerPage } from './state.js';
import apiClient from './api.js';
import * as ui from './ui.js';
import * as analysis from './analysis.js';

// =============================================================================
// APPLICATION INITIALIZATION
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    console.log("▶️ DOMContentLoaded: Инициализация приложения v3.0...");
    
    loadSettings();
    initializeAppUI();
    initializeEventListeners();
    
    apiClient.loadInitialData(1);
    console.log("✅ DOMContentLoaded: Инициализация завершена.");
});

function loadSettings() {
    const saved = localStorage.getItem('analysisSettings');
    if (saved) {
        try {
            updateSettings(JSON.parse(saved));
            console.log("...Настройки загружены из localStorage.");
        } catch (e) {
            console.error("Не удалось прочитать сохраненные настройки:", e);
        }
    }
}

function initializeAppUI() {
    const savedTheme = localStorage.getItem('theme');
    const themeToggle = document.getElementById('themeToggle');
    if (savedTheme === 'dark' && themeToggle) {
        document.documentElement.setAttribute('data-theme', 'dark');
        themeToggle.textContent = '☀️';
    }
    const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
    if(isCollapsed) {
        const sidebar = document.getElementById('sidebar');
        if (sidebar) sidebar.classList.add('collapsed');
    }
    console.log("...Интерфейс инициализирован (тема, сайдбар).");
}

// =============================================================================
// EVENT LISTENERS (Event Delegation for Dynamic Content)
// =============================================================================

function initializeEventListeners() {
    console.log("⚙️ Запуск initializeEventListeners.");

    // --- НАВИГАЦИЯ В БОКОВОЙ ПАНЕЛИ ---
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            ui.switchView(item.dataset.view);
        });
    });

    // --- ВЕРХНЯЯ ПАНЕЛЬ (КНОПКИ МЕНЮ, ТЕМЫ, УВЕДОМЛЕНИЙ) ---
    const menuToggle = document.getElementById('menuToggle');
    if (menuToggle) {
        menuToggle.addEventListener('click', () => {
            const sidebar = document.getElementById('sidebar');
            if(sidebar) {
                sidebar.classList.toggle('collapsed');
                localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
            }
        });
    }

    const themeToggle = document.getElementById('themeToggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', function() {
            const newTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            this.textContent = newTheme === 'dark' ? '☀️' : '🌙';
            localStorage.setItem('theme', newTheme);
        });
    }

    const notificationBtn = document.getElementById('notificationBtn');
    if (notificationBtn) {
        notificationBtn.addEventListener('click', () => {
            const panel = document.getElementById('notificationsPanel');
            const badge = document.getElementById('notificationBadge');
            if (panel) panel.classList.toggle('active');
            if (badge) {
                badge.style.display = 'none';
                badge.textContent = '0';
            }
        });
    }

    // --- ГЛАВНЫЙ ОБРАБОТЧИК КЛИКОВ НА ВСЕЙ СТРАНИЦЕ ---
    // Используем делегирование событий для обработки кликов на динамических элементах
    document.body.addEventListener('click', (e) => {
        const target = e.target;

        // --- 1. ЛОГИКА ВЫПАДАЮЩЕГО СПИСКА (Dropdown) ---
        const dropdownToggle = target.closest('#loadOptionsToggle');
        const dropdownItem = target.closest('.dropdown-item');
        const isClickInsideDropdown = target.closest('.load-options-dropdown');

        // Закрываем список, если клик был вне его области
        if (!isClickInsideDropdown) {
            const menu = document.getElementById('loadOptionsMenu');
            if (menu?.classList.contains('active')) {
                menu.classList.remove('active');
                document.getElementById('loadOptionsToggle')?.classList.remove('active');
            }
        }
        
        if (dropdownToggle) { // Клик по кнопке для открытия/закрытия
            const menu = document.getElementById('loadOptionsMenu');
            dropdownToggle.classList.toggle('active');
            menu.classList.toggle('active');
            return; // Действие обработано
        }
        
        if (dropdownItem) { // Клик по элементу в списке
            e.preventDefault();
            const count = dropdownItem.dataset.count;
            document.getElementById('selectedValue').textContent = count === 'all' ? 'Все' : count;
            setResultsPerPage(count);
            apiClient.loadInitialData(1);
            
            // Закрываем меню после выбора
            const menu = document.getElementById('loadOptionsMenu');
            const toggle = document.getElementById('loadOptionsToggle');
            menu?.classList.remove('active');
            toggle?.classList.remove('active');
            return; // Действие обработано
        }
        
        // --- 2. ГЛОБАЛЬНЫЕ ДЕЙСТВИЯ (Закрытие модальных окон) ---
        if (target.classList.contains('modal')) {
            target.style.display = 'none';
            return;
        }
        if (target.closest('.close-btn')) {
            target.closest('.modal').style.display = 'none';
            return;
        }

        // --- 3. КНОПКИ НА ПАНЕЛИ ФИЛЬТРОВ И АНАЛИЗА ---
        const actionButton = target.closest('.btn, .preset-btn');
        if (actionButton) {
            if (actionButton.id === 'analyzeFingerprintBtn') analysis.analyzeFingerprints();
            else if (actionButton.id === 'analyzeFocusBtn') ui.displayAnomalyReport('violations');
            else if (actionButton.id === 'detailedAnalysisBtn') runDetailedAnalysis();
            else if (actionButton.id === 'saveSettingsBtn') ui.saveSettings();
            else if (actionButton.id === 'resetFiltersBtn') ui.resetFilters();
            else if (actionButton.id === 'exportBtn') ui.openExportModal();
            else if (actionButton.matches('.preset-btn')) {
                document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
                actionButton.classList.add('active');
                ui.applyPresetFilter(actionButton.dataset.preset);
            }
            return; // Действие обработано
        }

        // --- 4. ВЗАИМОДЕЙСТВИЯ С ТАБЛИЦАМИ (Сортировка, ссылки, пагинация) ---
        const tableHeader = target.closest('.data-table thead th[data-sort]');
        if (tableHeader) {
            const sortKey = tableHeader.dataset.sort;
            // Определяем, для какой таблицы вызвана сортировка
            if (target.closest('#results-container')) {
                ui.sortAndRerenderMainResults(sortKey);
            } else if (target.closest('#abandoned-sessions-container')) {
                ui.sortAndRerenderAbandoned(sortKey);
            }
            return; // Действие обработано
        }
        // Вставьте этот код в обработчик кликов в main.js

		const analysisBtn = target.closest('.single-analysis-btn');
		if (analysisBtn) {
			e.preventDefault();
			const sessionId = analysisBtn.dataset.sessionId;
			if (sessionId) {
				// Очищаем предыдущий выбор и выбираем только одного пользователя
				selectedForComparison.clear();
				selectedForComparison.add(sessionId);

				// Переключаемся на вид сравнения
				ui.switchView('comparison');

				// Сразу запускаем анализ
				// Небольшая задержка, чтобы view успел переключиться
				setTimeout(() => runDetailedAnalysis(), 50);
			}
			return; // Действие обработано
		}
        const pageButton = target.closest('.page-btn');
        if (pageButton && !pageButton.disabled) {
            apiClient.loadInitialData(parseInt(pageButton.dataset.page, 10));
            return;
        }

        const profileLink = target.closest('.user-profile-link');
        if (profileLink) {
            e.preventDefault();
            ui.openUserProfile(profileLink.dataset.lastname, profileLink.dataset.firstname);
            return;
        }

        const logLink = target.closest('.event-log-link');
        if (logLink) {
            e.preventDefault();
            apiClient.showEventLog(logLink.dataset.sessionId);
            return;
        }

        // --- 5. ДЕЙСТВИЯ НА КОНКРЕТНЫХ СТРАНИЦАХ (Views) ---
        const abandonedFilterBtn = target.closest('#abandoned-filters .filter-btn');
        if (abandonedFilterBtn) {
            document.querySelector('#abandoned-filters .filter-btn.active')?.classList.remove('active');
            abandonedFilterBtn.classList.add('active');
            ui.renderAbandonedSessions(abandonedFilterBtn.dataset.filter);
            return;
        }
        
        const comparisonCard = target.closest('.comparison-list-card');
        if (comparisonCard) {
            ui.toggleComparisonSelection(comparisonCard);
            return;
        }
    });

    // --- ОБРАБОТЧИКИ ДЛЯ ПОЛЕЙ ВВОДА ФИЛЬТРОВ ---
    const lastNameFilter = document.getElementById('lastNameFilter');
    if (lastNameFilter) lastNameFilter.addEventListener('input', ui.applyFiltersAndRender);

    const firstNameFilter = document.getElementById('firstNameFilter');
    if (firstNameFilter) firstNameFilter.addEventListener('input', ui.applyFiltersAndRender);

    const fingerprintFilter = document.getElementById('fingerprintFilter');
    if (fingerprintFilter) fingerprintFilter.addEventListener('change', ui.applyFiltersAndRender);
    
    console.log("✅ Все обработчики событий успешно установлены.");
}


// =============================================================================
// BUSINESS LOGIC
// =============================================================================

async function runDetailedAnalysis() {
    const selectedIds = Array.from(selectedForComparison);

    // ИЗМЕНЕНИЕ: Проверяем, что выбран хотя бы один пользователь
    if (selectedIds.length < 1) {
        ui.showNotification("Выберите хотя бы одного пользователя для анализа.", "warning");
        return;
    }

    ui.showLoading();

    try {
        // --- ГЛАВНОЕ ИЗМЕНЕНИЕ ---
        // 1. Создаем массив промисов, запрашивая полные данные для каждой сессии
        const fetchPromises = selectedIds.map(id => apiClient.fetchFullResultDetails(id));

        // 2. Дожидаемся загрузки ВСЕХ полных данных
        const fullResults = (await Promise.all(fetchPromises)).filter(Boolean); // .filter(Boolean) убирает null в случае ошибки

        if (fullResults.length !== selectedIds.length) {
            ui.showNotification("Не удалось загрузить полные данные для одной или нескольких сессий.", "danger");
        }

        let dtwResults = {}; // По умолчанию результат DTW пустой

        // ИЗМЕНЕНИЕ: Запускаем DTW-анализ, только если пользователей больше одного
        if (fullResults.length > 1) {
            const useServer = document.getElementById('serverAnalysisToggle')?.checked;
            if (useServer) {
                dtwResults = await apiClient.runServerDtwAnalysis(selectedIds);
            } else {
                dtwResults = await analysis.runClientDtwAnalysis(fullResults);
            }
        }

        if (dtwResults) {
            ui.renderComparisonResults(dtwResults, fullResults);
        } else {
            ui.showNotification("Не удалось выполнить DTW анализ.", "danger");
        }

    } catch (error) {
        console.error("Ошибка в процессе детального анализа:", error);
        ui.showNotification("Произошла ошибка во время анализа.", "danger");
    } finally {
        ui.hideLoading();
    }
}

