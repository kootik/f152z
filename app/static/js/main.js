/**
 * main.js
 * Application orchestrator with secure event handling.
 */

import { settings, updateSettings, selectedForComparison, currentPage, setResultsPerPage, setDashboardStats } from './state.js';
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
    
    // --- ИЗМЕНЕНИЕ: Загружаем и данные, и статистику ---
    apiClient.loadInitialData(1).then(() => {
        // Загружаем статистику ПОСЛЕ загрузки основных данных
        // чтобы renderDashboardCharts мог использовать allLoadedResults
        apiClient.fetchDashboardStats().then(stats => {
            setDashboardStats(stats); // <-- СОХРАНЯЕМ СТАТИСТИКУ
            ui.renderDashboardWidgets(stats);
            ui.renderDashboardCharts(); // Теперь это безопасно вызывать
        });
    });
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

    // --- ИСПРАВЛЕНИЕ #3: Добавляем обработчик для меню пользователя ---
    const userMenu = document.getElementById('userMenu');
    if (userMenu) {
        userMenu.addEventListener('click', (e) => {
            // Просто переключаем класс 'active'. 
            // CSS должен будет обработать показ/скрытие выпадающего меню.
            e.currentTarget.classList.toggle('active');
        });
    }
    // --- КОНЕЦ ИСПРАВЛЕНИЯ #3 ---
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
    document.body.addEventListener('click', (e) => {
        const target = e.target;

        // --- 1. ЛОГИКА ВЫПАДАЮЩЕГО СПИСКА (Dropdown) ---
        const dropdownToggle = target.closest('#loadOptionsToggle');
        const dropdownItem = target.closest('.dropdown-item'); // Находит ЛЮБОЙ элемент .dropdown-item
        const isClickInsideLoadOptions = target.closest('.load-options-dropdown');
        
        // Закрываем список "Показывать по:", если клик был вне его
        if (!isClickInsideLoadOptions) {
            const menu = document.getElementById('loadOptionsMenu');
            if (menu?.classList.contains('active')) {
                menu.classList.remove('active');
                document.getElementById('loadOptionsToggle')?.classList.remove('active');
            }
        }
        
        if (dropdownToggle) { // Клик по кнопке "Показывать по:"
            const menu = document.getElementById('loadOptionsMenu');
            dropdownToggle.classList.toggle('active');
            menu.classList.toggle('active');
            return; // Действие обработано
        }
        
        // --- 👇 ИСПРАВЛЕННАЯ ЛОГИКА ОБРАБОТКИ .dropdown-item 👇 ---
        if (dropdownItem) { 
            e.preventDefault(); // Отменяем переход по ссылке для ВСЕХ

            // A. Это элемент из списка "Показывать по:"?
            if (dropdownItem.closest('#loadOptionsMenu')) {
                const count = dropdownItem.dataset.count;
                document.getElementById('selectedValue').textContent = count === 'all' ? 'Все' : count;
                setResultsPerPage(count);
                apiClient.loadInitialData(1);
                
                // Закрываем это меню
                const menu = document.getElementById('loadOptionsMenu');
                const toggle = document.getElementById('loadOptionsToggle');
                menu?.classList.remove('active');
                toggle?.classList.remove('active');
            }
            
            // B. Это кнопка "Настройки" из меню пользователя?
            else if (dropdownItem.id === 'openSettingsBtn') {
                ui.openSettings(); // <-- ВЫЗЫВАЕМ ФУНКЦИЮ ИЗ ui.js
                document.getElementById('userMenu')?.classList.remove('active'); // Закрываем меню пользователя
            }
            
            // C. Это кнопка "Выход"?
            else if (dropdownItem.classList.contains('logout')) {
                window.location.href = '/logout'; // (Или ваш URL для выхода)
            }

            return; // Действие обработано
        }
        // --- 👆 КОНЕЦ ИСПРАВЛЕННОЙ ЛОГИКИ 👆 ---
        
        
        // --- 2. ГЛОБАЛЬНЫЕ ДЕЙСТВИЯ (Закрытие модальных окон и меню) ---
        if (target.classList.contains('modal')) {
            target.style.display = 'none';
            return;
        }
        if (target.closest('.close-btn')) {
            target.closest('.modal').style.display = 'none';
            return;
        }
        // Закрываем меню пользователя, если клик был вне его
        if (!target.closest('#userMenu')) {
            document.getElementById('userMenu')?.classList.remove('active');
        }
        // Закрываем поиск, если клик был вне его
        if (!target.closest('.search-container')) {
            ui.hideGlobalSearchResults();
        }

        // --- 3. КНОПКИ НА ПАНЕЛИ ФИЛЬТРОВ И АНАЛИЗА ---
        const actionButton = target.closest('.btn, .preset-btn');
        if (actionButton) {
            if (actionButton.id === 'analyzeMouseBtn') {
                const selectedCheckboxes = document.querySelectorAll('#results-table-body .row-checkbox:checked');
                const selectedIds = Array.from(selectedCheckboxes).map(cb => cb.dataset.sessionId);
                
                if (selectedIds.length === 0) {
                    ui.showNotification("Выберите хотя бы одну сессию из таблицы для анализа мыши.", "warning");
                    return;
                }
                
                selectedForComparison.clear();
                selectedIds.forEach(id => selectedForComparison.add(id));
                ui.switchView('comparison');
                setTimeout(() => runDetailedAnalysis(), 50);

            }
            else if (actionButton.id === 'analyzeFingerprintBtn') analysis.analyzeFingerprints();
            else if (actionButton.id === 'analyzeFocusBtn') ui.displayAnomalyReport('violations');
            else if (actionButton.id === 'detailedAnalysisBtn') runDetailedAnalysis();
            else if (actionButton.id === 'saveSettingsBtn') ui.saveSettings();
            else if (actionButton.id === 'resetFiltersBtn') ui.resetFilters();
            else if (actionButton.id === 'exportBtn') ui.openExportModal();
            else if (actionButton.matches('.preset-btn')) {
                document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
                actionButton.classList.add('active');
                apiClient.loadInitialData(1);
            }
            return; // Действие обработано
        }

        // --- 4. ВЗАИМОДЕЙСТВИЯ С ТАБЛИЦАМИ (Сортировка, ссылки, пагинация) ---
        const tableHeader = target.closest('.data-table thead th[data-sort]');
        if (tableHeader) {
            const sortKey = tableHeader.dataset.sort;
            if (target.closest('#results-container')) {
                ui.sortAndRerenderMainResults(sortKey);
            } else if (target.closest('#abandoned-sessions-container')) {
                ui.sortAndRerenderAbandoned(sortKey);
            }
            return;
        }
        
        const selectAll = target.closest('#selectAllRows');
        if (selectAll) {
            const isChecked = selectAll.checked;
            document.querySelectorAll('#results-table-body .row-checkbox').forEach(checkbox => {
                checkbox.checked = isChecked;
            });
            return;
        }

		const analysisBtn = target.closest('.single-analysis-btn');
		if (analysisBtn) {
			e.preventDefault();
			const sessionId = analysisBtn.dataset.sessionId;
			if (sessionId) {
				selectedForComparison.clear();
				selectedForComparison.add(sessionId);
				ui.switchView('comparison');
				setTimeout(() => runDetailedAnalysis(), 50);
			}
			return;
		}
        // --- 👇 ИЗМЕНЕННАЯ ЛОГИКА ПАГИНАЦИИ 👇 ---
        const pageButton = target.closest('.page-btn');
        if (pageButton && !pageButton.disabled) {
            
            // Проверяем, к какой пагинации относится кнопка
            if (pageButton.classList.contains('registry-page-btn')) {
                // Это пагинация Реестра
                apiClient.loadAndRenderCertificates(parseInt(pageButton.dataset.page, 10));
            } else {
                // Это пагинация Дашборда (старая логика)
                apiClient.loadInitialData(parseInt(pageButton.dataset.page, 10));
            }
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
    
    // Этот фильтр вызывает БЭКЕНД (API) фильтрацию
    const statusFilter = document.getElementById('statusFilter');
    if (statusFilter) {
        statusFilter.addEventListener('change', () => {
            // При смене статуса, загружаем первую страницу (page=1) с новым фильтром
            apiClient.loadInitialData(1);
        });
    } 
    // --- ИСПРАВЛЕНИЕ #2: Глобальный поиск (частичная реализация) ---
    const globalSearch = document.getElementById('globalSearch');
    let searchTimeout;
    if (globalSearch) {
        // Обработчик для ввода текста
        globalSearch.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            
            // Отменяем предыдущий таймаут
            clearTimeout(searchTimeout);
            if (query.length > 2) {
                // Ждем 300мс после окончания ввода
                searchTimeout = setTimeout(async () => {
                    const results = await apiClient.fetchGlobalSearch(query);
                    console.log("Результаты поиска:", results);
                    ui.renderGlobalSearchResults(results);
                }, 300);
            } else {
                ui.hideGlobalSearchResults(); 
            }
        });
    }
    // Обработчик горячей клавиши Ctrl+K
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && (e.key === 'k' || e.key === 'K' || e.keyCode === 75)) {
            e.preventDefault();
            globalSearch?.focus();
        }
    });
    // --- КОНЕЦ ИСПРАВЛЕНИЯ #2 ---
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
        // Если ничего не загрузилось, выходим
        if (fullResults.length === 0) {
            ui.showNotification("Не удалось загрузить данные для анализа.", "danger");
            ui.hideLoading(); // Не забываем скрыть загрузчик
            return; // Выходим из функции
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

        // 4. Рендерим в любом случае (даже для 1 пользователя)
            ui.renderComparisonResults(dtwResults, fullResults);

    } catch (error) {
        console.error("Ошибка в процессе детального анализа:", error);
        ui.showNotification("Произошла ошибка во время анализа.", "danger");
    } finally {
        ui.hideLoading();
    }
}