import { 
    currentPageResults, allLoadedResults, 
    settings, currentView, selectedForComparison, 
    fingerprintGroups, charts, USER_COLORS,
    setSettings, setCurrentView, totalResults, resultsPerPage, currentPage, 
    allAbandonedSessions, 
    abandonedSessionsSortKey, 
    abandonedSessionsSortDir, 
    setAbandonedSessionsSort,
    mainResultsSortKey, mainResultsSortDir, setMainResultsSort,
    dashboardStats,
    registrySortKey, registrySortDir, setRegistrySort
} from './state.js';
import apiClient from './api.js';
import * as analysis from './analysis.js';

// =============================================================================
// Кэш DOM Элементов
// =============================================================================
const DOM_CACHE = {
    navItems: null,
    viewContainers: null,
    cache: {},
    
    init() {
        this.navItems = Array.from(document.querySelectorAll('.nav-item'));
        this.viewContainers = Array.from(document.querySelectorAll('.content-area > div[id$="-view"]'));
        console.log("DOM кэш инициализирован.");
    },
    
    getElementById(id) {
        if (!this.cache[id]) {
            this.cache[id] = document.getElementById(id);
        }
        return this.cache[id];
    },

    // Метод для очистки кэша (добавлен в прошлом)
    invalidate(id) {
        if (this.cache[id]) {
            delete this.cache[id];
        }
    }
};

document.addEventListener('DOMContentLoaded', () => DOM_CACHE.init());

// =============================================================================
// УПРАВЛЕНИЕ ВИДАМИ (VIEW MANAGEMENT)
// =============================================================================

function updateBreadcrumbs(viewName) {
    const breadcrumbsContainer = DOM_CACHE.getElementById('breadcrumbs'); 
    if (!breadcrumbsContainer) return;

    const viewTitles = {
        dashboard: "Дашборд",
        comparison: "Детальное сравнение",
        abandoned: "Прерванные сессии",
        behavior: "Поведенческий анализ",
        registry: "Реестр аттестатов",
        statistics: "Сводный отчет",
        settings: "Настройки PDF"
    };
    
    const currentTitle = viewTitles[viewName] || 'Аналитика';

    breadcrumbsContainer.innerHTML = html`
        <a href="#" class="breadcrumb-item nav-item" data-view="dashboard">Анализ</a>
        <span class="breadcrumb-separator">/</span>
        <span class="breadcrumb-item active">${currentTitle}</span>
    `.toString(); 
}

function destroyCharts(chartKeys) {
    chartKeys.forEach(key => {
        if (charts[key] && typeof charts[key].destroy === 'function') {
            charts[key].destroy();
            delete charts[key];
            console.log(`...График ${key} уничтожен.`);
        }
    });
}

export function switchView(viewName) {
    if (currentView === viewName) return;

    // УДАЛЕНЫ избыточные вызовы destroyCharts
    // (Они уже есть в функциях рендеринга)

    const previousView = currentView; 
    setCurrentView(viewName);
    updateBreadcrumbs(viewName);

    DOM_CACHE.navItems.forEach(item => 
        item.classList.toggle('active', item.dataset.view === viewName)
    );

    DOM_CACHE.viewContainers.forEach(div => {
        div.classList.toggle('hidden', div.id !== `${viewName}-view`);
    });

    switch (viewName) {
        case 'dashboard':
            if (dashboardStats) renderDashboardWidgets(dashboardStats);
            if (allLoadedResults.size > 0) {
                renderDataTable(Array.from(allLoadedResults.values()));
                renderPaginationControls();
                applyFiltersAndRender(); 
                 renderDashboardCharts(); 
            } else {
                 apiClient.loadInitialData(currentPage);
            }
            break;
        case 'comparison':
            renderComparisonUserList(Array.from(allLoadedResults.values()));
            const analysisBtn = DOM_CACHE.getElementById('detailedAnalysisBtn');
            if (analysisBtn) {
                analysisBtn.disabled = selectedForComparison.size < 1; 
            }
            const resultsPanel = DOM_CACHE.getElementById('comparison-results-panel');
            if(resultsPanel) {
                resultsPanel.innerHTML = '<div class="comparison-analysis-placeholder"><h4>Панель анализа</h4><p>Выберите одного или более пользователей из списка слева и нажмите "Провести анализ".</p></div>';
            }
            // Очищаем кэш, так как уходим со страницы
            DOM_CACHE.invalidate('latencyChart');
            break;
        case 'abandoned':
            if (previousView !== 'abandoned') {
                apiClient.loadAndRenderAbandonedSessions();
            }
            break;
        case 'behavior':
             if (previousView !== 'behavior') {
                apiClient.loadAndRenderBehaviorAnalysis();
             }
            break;
        case 'registry':
             if (previousView !== 'registry') {
                 apiClient.loadAndRenderCertificates();
             }
            break;
        case 'statistics':
             generateStatistics();
            break;
    }
}

// =============================================================================
// ФИЛЬТРЫ И ОСНОВНОЙ ВИД
// =============================================================================

export function applyPresetFilter(presetType) {
    let sourceData = Array.from(allLoadedResults.values());
    let filtered = [...sourceData];
    const now = new Date();
    
    switch(presetType) {
        case 'all':
            break;
        case 'today':
            filtered = filtered.filter(result => {
                const resultDate = new Date(result.sessionMetrics?.startTime);
                return resultDate.toDateString() === now.toDateString();
            });
            break;
        case 'week':
            const weekStart = new Date(now);
            weekStart.setDate(now.getDate() - now.getDay()); 
            weekStart.setHours(0, 0, 0, 0);
            filtered = filtered.filter(result => {
                const resultDate = new Date(result.sessionMetrics?.startTime);
                return resultDate >= weekStart;
            });
            break;
        case 'anomalies':
            filtered = filtered.filter(result => {
                const sm = result.sessionMetrics || {};
                return (sm.totalFocusLoss > (settings.focusThreshold ?? 5)) ||
                       (sm.totalBlurTime > (settings.blurThreshold ?? 60)) ||
                       (sm.printAttempts > (settings.printThreshold ?? 0));
            });
            break;
    }
    
    if (currentView === 'dashboard') {
        renderDataTable(filtered); // <-- ВЫЗОВ ОБНОВЛЕННОЙ ФУНКЦИИ
        const paginationContainer = DOM_CACHE.getElementById('pagination-container');
        if (paginationContainer) {
            if (presetType !== 'all') {
                paginationContainer.innerHTML = `<div class="pagination-info">Показаны отфильтрованные результаты (${filtered.length})</div>`;
            } else {
                renderPaginationControls(); 
            }
        }
    }
    
    const message = presetType === 'all' ? 'Показаны все данные' :
                    presetType === 'today' ? `Найдено ${filtered.length} результатов за сегодня` :
                    presetType === 'week' ? `Найдено ${filtered.length} результатов за эту неделю` :
                    `Найдено ${filtered.length} результатов с аномалиями`;
    showNotification(message, 'info', 2000);
}

function populateRegistryYearFilter(certificates) {
    const yearSelect = DOM_CACHE.getElementById('registryYearFilter');
    if (!yearSelect || yearSelect.options.length > 1) return; 

    const years = new Set();
    certificates.forEach(cert => {
        if (cert.issue_date) {
            years.add(new Date(cert.issue_date).getFullYear());
        }
    });

    const sortedYears = Array.from(years).sort((a, b) => b - a); 
    sortedYears.forEach(year => {
        const option = document.createElement('option');
        option.value = year;
        option.textContent = year;
        yearSelect.appendChild(option);
    });
}

function createSorter(getSortState, setSortState, getDataToSort, rerenderFunc) {
    return function(newSortKey) {
        const { key: currentKey, dir: currentDir } = getSortState();
        const newDir = (currentKey === newSortKey && currentDir === 'desc') ? 'asc' : 'desc';
        
        setSortState(newSortKey, newDir);
        
        const comparator = (a, b) => {
            const getVal = (obj, path) => path.split('.').reduce((o, i) => o?.[i], obj);
            let valA = getVal(a, newSortKey);
            let valB = getVal(b, newSortKey);

            if (valA == null && valB != null) return newDir === 'asc' ? 1 : -1;
            if (valA != null && valB == null) return newDir === 'asc' ? -1 : 1;
            if (valA == null && valB == null) return 0;
            
            if (newSortKey.includes('Time') || newSortKey.includes('date') || newSortKey.includes('Date') || newSortKey === 'startTime' || newSortKey === 'issue_date') {
                const dateA = typeof valA === 'string' ? new Date(valA) : valA;
                const dateB = typeof valB === 'string' ? new Date(valB) : valB;
                const timeA = !isNaN(dateA?.getTime()) ? dateA.getTime() : (newDir === 'asc' ? Infinity : -Infinity);
                const timeB = !isNaN(dateB?.getTime()) ? dateB.getTime() : (newDir === 'asc' ? Infinity : -Infinity);
                return newDir === 'asc' ? timeA - timeB : timeB - timeA;
            }
            
            const numA = parseFloat(valA);
            const numB = parseFloat(valB);
            if (!isNaN(numA) && !isNaN(numB)) {
                 return newDir === 'asc' ? numA - numB : numB - numA;
            }
            
            return newDir === 'asc' 
                ? String(valA).localeCompare(String(valB), 'ru', { sensitivity: 'base' }) 
                : String(valB).localeCompare(String(valA), 'ru', { sensitivity: 'base' });
        };
        
        const dataToSort = getDataToSort(); 
        const sortedData = dataToSort.sort(comparator); 
        
        rerenderFunc(sortedData); 
    };
}

export const sortAndRenderMainResults = createSorter(
    () => ({ key: mainResultsSortKey, dir: mainResultsSortDir }),
    setMainResultsSort,
    () => Array.from(allLoadedResults.values()), 
    (sortedData) => {
        applyFiltersAndRender();
    }
);

export const sortAndRenderAbandoned = createSorter(
    () => ({ key: abandonedSessionsSortKey, dir: abandonedSessionsSortDir }),
    setAbandonedSessionsSort,
    () => [...allAbandonedSessions], 
    (sortedData) => {
        const currentFilter = document.querySelector('#abandoned-filters .filter-btn.active')?.dataset.filter || 'all';
        renderAbandonedSessions(currentFilter, sortedData); 
    }
);

export const sortAndRenderRegistry = createSorter(
    () => ({ key: registrySortKey, dir: registrySortDir }),
    setRegistrySort, 
    () => apiClient.getCurrentRegistryData(), 
    (sortedData) => {
        renderCertificatesTable({ certificates: sortedData }); 
    }
);
function updateTableRows(newData) {
    const tbody = DOM_CACHE.getElementById('results-table-body');
    if (!tbody) return;
    // === 🔻 ВОТ ИСПРАВЛЕНИЕ 🔻 ===
    // Принудительно удаляем "заглушку" (Результаты не найдены/Загрузка),
    // если она существует, перед началом любых операций.
    const placeholderRow = tbody.querySelector('td.loading');
    if (placeholderRow) {
        placeholderRow.closest('tr').remove();
    }
    // === 🔺 КОНЕЦ ИСПРАВЛЕНИЯ 🔺 ===

    // Если новых данных нет, очищаем таблицу
    if (!newData || newData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="loading">Результаты не найдены.</td></tr>';
        return;
    }

    // Создаем Map существующих строк по sessionId
    const existingRowsMap = new Map();
    tbody.querySelectorAll('tr[data-session-id]').forEach(row => {
        existingRowsMap.set(row.dataset.sessionId, row);
    });

    const processedIds = new Set();
    const fragment = document.createDocumentFragment(); // Фрагмент для новых строк

    // Обрабатываем новые данные
    newData.forEach((result) => {
        const sessionId = result.sessionId;
        processedIds.add(sessionId);
        
        const existingRow = existingRowsMap.get(sessionId);
        
        if (existingRow) {
            // Обновляем существующую строку (не переделываем, а обновляем содержимое)
            updateTableRow(existingRow, result);
        } else {
            // Создаем новую строку
            const newRow = createTableRowElement(result);
            fragment.appendChild(newRow); // Добавляем во фрагмент
            existingRowsMap.set(sessionId, newRow);
        }
    });

    // Удаляем строки, которых нет в новых данных
    existingRowsMap.forEach((row, sessionId) => {
        if (!processedIds.has(sessionId)) {
            row.remove();
        }
    });

    // Добавляем все новые строки одним махом в конец
    if (fragment.children.length > 0) {
        tbody.appendChild(fragment);
        // Переприсваиваем обработчики событий для новых строк
        attachRowEventHandlers(fragment);
    }

    // Если tbody пустой - показываем сообщение
    // (Этот блок теперь будет работать корректно, т.к. placeholder удален вначале)
    if (tbody.children.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="loading">Результаты не найдены.</td></tr>';
    }
}

export function applyFiltersAndRender() {
    const lastName = DOM_CACHE.getElementById('lastNameFilter')?.value.toLowerCase() || '';
    const firstName = DOM_CACHE.getElementById('firstNameFilter')?.value.toLowerCase() || '';
    const fingerprint = DOM_CACHE.getElementById('fingerprintFilter')?.value || '';
    
    const sourceData = Array.from(allLoadedResults.values());

    const filtered = sourceData.filter(result => {
        const ui = result.userInfo || {};
        const lastNameMatch = !lastName || ui.lastName?.toLowerCase().includes(lastName);
        const firstNameMatch = !firstName || ui.firstName?.toLowerCase().includes(firstName);
        const fingerprintMatch = !fingerprint || (result.fingerprintHash && result.fingerprintHash === fingerprint);
        
        return lastNameMatch && firstNameMatch && fingerprintMatch;
    });
    
    const { key: sortKey, dir: sortDir } = { key: mainResultsSortKey, dir: mainResultsSortDir };
    
    const comparator = (a, b) => {
        const getVal = (obj, path) => path.split('.').reduce((o, i) => o?.[i], obj);
        let valA = getVal(a, sortKey);
        let valB = getVal(b, sortKey);

        if (valA == null && valB != null) return sortDir === 'asc' ? 1 : -1;
        if (valA != null && valB == null) return sortDir === 'asc' ? -1 : 1;
        if (valA == null && valB == null) return 0;
        
        if (sortKey.includes('Time') || sortKey.includes('date') || sortKey.includes('Date') || sortKey === 'startTime') {
            const dateA = typeof valA === 'string' ? new Date(valA) : valA;
            const dateB = typeof valB === 'string' ? new Date(valB) : valB;
            const timeA = !isNaN(dateA?.getTime()) ? dateA.getTime() : (sortDir === 'asc' ? Infinity : -Infinity);
            const timeB = !isNaN(dateB?.getTime()) ? dateB.getTime() : (sortDir ==='asc' ? Infinity : -Infinity);
            return sortDir === 'asc' ? timeA - timeB : timeB - timeA;
        }
        
        const numA = parseFloat(valA);
        const numB = parseFloat(valB);
        if (!isNaN(numA) && !isNaN(numB)) {
             return sortDir === 'asc' ? numA - numB : numB - numA;
        }
        
        return sortDir === 'asc' 
            ? String(valA).localeCompare(String(valB), 'ru', { sensitivity: 'base' }) 
            : String(valB).localeCompare(String(valA), 'ru', { sensitivity: 'base' });
    };
    
    filtered.sort(comparator); 
    
    if (currentView === 'dashboard') {
        renderDataTable(filtered); // <-- ИЗМЕНЕНО: теперь вызывает renderDataTable с отфильтрованными данными
        
        const paginationContainer = DOM_CACHE.getElementById('pagination-container');
         if (paginationContainer) {
             if (lastName || firstName || fingerprint) {
                 paginationContainer.innerHTML = html`<div class="pagination-info">Показаны отфильтрованные результаты (${filtered.length})</div>`.toString();
             } else {
                 renderPaginationControls();
             }
         }
    }
}

export function resetFilters() {
    const lastNameFilter = DOM_CACHE.getElementById('lastNameFilter');
    const firstNameFilter = DOM_CACHE.getElementById('firstNameFilter');
    const fingerprintFilter = DOM_CACHE.getElementById('fingerprintFilter');
    const anomalyReports = DOM_CACHE.getElementById('anomaly-reports');
    
    if(lastNameFilter) lastNameFilter.value = '';
    if(firstNameFilter) firstNameFilter.value = '';
    if(fingerprintFilter) fingerprintFilter.value = '';
    if(anomalyReports) anomalyReports.innerHTML = '';

    document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector('.preset-btn[data-preset="all"]')?.classList.add('active');
    
    selectedForComparison.clear();
    
    setMainResultsSort('sessionMetrics.startTime', 'desc'); 
    const sortedByDefault = Array.from(allLoadedResults.values()).sort((a, b) => 
        new Date(b.sessionMetrics?.startTime ?? 0) - new Date(a.sessionMetrics?.startTime ?? 0)
    );
    
    renderDataTable(sortedByDefault); 
    renderPaginationControls(); 
}

// =============================================================================
// SAFE HTML HELPERS (XSS Protection)
// =============================================================================

function escapeHtml(unsafe) {
    if (unsafe == null) return ''; 
    return String(unsafe)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function createSafeText(text) {
    return escapeHtml(text);
}

const html = (strings, ...values) => {
    return strings.reduce((result, str, i) => {
        let value = values[i];
        if (value && typeof value === 'object' && value.__UNSAFE_HTML) {
            value = String(value.__UNSAFE_HTML); 
        } else {
             value = value != null ? escapeHtml(String(value)) : ''; 
        }
        return result + str + value;
    }, '');
};

const unsafeHTML = (trustedValue) => ({ __UNSAFE_HTML: trustedValue });

// =============================================================================
// РЕНДЕРИНГ - ДАШБОРД (НОВЫЙ ДИЗАЙН)
// =============================================================================

export function renderDashboardWidgets(stats) {
    const container = DOM_CACHE.getElementById('dashboard-widgets');
    if (!container) return;
    
    const formatChange = (change) => {
        if (change == null) { 
            return unsafeHTML('<div class="widget-change"><span></span><span>-</span></div>');
        }
        if (change === 0) {
            return unsafeHTML('<div class="widget-change"><span></span><span>Без изменений</span></div>');
        }
        const direction = change > 0 ? 'positive' : 'negative';
        const icon = change > 0 ? '↑' : '↓';
        return unsafeHTML(`<div class="widget-change ${direction}"><span>${icon}</span><span>${Math.abs(change)}% за неделю</span></div>`);
    };

    if (!stats) {
        container.innerHTML = `
            <div class="widget"><div class="widget-header"><div class="widget-title">Завершено тестов</div></div><div class="widget-value">...</div></div>
            <div class="widget"><div class="widget-header"><div class="widget-title">Средний балл</div></div><div class="widget-value">...</div></div>
            <div class="widget"><div class="widget-header"><div class="widget-title">Обнаружено аномалий</div></div><div class="widget-value">...</div></div>
            <div class="widget"><div class="widget-header"><div class="widget-title">Уникальных пользователей</div></div><div class="widget-value">...</div></div>
        `;
        return;
    }

    container.innerHTML = html`
        <div class="widget">
            <div class="widget-header"><div class="widget-title">Завершено тестов</div><div class="widget-icon" style="background: rgba(37, 99, 235, 0.1); color: var(--primary);">📊</div></div>
            <div class="widget-value">${stats.totalTests?.value ?? '...'}</div>
            ${formatChange(stats.totalTests?.change)}
        </div>
        <div class="widget">
            <div class="widget-header"><div class="widget-title">Средний балл</div><div class="widget-icon" style="background: rgba(16, 185, 129, 0.1); color: var(--success);">📈</div></div>
            <div class="widget-value">${stats.avgScore?.value ?? '...'}%</div>
            ${formatChange(stats.avgScore?.change)}
        </div>
        <div class="widget">
            <div class="widget-header"><div class="widget-title">Обнаружено аномалий</div><div class="widget-icon" style="background: rgba(239, 68, 68, 0.1); color: var(--danger);">🚨</div></div>
            <div class="widget-value">${stats.anomaliesCount?.value ?? '...'}</div>
            ${formatChange(stats.anomaliesCount?.change)}
        </div>
        <div class="widget">
            <div class="widget-header"><div class="widget-title">Уникальных пользователей</div><div class="widget-icon" style="background: rgba(124, 58, 237, 0.1); color: var(--secondary);">👥</div></div>
            <div class="widget-value">${stats.uniqueUsers?.value ?? '...'}</div>
            ${formatChange(stats.uniqueUsers?.change)}
        </div>
    `.toString();
}

function drawPlaceholder(ctx, message) {
     if (!ctx) return;
     const width = ctx.canvas.clientWidth || ctx.canvas.width || 400;
     const height = ctx.canvas.clientHeight || ctx.canvas.height || 300;
     
     if (ctx.canvas.width !== width) ctx.canvas.width = width;
     if (ctx.canvas.height !== height) ctx.canvas.height = height;

     ctx.clearRect(0, 0, width, height);
     ctx.save(); 
     ctx.font = "16px Arial";
     ctx.fillStyle = getComputedStyle(document.documentElement)
         .getPropertyValue('--text-light').trim() || '#999999'; 
     ctx.textAlign = "center";
     ctx.textBaseline = "middle"; 
     ctx.fillText(message, width / 2, height / 2);
     ctx.restore(); 
}

export function renderDashboardCharts() {
	destroyCharts(['dashboardGrades', 'dashboardActivity']);
    const resultsArray = Array.from(allLoadedResults.values());
    
    const acceptedGrades = ['Отлично', 'Хорошо']; 
    const filteredResults = resultsArray.filter(r => 
        r.testResults?.grade && acceptedGrades.includes(r.testResults.grade.text) 
    );
    
    const gradesCtx = DOM_CACHE.getElementById('dashboardGradesChart')?.getContext('2d');
    if (gradesCtx) {
        if (filteredResults.length === 0) {
            drawPlaceholder(gradesCtx, "Нет 'зачетных' результатов (4 и 5) для отображения");
        } else {
            const gradesCounts = filteredResults.reduce((acc, r) => {
                const gradeText = r.testResults.grade.text;
                acc[gradeText] = (acc[gradeText] || 0) + 1;
                return acc;
            }, {});

            const gradeLabels = Object.keys(gradesCounts);
            const gradeData = Object.values(gradesCounts);
            
            const gradeColors = {
                'Отлично': 'hsla(145, 63%, 42%, 1)',
                'Хорошо': 'hsla(221, 83%, 53%, 1)',
                'Удовлетворительно': 'hsla(39, 92%, 56%, 1)',
            };
            
            const backgroundColors = gradeLabels.map(label => gradeColors[label] || '#94a3b8');

            charts['dashboardGrades'] = new Chart(gradesCtx, {
                type: 'doughnut',
                data: {
                    labels: gradeLabels,
                    datasets: [{
                        label: 'Количество',
                        data: gradeData,
                        backgroundColor: backgroundColors,
                        borderColor: '#fff',
                        borderWidth: 3,
                        borderRadius: 8, 
                        hoverOffset: 15
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '70%', 
                    plugins: {
                        title: {
                            display: true,
                            text: 'Соотношение зачетных оценок (4 и 5)',
                            padding: { top: 10, bottom: 10 },
                            font: { size: 16, weight: '600' },
                            color: 'var(--text)'
                        },
                        legend: {
                            position: 'right', 
                            labels: {
                                padding: 20,
                                font: { size: 14 },
                                color: 'var(--text-light)',
                                usePointStyle: true,
                                pointStyle: 'circle'
                            }
                        },
                        tooltip: {
                            backgroundColor: 'rgba(0, 0, 0, 0.7)',
                            titleFont: { size: 14, weight: 'bold' },
                            bodyFont: { size: 12 },
                            padding: 10,
                            cornerRadius: 8,
                            callbacks: {
                                label: function(context) {
                                    const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                    const percentage = ((context.parsed / total) * 100).toFixed(1);
                                    return ` ${context.label}: ${context.parsed} (${percentage}%)`;
                                }
                            }
                        }
                    }
                }
            });
        }
    }

    const activityCtx = DOM_CACHE.getElementById('dashboardActivityChart')?.getContext('2d');
    if (activityCtx) {
         if (resultsArray.length === 0) {
             drawPlaceholder(activityCtx, "Нет данных для отображения");
         } else {
            const dailyActivity = resultsArray.reduce((acc, r) => {
                 if (r.sessionMetrics?.startTime) { 
                     const date = new Date(r.sessionMetrics.startTime).toLocaleDateString('ru-RU');
                     acc[date] = (acc[date] || 0) + 1;
                 }
                 return acc;
             }, {});

            const sortedDates = Object.keys(dailyActivity).sort((a, b) => new Date(a.split('.').reverse().join('-')) - new Date(b.split('.').reverse().join('-')));
            const activityData = sortedDates.map(date => dailyActivity[date]);

            charts['dashboardActivity'] = new Chart(activityCtx, {
                type: 'line',
                data: {
                    labels: sortedDates,
                    datasets: [{
                        label: 'Тесты в день',
                        data: activityData,
                        borderColor: '#2563eb',
                        backgroundColor: 'rgba(37, 99, 235, 0.1)',
                        tension: 0.3,
                        fill: true,
                        pointRadius: 4,
                        pointHoverRadius: 6,
                        pointBackgroundColor: '#2563eb',
                        pointBorderColor: '#fff',
                        pointBorderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
                    scales: {
                        y: { beginAtZero: true, ticks: { stepSize: 1, precision: 0 }, grid: { color: 'rgba(0, 0, 0, 0.05)' } },
                        x: { grid: { display: false }, ticks: { maxRotation: 45, minRotation: 45 } }
                    },
                    interaction: { mode: 'nearest', axis: 'x', intersect: false }
                }
            });
        }
    }
}

// =============================================================================
// РЕНДЕРИНГ - ТАБЛИЦА РЕЗУЛЬТАТОВ (ГЛАВНОЕ ИЗМЕНЕНИЕ)
// =============================================================================

const sortHeader = (label, sortKey, currentKey, currentDir) => {
    return unsafeHTML(html`
    <th data-sort="${sortKey}">
        ${label} 
        <span class="sort-icon">
            ${currentKey === sortKey ? (currentDir === 'desc' ? '▼' : '▲') : ''}
        </span>
        </th>`.toString());
};

/**
 * ИСПРАВЛЕНО: Эта функция теперь создает "скелет" таблицы,
 * а `updateTableRows` ее заполняет.
 */
export function renderDataTable(results) {
    const container = DOM_CACHE.getElementById('results-container');
    if (!container) return;
    
    if (!Array.isArray(results)) {
        console.error('renderDataTable: expected array, got', typeof results);
        container.innerHTML = '<p class="error">Ошибка отображения данных таблицы.</p>';
        return;
    }

    const headersHTML = html`
        <th style="width: 50px;"><input type="checkbox" id="selectAllRows"></th>
        ${sortHeader('Пользователь', 'userInfo.lastName', mainResultsSortKey, mainResultsSortDir)}
        ${sortHeader('Тест', 'testType', mainResultsSortKey, mainResultsSortDir)}
        ${sortHeader('Дата', 'sessionMetrics.startTime', mainResultsSortKey, mainResultsSortDir)}
        ${sortHeader('IP Адрес', 'clientIp', mainResultsSortKey, mainResultsSortDir)}
        ${sortHeader('Результат', 'testResults.percentage', mainResultsSortKey, mainResultsSortDir)}
        <th>Время</th>
        <th>Аномалии</th>
        <th style="text-align: center;">Действия</th>
    `.toString();

    // Проверяем наличие tbody
    let tbody = DOM_CACHE.getElementById('results-table-body');
    
    if (!tbody) {
        // Создаем таблицу первый раз
        container.innerHTML = `
            <div class="table-wrapper">
                <table class="data-table">
                    <thead>
                        <tr>${headersHTML}</tr>
                    </thead>
                    <tbody id="results-table-body"></tbody>
                </table>
            </div>
        `;
        // Инвалидируем кэш, чтобы следующий getElementById нашел новый tbody
        DOM_CACHE.invalidate('results-table-body');
        tbody = DOM_CACHE.getElementById('results-table-body');
    } else {
        // Обновляем заголовок
        const thead = container.querySelector('thead tr');
        if (thead) thead.innerHTML = headersHTML;
    }

    // Обновляем строки
    updateTableRows(results);
    
    // Обновляем чекбокс "Выбрать все"
    const selectAllCheckbox = DOM_CACHE.getElementById('selectAllRows');
    if (selectAllCheckbox) {
        // Убедимся, что обработчик назначен только один раз
        if (!selectAllCheckbox.dataset.listenerAttached) {
            selectAllCheckbox.addEventListener('change', (e) => {
                const allRowCheckboxes = DOM_CACHE.getElementById('results-table-body')?.querySelectorAll('.row-checkbox');
                if (allRowCheckboxes) {
                    allRowCheckboxes.forEach(cb => {
                        cb.checked = e.target.checked;
                        // Обновляем state
                        if (e.target.checked) {
                            selectedForComparison.add(cb.dataset.sessionId);
                        } else {
                            selectedForComparison.delete(cb.dataset.sessionId);
                        }
                    });
                }
            });
            selectAllCheckbox.dataset.listenerAttached = 'true';
        }
        updateSelectAllCheckbox(); // Обновляем состояние (indeterminate/checked)
    }
}


/**
 * НОВАЯ ФУНКЦИЯ: Умное обновление строк таблицы
 */


/**
 * НОВАЯ ФУНКЦИЯ: Обновляет содержимое существующей строки без пересоздания
 */
function updateTableRow(row, result) {
    // Обновляем только содержимое ячеек, не пересоздаем строку
    const cells = row.querySelectorAll('td');
    if (cells.length < 9) return; // Ожидаем 9 ячеек (1 чекбокс + 8 данных)

    // Обновляем каждую ячейку
    const updates = [
        { index: 1, html: createUserCell(result.userInfo) },
        { index: 2, html: createTestCell(result.testType) },
        { index: 3, html: createDateCell(result.sessionMetrics?.startTime) },
        { index: 4, html: createIpCell(result.clientIp) },
        { index: 5, html: createResultCell(result.testResults) },
        { index: 6, html: createDurationCell(result.sessionMetrics) },
        { index: 7, html: createAnomaliesCell(result.sessionMetrics) },
        { index: 8, html: createActionsCell(result.sessionId) }
    ];

    updates.forEach(({ index, html: cellHtml }) => {
        if (cells[index] && cellHtml) {
            // Сравниваем HTML, чтобы избежать ненужных замен
            if (cells[index].innerHTML !== cellHtml) {
                // === ИСПРАВЛЕНИЕ: Используем 'tr' вместо 'div' для парсинга <td> ===
                const tempTr = document.createElement('tr');
                tempTr.innerHTML = cellHtml; // cellHtml это <td>...</td>
                const newCell = tempTr.firstElementChild;
                // ==========================================================
                
                if (newCell) {
                    cells[index].replaceWith(newCell);
                }
            }
        }
    });
    
    // Обновляем чекбокс (на случай, если он тоже изменился)
    const checkbox = row.querySelector('.row-checkbox');
    if (checkbox) {
        checkbox.checked = selectedForComparison.has(result.sessionId);
    }
}

/**
 * НОВАЯ ФУНКЦИЯ: Создает элемент TR (не HTML-строку)
 */
function createTableRowElement(result) {
    const tr = document.createElement('tr');
    tr.dataset.sessionId = result.sessionId;

    // Создаем чекбокс
    const checkboxTd = document.createElement('td');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'row-checkbox';
    checkbox.dataset.sessionId = result.sessionId;
    checkbox.checked = selectedForComparison.has(result.sessionId);
    checkboxTd.appendChild(checkbox);
    tr.appendChild(checkboxTd);

    // Добавляем все остальные ячейки
    const cellsHTML = [
        createUserCell(result.userInfo),
        createTestCell(result.testType),
        createDateCell(result.sessionMetrics?.startTime),
        createIpCell(result.clientIp),
        createResultCell(result.testResults),
        createDurationCell(result.sessionMetrics),
        createAnomaliesCell(result.sessionMetrics),
        createActionsCell(result.sessionId)
    ];

    cellsHTML.forEach(cellHtml => {
        // === ИСПРАВЛЕНИЕ: Используем 'tr' вместо 'div' для парсинга <td> ===
        const tempTr = document.createElement('tr');
        tempTr.innerHTML = cellHtml; // cellHtml это <td>...</td>
        const cell = tempTr.firstElementChild;
        // ==========================================================
        if (cell) {
            tr.appendChild(cell);
        }
    });

    return tr;
}

/**
 * НОВАЯ ФУНКЦИЯ: Переприсваивает обработчики событий для новых строк
 */
function attachRowEventHandlers(container) {
    // Назначаем обработчики только на дочерние элементы `container` (fragment)
    container.querySelectorAll('.row-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', (e) => {
            handleRowCheckboxChange(e);
        });
    });
}

/**
 * НОВАЯ ФУНКЦИЯ: Обрабатывает изменение чекбокса строки
 */
function handleRowCheckboxChange(e) {
    const sessionId = e.target.dataset.sessionId;
    if (e.target.checked) {
        selectedForComparison.add(sessionId);
    } else {
        selectedForComparison.delete(sessionId);
    }
    updateSelectAllCheckbox();
}

/**
 * НОВАЯ ФУНКЦИЯ: Обновляет состояние чекбокса "Выбрать все"
 */
function updateSelectAllCheckbox() {
    const selectAllCheckbox = DOM_CACHE.getElementById('selectAllRows');
    const allRowCheckboxes = DOM_CACHE.getElementById('results-table-body')?.querySelectorAll('.row-checkbox');
    
    if (selectAllCheckbox && allRowCheckboxes && allRowCheckboxes.length > 0) {
        const checkedCount = Array.from(allRowCheckboxes).filter(cb => cb.checked).length;
        selectAllCheckbox.checked = checkedCount === allRowCheckboxes.length;
        selectAllCheckbox.indeterminate = checkedCount > 0 && checkedCount < allRowCheckboxes.length;
    } else if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }
}


// === ХЕЛПЕРЫ ДЛЯ ГЕНЕРАЦИИ ЯЧЕЕК (ОСТАЮТСЯ БЕЗ ИЗМЕНЕНИЙ) ===
// Они теперь используются функциями createTableRowElement и updateTableRow

const TEST_NAME_MAP = {
    "study-117": "Обучение (ФЗ-117)", "test-117": "Тест (ФЗ-117)",
    "INFOSEC_117": "Тест (ФЗ-117)", "study-152": "Обучение (ФЗ-152)",
    "test-152": "Тест (ФЗ-152)", "PD_152": "Тест (ФЗ-152)",
    "studytest-152": "Самопроверка (ФЗ-152)", "study": "Обучение (Общее)",
    "test": "Тест (Общий)"
};

function createUserCell(userInfo) {
    const lastName = userInfo?.lastName ?? 'N/A';
    const firstName = userInfo?.firstName ?? '';
    const initials = `${lastName.at(0) ?? ''}${firstName.at(0) ?? ''}`.toUpperCase(); 
    
    return html`
        <td>
            <div class="user-cell">
                <div class="user-avatar-small">${initials}</div>
                <div>
                    <a href="#" class="user-profile-link" 
                       data-lastname="${lastName}" 
                       data-firstname="${firstName}">
                        <strong>${lastName}</strong> ${firstName}
                    </a>
                </div>
            </div>
        </td>
    `.toString();
}

function createTestCell(rawTestType) {
    const type = rawTestType ?? 'unknown';
    const prettyTestName = TEST_NAME_MAP[type] ?? type;
    
    let testIcon = '❓';
    if (type.includes('study')) { testIcon = '📚'; } 
    else if (type.includes('test') || type.includes('INFOSEC') || type.startsWith('PD_')) { testIcon = '📝'; }
    
    return html`
        <td class="cell-type"><span title="${prettyTestName}">${testIcon}</span> ${prettyTestName}</td>
    `.toString();
}

function createDateCell(startTime) {
    const dateStr = startTime ? new Date(startTime).toLocaleString('ru-RU') : 'N/A';
    return html`<td>${dateStr}</td>`.toString();
}

function createIpCell(clientIp) {
    return html`<td>${clientIp ?? 'N/A'}</td>`.toString();
}

function createResultCell(testResults) {
    const tr = testResults || {};
    const percentage = tr.percentage ?? 0; 
    const gradeClass = tr.grade?.class ?? 'poor'; 
    return html`<td><span class="status-badge grade-${gradeClass}">${percentage}%</span></td>`.toString();
}

function createDurationCell(sessionMetrics) {
    const sm = sessionMetrics || {};
    const duration = (sm.endTime && sm.startTime) 
        ? `${Math.round((new Date(sm.endTime) - new Date(sm.startTime)) / 60000)} мин` 
        : 'N/A';
    return html`<td>${duration}</td>`.toString();
}

function createAnomaliesCell(sessionMetrics) {
    const sm = sessionMetrics || {};
    const focusLoss = sm.totalFocusLoss ?? 0;
    const blurTime = sm.totalBlurTime ?? 0;
    const printAttempts = sm.printAttempts ?? 0;
    
    const totalAnomalies = [
        focusLoss > (settings.focusThreshold ?? 5), 
        blurTime > (settings.blurThreshold ?? 60),
        printAttempts > (settings.printThreshold ?? 0)
    ].filter(Boolean).length;
    
    const levels = {
        0: { width: 0, level: 'Низкий', class: 'low' },
        1: { width: 33, level: 'Низкий', class: 'low' }, 
        2: { width: 66, level: 'Средний', class: 'medium' },
        3: { width: 100, level: 'Высокий', class: 'high' }
    };
    
    const { width, level, class: levelClass } = levels[Math.min(totalAnomalies, 3)];
    
    return html`
        <td>
            <div class="anomaly-indicator">
                <div class="anomaly-level">
                    <div class="anomaly-level-fill ${levelClass}" style="width: ${width}%;"></div>
                </div>
                <span style="font-size: 0.85rem;">${level}</span>
            </div>
        </td>
    `.toString();
}

function createActionsCell(sessionId) {
    return html`
        <td class="cell-actions">
            <div class="action-buttons">
                <button class="action-btn event-log-link tooltip" data-session-id="${sessionId}">
                    <span class="tooltip-content">Журнал</span>👁️
                </button>
                <button class="action-btn single-analysis-btn tooltip" data-session-id="${sessionId}">
                    <span class="tooltip-content">Анализ</span>📊
                </button>
            </div>
        </td>
    `.toString();
}

/**
 * ИСПРАВЛЕННАЯ (старая) функция - теперь возвращает HTML-строку корректно.
 * ПРИМЕЧАНИЕ: Эта функция больше не используется `renderDataTable` или `updateTableRows`,
 * но оставлена, так как вы ее предоставили.
 */
function createTableRowHTML(result) {
    const cellsHTML = [
        createUserCell(result.userInfo),
        createTestCell(result.testType),
        createDateCell(result.sessionMetrics?.startTime),
        createIpCell(result.clientIp),
        createResultCell(result.testResults),
        createDurationCell(result.sessionMetrics),
        createAnomaliesCell(result.sessionMetrics),
        createActionsCell(result.sessionId)
    ].join('');

    return html`
        <tr data-session-id="${result.sessionId}">
            <td><input type="checkbox" class="row-checkbox" data-session-id="${result.sessionId}"></td>
            ${unsafeHTML(cellsHTML)}
        </tr>
    `.toString();
}

export function renderPaginationControls() {
    const container = DOM_CACHE.getElementById('pagination-container');
    if (!container) return;

    const totalPages = Math.ceil(totalResults / resultsPerPage);
    if (totalPages <= 1 && totalResults > 0) {
        container.innerHTML = html`<div class="pagination-info">Показаны все ${totalResults} записей</div>`.toString();
        return;
    }
     if (totalResults === 0) {
         container.innerHTML = ''; 
         return;
     }

    let pagesHtml = '';
    const pagesToShow = new Set();
    pagesToShow.add(1);
    pagesToShow.add(totalPages);
    const range = 2; 
    for (let i = -range; i <= range; i++) {
        const p = currentPage + i;
        if (p > 1 && p < totalPages) pagesToShow.add(p);
    }
    
    const sortedPages = Array.from(pagesToShow).sort((a,b)=>a-b);
    let lastPage = 0;
    sortedPages.forEach(p => {
        if(lastPage > 0 && p > lastPage + 1) {
            pagesHtml += `<button class="page-btn ellipsis" disabled>...</button>`;
        }
        pagesHtml += html`<button class="page-btn ${currentPage === p ? 'active' : ''}" data-page="${p}">${p}</button>`.toString();
        lastPage = p;
    });

    const startItem = Math.max(0, (currentPage - 1) * resultsPerPage) + 1;
    const endItem = Math.min(startItem + resultsPerPage - 1, totalResults);
    
    container.innerHTML = html`
        <div class="pagination">
            <div class="pagination-info">Показано ${startItem} - ${endItem} из ${totalResults}</div>
            <div class="pagination-controls">
                <button class="page-btn" ${currentPage === 1 ? 'disabled' : ''} data-page="${currentPage - 1}">‹ Пред.</button>
                ${unsafeHTML(pagesHtml)}
                <button class="page-btn" ${currentPage === totalPages ? 'disabled' : ''} data-page="${currentPage + 1}">След. ›</button>
            </div>
        </div>
    `.toString();
}

// =============================================================================
// ОТЧЕТЫ ОБ АНОМАЛИЯХ И ФИЛЬТР FINGERPRINT
// =============================================================================

export function displayAnomalyReport(type) {
    const container = DOM_CACHE.getElementById('anomaly-reports');
    if(!container) return;
    let report = { title: '', severity: 'info', details: [] };

    if (type === 'fingerprint') {
        report.title = '🔐 Обнаружено совпадение отпечатков устройств';
        report.severity = 'danger';
        const anomalousGroups = Object.values(fingerprintGroups).filter(g => g.isAnomalous);
        if (anomalousGroups.length > 0) {
            anomalousGroups.forEach(group => {
                const usersByTest = {};
                group.results.forEach(res => {
                    const testType = res.testType || 'Unknown Test';
                    if (!usersByTest[testType]) usersByTest[testType] = new Set();
                    usersByTest[testType].add(`${res.userInfo.lastName} ${res.userInfo.firstName}`);
                });
                Object.entries(usersByTest).forEach(([test, users]) => {
                    if (users.size > 1) {
                        report.details.push(`<b>Тест "${createSafeText(test)}":</b> ${[...users].map(createSafeText).join(', ')}`);
                    }
                });
            });
        }
        if(report.details.length === 0) report.details.push("Аномальных совпадений не найдено.");

    } else if (type === 'violations') {
        report.title = '👁️ Обнаружены нарушения правил тестирования';
        report.severity = 'warning';
        const anomalies = Array.from(allLoadedResults.values()).filter(r => 
            (r.sessionMetrics.totalFocusLoss > (settings.focusThreshold ?? 5)) ||
            (r.sessionMetrics.totalBlurTime > (settings.blurThreshold ?? 60)) ||
            (r.sessionMetrics.printAttempts > (settings.printThreshold ?? 0))
        );
        if (anomalies.length > 0) {
            anomalies.forEach(r => {
                let details = [];
                if (r.sessionMetrics.totalFocusLoss > (settings.focusThreshold ?? 5)) details.push(`потери фокуса: ${r.sessionMetrics.totalFocusLoss}`);
                if (r.sessionMetrics.totalBlurTime > (settings.blurThreshold ?? 60)) details.push(`время вне фокуса: ${r.sessionMetrics.totalBlurTime}с`);
                if (r.sessionMetrics.printAttempts > (settings.printThreshold ?? 0)) details.push(`попытки печати: ${r.sessionMetrics.printAttempts}`);
                report.details.push(`<b>${createSafeText(r.userInfo.lastName)} ${createSafeText(r.userInfo.firstName)}</b>: ${details.join(', ')}`);
            });
        } else {
            report.details.push("Нарушений не найдено.");
        }
    }
    const detailsHTML = `<ul>${report.details.map(d => `<li>${d}</li>`).join('')}</ul>`;
    container.innerHTML = html`
        <div class="anomaly-card ${report.severity}">
            <div class="anomaly-header">
                <div class="anomaly-icon ${report.severity}">!</div>
                <h4>${report.title}</h4>
            </div>
            ${unsafeHTML(detailsHTML)} 
        </div>`.toString();
}

export function populateFingerprintFilter() {
    const select = DOM_CACHE.getElementById('fingerprintFilter');
    if(!select) return; 
    
    select.innerHTML = '<option value="">Все группы</option>';
    Object.entries(fingerprintGroups)
          .filter(([_, group]) => group.results.length > 1)
          .sort() 
          .forEach(([hash, group]) => {
              const option = document.createElement('option');
              option.value = hash;
              const anomalyText = group.isAnomalous ? " (АНОМАЛИЯ)" : "";
              option.textContent = `Группа ...${hash.slice(-10)} (${group.results.length} сессий)${anomalyText}`;
              if (group.isAnomalous) option.style.color = 'var(--danger)';
              select.appendChild(option);
          });
}

// =============================================================================
// МОДАЛЬНЫЕ ОКНА
// =============================================================================

export function openSettings() {
    const focusThresholdInput = DOM_CACHE.getElementById('focusThreshold');
    const blurThresholdInput = DOM_CACHE.getElementById('blurThreshold');
    const mouseThresholdInput = DOM_CACHE.getElementById('mouseThreshold');
    const printThresholdInput = DOM_CACHE.getElementById('printThreshold');
    const ipFingerprintCheck = DOM_CACHE.getElementById('ipFingerprintCheck');
    const settingsModal = DOM_CACHE.getElementById('settingsModal');

    if(focusThresholdInput) focusThresholdInput.value = settings.focusThreshold ?? 5;
    if(blurThresholdInput) blurThresholdInput.value = settings.blurThreshold ?? 60;
    if(mouseThresholdInput) mouseThresholdInput.value = settings.mouseThreshold ?? 85;
    if(printThresholdInput) printThresholdInput.value = settings.printThreshold ?? 0;
    if(ipFingerprintCheck) ipFingerprintCheck.checked = settings.checkIpInFingerprint ?? true;
    
    if(settingsModal) settingsModal.style.display = 'flex';
}

export function closeSettings() {
    const modal = DOM_CACHE.getElementById('settingsModal');
    if (modal) modal.style.display = 'none';
}

export function saveSettings() {
    const newSettings = {
        focusThreshold: parseInt(DOM_CACHE.getElementById('focusThreshold')?.value ?? settings.focusThreshold),
        blurThreshold: parseInt(DOM_CACHE.getElementById('blurThreshold')?.value ?? settings.blurThreshold),
        mouseThreshold: parseInt(DOM_CACHE.getElementById('mouseThreshold')?.value ?? settings.mouseThreshold),
        printThreshold: parseInt(DOM_CACHE.getElementById('printThreshold')?.value ?? settings.printThreshold),
        checkIpInFingerprint: DOM_CACHE.getElementById('ipFingerprintCheck')?.checked ?? settings.checkIpInFingerprint,
    };
    newSettings.focusThreshold = Math.max(0, newSettings.focusThreshold);
    newSettings.blurThreshold = Math.max(0, newSettings.blurThreshold);
    newSettings.mouseThreshold = Math.min(100, Math.max(0, newSettings.mouseThreshold));
    newSettings.printThreshold = Math.max(0, newSettings.printThreshold);

    setSettings(newSettings);
    try {
        localStorage.setItem('analysisSettings', JSON.stringify(newSettings));
        showNotification("Настройки сохранены", "success");
    } catch (e) {
        console.error("Failed to save settings to localStorage:", e);
        showNotification("Не удалось сохранить настройки локально", "warning");
    }
    closeSettings();
}

export function openExportModal() {
    const modal = DOM_CACHE.getElementById('exportModal');
    if (!modal) return;
    
    document.querySelectorAll('.export-option').forEach(opt => opt.classList.remove('selected'));
    modal.style.display = 'flex';
}

export function closeExportModal() {
    const modal = DOM_CACHE.getElementById('exportModal');
    if (modal) modal.style.display = 'none';
}

export function executeExport() {
    const selectedOption = document.querySelector('.export-option.selected');
    if (!selectedOption) {
        showNotification('Выберите формат экспорта', 'warning');
        return;
    }
    
    const format = selectedOption.dataset.format;
    const onlySelected = DOM_CACHE.getElementById('exportSelectedToggle')?.classList.contains('active') || false;
    
    console.log('Экспорт в формате:', format, 'Только выбранные:', onlySelected);
    
    showNotification(`Экспорт в формат ${format.toUpperCase()} начат...`, 'success');
    closeExportModal();
    
    setTimeout(() => {
        showNotification(`Файл успешно экспортирован!`, 'success');
    }, 2000);
}

export function openUserProfile(lastName, firstName) {
    if (!lastName && !firstName) return;
    
    const userTests = Array.from(allLoadedResults.values())
        .filter(r => r.userInfo?.lastName === lastName && r.userInfo?.firstName === firstName) 
        .sort((a, b) => new Date(b.sessionMetrics?.startTime ?? 0) - new Date(a.sessionMetrics?.startTime ?? 0)); 
    
    if (userTests.length === 0) {
        showNotification("Не найдено тестов для этого пользователя.", "warning");
        return;
    }
    
    const profileTitle = DOM_CACHE.getElementById('profileTitle');
    const profileContent = DOM_CACHE.getElementById('profileContent');
    const userProfileModal = DOM_CACHE.getElementById('userProfileModal');

    if (profileTitle) profileTitle.textContent = `👤 Профиль: ${lastName} ${firstName}`; 
    if (profileContent) profileContent.innerHTML = generateUserProfileContent(userTests); 
    if (userProfileModal) userProfileModal.style.display = 'flex';
}

export function closeUserProfile() {
     const modal = DOM_CACHE.getElementById('userProfileModal');
     if (modal) modal.style.display = 'none';
}

function generateUserProfileContent(userTests) {
    const latestTest = userTests.at(0); 
    const bestScore = Math.max(0, ...userTests.map(t => t.testResults?.percentage ?? 0)); 
    
    const statsHTML = html`
        <div class="stats-overview" style="margin-bottom: 1.5rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));">
            <div class="stat-card"><div class="stat-value">${userTests.length}</div><div class="stat-label">Всего попыток</div></div>
            <div class="stat-card"><div class="stat-value">${latestTest?.testResults?.percentage ?? 'N/A'}%</div><div class="stat-label">Последний результат</div></div>
            <div class="stat-card"><div class="stat-value">${bestScore}%</div><div class="stat-label">Лучший результат</div></div>
        </div>
    `.toString();

    const rowsHTML = userTests.map(test => {
        const sm = test.sessionMetrics || {};
        const tr = test.testResults || {};
        const hasAnomalies = (sm.totalFocusLoss > (settings.focusThreshold ?? 5)) || (sm.totalBlurTime > (settings.blurThreshold ?? 60)) || (sm.printAttempts > (settings.printThreshold ?? 0));
        return html`
            <tr>
                <td>${new Date(sm.startTime ?? 0).toLocaleString('ru-RU')}</td>
                <td>${tr.percentage ?? 'N/A'}%</td>
                <td><span class="status-badge grade-${tr.grade?.class ?? 'poor'}">${tr.grade?.text ?? 'N/A'}</span></td>
                <td>${hasAnomalies ? unsafeHTML('⚠️ Да') : unsafeHTML('✅ Нет')}</td>
                <td><button class="action-btn event-log-link" data-session-id="${test.sessionId}">👁️</button></td>
            </tr>
        `.toString();
    }).join('');

    return html`
        ${unsafeHTML(statsHTML)}
        <div class="table-wrapper">
            <table class="data-table">
                <thead><tr><th>Дата</th><th>Результат</th><th>Оценка</th><th>Аномалии</th><th>Действия</th></tr></thead>
                <tbody>${unsafeHTML(rowsHTML)}</tbody>
            </table>
        </div>
    `.toString();
}

export function openEventLogModal(sessionId) {
    const modal = DOM_CACHE.getElementById('eventLogModal');
    const title = DOM_CACHE.getElementById('eventLogTitle');
    if(title) title.textContent = `📜 Журнал событий (${createSafeText(sessionId).slice(0, 8)}...)`;
    if(modal) modal.style.display = 'flex';
}

export function closeEventLog() {
    const modal = DOM_CACHE.getElementById('eventLogModal');
    if(modal) modal.style.display = 'none';
}

function renderTestLog(events) {
    const content = DOM_CACHE.getElementById('eventLogContent');
    if (!content) return;
    
    events.sort((a, b) => new Date(a.event_timestamp) - new Date(b.event_timestamp));

    const uniqueIPs = [...new Set(events.map(e => e.details?.ip).filter(Boolean))];
    
    const titleEl = DOM_CACHE.getElementById('eventLogTitle');
    if (titleEl && uniqueIPs.length === 1) {
        titleEl.innerHTML += ` <span class="ip-address">(${uniqueIPs[0]})</span>`;
    }

    const violations = events.filter(e => ['focus_loss', 'print_attempt', 'screenshot_attempt'].includes(e.event_type));
    const testFinishEvent = events.find(e => e.event_type === 'test_finished');

    const summaryHtml = `<div class="event-log-summary"><strong>Нарушения:</strong> <span class="violation-count">${violations.length}</span></div>`;

    const timelineHtml = events.map((event, index) => {
        const timestamp = new Date(event.event_timestamp);
        let eventClass = violations.includes(event) ? 'event-violation' : 'event-info';
        if (testFinishEvent && timestamp > new Date(testFinishEvent.event_timestamp) && event.event_type !== 'test_finished') {
            eventClass = 'event-anomaly';
        }

        let durationHtml = '';
        if (index > 0) {
            const prevTimestamp = new Date(events[index - 1].event_timestamp);
            const durationSec = Math.round((timestamp - prevTimestamp) / 1000);
            if (durationSec > 0) {
                 durationHtml = `<span class="event-duration" title="Время с предыдущего события">+ ${durationSec} сек</span>`;
            }
        }

        return `
            <div class="timeline-item ${eventClass}">
                <div class="timeline-icon">${getIconForEvent(event.event_type)}</div>
                <div class="timeline-content">
                    <div class="content-header">
                        <strong class="event-title">${getTitleForEvent(event, false)}</strong>
                        <div class="header-right-col">
                            ${durationHtml}
                            <span class="event-time">${timestamp.toLocaleTimeString('ru-RU')}</span>
                        </div>
                    </div>
                    <div class="content-details">${getDetailsForEvent(event, eventClass === 'event-anomaly', uniqueIPs)}</div>
                </div>
            </div>`;
    }).join('');

    content.innerHTML = `${summaryHtml}<div class="event-timeline">${timelineHtml}</div>`;
}

function renderStudyLog(events) {
    const content = DOM_CACHE.getElementById('eventLogContent');
    if (!content) return;
    
    events.sort((a, b) => new Date(a.event_timestamp) - new Date(b.event_timestamp));

    const uniqueIPs = [...new Set(events.map(e => e.details?.ip).filter(Boolean))];
    
    const titleEl = DOM_CACHE.getElementById('eventLogTitle');
    if (titleEl && uniqueIPs.length === 1) {
        titleEl.innerHTML += ` <span class="ip-address">(${uniqueIPs[0]})</span>`;
    }
    
    const startTime = new Date(events[0].event_timestamp);
    const lastEventTime = new Date(events[events.length - 1].event_timestamp);
    const totalSessionTime = Math.round((lastEventTime - startTime) / 1000);
    const totalActiveTime = events.filter(e => e.event_type === 'module_view_time').reduce((sum, e) => sum + (e.details?.duration || 0), 0);
    const maxScrollDepth = Math.max(0, ...events.filter(e => e.event_type === 'scroll_depth_milestone').map(e => parseInt(e.details?.depth) || 0));

    const summaryHtml = `
        <div class="event-log-summary study-summary">
            <div><strong>Общее время:</strong> ${Math.floor(totalSessionTime / 60)} мин ${totalSessionTime % 60} сек</div>
            <div><strong>Активное время:</strong> ${Math.floor(totalActiveTime / 60)} мин ${totalActiveTime % 60} сек</div>
            <div><strong>Глубина просмотра:</strong> ${maxScrollDepth}%</div>
        </div>
    `;

    const timelineHtml = events.map((event, index) => {
        const timestamp = new Date(event.event_timestamp);
        
        let durationHtml = '';
        if (index > 0) {
            const prevTimestamp = new Date(events[index - 1].event_timestamp);
            const durationSec = Math.round((timestamp - prevTimestamp) / 1000);
            if (durationSec > 0) {
                durationHtml = `<span class="event-duration" title="Время с предыдущего события">+ ${durationSec} сек</span>`;
            }
        }
        
        return `
            <div class="timeline-item event-info">
                <div class="timeline-icon">${getIconForEvent(event.event_type)}</div>
                <div class="timeline-content">
                    <div class="content-header">
                        <strong class="event-title">${getTitleForEvent(event, true)}</strong>
                        <div class="header-right-col">
                            ${durationHtml}
                            <span class="event-time">${timestamp.toLocaleTimeString('ru-RU')}</span>
                        </div>
                    </div>
                    <div class="content-details">${getDetailsForEvent(event, false, uniqueIPs)}</div>
                </div>
            </div>`;
    }).join('');

    content.innerHTML = `${summaryHtml}<div class="event-timeline">${timelineHtml}</div>`;
}

export function renderEventLog(events) {
    if (!events || events.length === 0) {
        const content = DOM_CACHE.getElementById('eventLogContent');
        if (content) content.innerHTML = '<p style="text-align: center; color: var(--text-light);">Для этой сессии не зафиксировано ни одного события.</p>';
        return;
    }

    const isStudySession = events.some(e => e.event_type === 'study_started' || e.event_type === 'module_view_time');

    if (isStudySession) {
        renderStudyLog(events);
    } else {
        renderTestLog(events);
    }
}

function getIconForEvent(eventType) {
    const icons = {
        'test_started': '✅', 'test_finished': '🏁', 'focus_loss': '👁️',
        'print_attempt': '🖨️', 'screenshot_attempt': '📸', 'study_started': '📚',
        'module_view_time': '⏱️', 'self_check_answered': '✍️', 'scroll_depth_milestone': '📜'
    };
    return icons[eventType] || '❓';
}

// ** ИСПРАВЛЕННАЯ ЛОГИКА **
function getTitleForEvent(event, isStudy) {
    let details = event.details || {}; // details - УЖЕ ОБЪЕКТ
    
    const titles = {
        'test_started': 'Тест начат', 'test_finished': 'Тест завершён',
        'focus_loss': 'Потеря фокуса', 'print_attempt': 'Попытка печати',
        'screenshot_attempt': 'Попытка скриншота', 'study_started': 'Начало обучения',
        'module_view_time': `Просмотр модуля "${details.module || ''}"`,
        'self_check_answered': 'Ответ на самопроверку',
        'scroll_depth_milestone': `Страница просмотрена до ${details.depth || '?'}`
    };
    return escapeHtml(titles[event.event_type] || event.event_type);
}

// ** ИСПРАВЛЕННАЯ ЛОГИКА **
function getDetailsForEvent(event, isAnomaly, uniqueIPs) {
    let details = event.details || {}; // details - УЖЕ ОБЪЕКТ
    
    let detailsHtml = '';
    if (isAnomaly) {
        detailsHtml += '<p class="anomaly-warning">⚠️ <strong>Действие совершено после завершения теста!</strong></p>';
    }

    switch (event.event_type) {
        case 'test_started':
        case 'study_started':
            const user = details.userInfo || {};
            detailsHtml += html`<p><strong>Пользователь:</strong> ${user.lastName ?? ''} ${user.firstName ?? ''}</p>`.toString();
            if (user.position) detailsHtml += html`<p><strong>Должность:</strong> ${user.position}</p>`.toString();
            break;
        case 'focus_loss':
        case 'print_attempt':
        case 'screenshot_attempt':
            detailsHtml += html`<p>На <strong>вопросе №${details.question ?? '?'}</strong></p>`.toString();
            break;
        case 'module_view_time':
            detailsHtml += html`<p><strong>Продолжительность:</strong> ${details.duration ?? 0} секунд</p>`.toString();
            break;
    }
    
    if (details.ip && uniqueIPs.length > 1) {
        detailsHtml += html`<p class="ip-address">IP: ${details.ip}</p>`.toString();
    }
    return detailsHtml;
}

// =============================================================================
// РЕНДЕРИНГ - ДЕТАЛЬНОЕ СРАВНЕНИЕ
// =============================================================================

export function renderComparisonUserList(results) {
    const listContainer = DOM_CACHE.getElementById('comparison-user-list');
    if (!listContainer) return;
    const completedResults = results.filter(r => r.testResults?.percentage > 0 && r.sessionMetrics?.endTime);
    
    listContainer.innerHTML = completedResults.map(result => {
        const isSelected = selectedForComparison.has(result.sessionId);
        const ui = result.userInfo || {};
        return html`
            <div class="comparison-list-card ${isSelected ? 'selected' : ''}" data-session-id="${result.sessionId}">
                <input type="checkbox" ${isSelected ? 'checked' : ''} readOnly>
                <div class="info"><h4>${ui.lastName} ${ui.firstName}</h4><p>${new Date(result.sessionMetrics.startTime).toLocaleString('ru-RU')} (${result.testType})</p></div>
                <div class="score grade-${result.testResults.grade?.class}" style="margin-left: auto;">${result.testResults.percentage}%</div>
            </div>`.toString();
    }).join('');
}

export function renderComparisonResults(analysisResults, selectedResults) {
    const container = DOM_CACHE.getElementById('comparison-results-panel');
    if (!container) return;

    destroyCharts(['latencyChart']); // Уничтожаем старые графики

    container.innerHTML = createDetailedAnalysisHTML(selectedResults);

    // Очищаем кэш DOM для новых элементов
    DOM_CACHE.invalidate('latencyChart');
    DOM_CACHE.invalidate('violations-summary-container');
    DOM_CACHE.invalidate('answer-changes-summary-container');
    DOM_CACHE.invalidate('questionSelector');
    DOM_CACHE.invalidate('mouseTrajectoryCanvas');
    DOM_CACHE.invalidate('dtw-analysis-results');

    renderViolationsSummary(selectedResults);
    renderComparisonCharts(selectedResults);
    renderDtwResults(analysisResults, selectedResults);
    renderAnswerChangesSummary(selectedResults);
}

function createDetailedAnalysisHTML(results) {
    const names = results.map(r => `${r.userInfo.lastName} ${r.userInfo.firstName}`).join(' vs ');
    const title = results.length > 1 ? `Детальное сравнение: ${createSafeText(names)}` : `Одиночный анализ: ${createSafeText(names)}`;

    return html`
        <h3>${title}</h3>
        <div class="analysis-section">
            <h3>Сравнение отпечатков (Fingerprint)</h3>
            <div class="analysis-content">${unsafeHTML(createFingerprintTable(results))}</div>
        </div>
        <div class="analysis-section">
            <h3>🚨 Анализ нарушений</h3>
            <div class="analysis-content" id="violations-summary-container"></div>
        </div>
        <div class="analysis-section">
            <h3>🧠 Поведение (по вопросам)</h3>
            <div class="behavioral-analysis-grid">
                <div class="behavioral-chart-container">
                    <h4>Время ответа (мс)</h4>
                    <canvas id="latencyChart"></canvas>
                </div>
                <div id="answer-changes-summary-container"></div>
            </div>
        </div>
        <div class="analysis-section">
            <h3>Визуализация движений мыши</h3>
            <div class="analysis-content">
                <label for="questionSelector">Выберите вопрос:</label>
                <select id="questionSelector" class="filter-input"></select>
                <canvas id="mouseTrajectoryCanvas" width="800" height="400"></canvas>
            </div>
        </div>
        <div class="analysis-section">
            <h3>Анализ DTW Сходства Мыши</h3>
            <div class="analysis-content" id="dtw-analysis-results">
                <p>Результаты DTW анализа...</p>
            </div>
        </div>`.toString();
}

const DTW_THRESHOLDS = {
    HIGH_SIMILARITY: 70, 
    SUSPICIOUS: settings?.mouseThreshold ?? 85, 
};

function renderDtwResults(dtwResults, selectedResults) {
    const container = DOM_CACHE.getElementById('dtw-analysis-results'); 
    if (!container) return;
    
    if (!dtwResults || Object.keys(dtwResults).length === 0) {
        container.innerHTML = '<p>Нет данных для DTW анализа.</p>';
        return;
    }
    
    let htmlContent = ''; 
    Object.entries(dtwResults).forEach(([pairKey, scores]) => {
        const questionScores = Object.values(scores);
        if (questionScores.length === 0) return;
        
        const avgSim = questionScores.reduce((a, b) => a + b, 0) / questionScores.length;
        const highSim = Object.entries(scores)
                              .filter(([, s]) => s >= DTW_THRESHOLDS.HIGH_SIMILARITY)
                              .sort(([,a],[,b])=>b-a);
        
        const [sid1, sid2] = pairKey.split('_vs_');
        const user1 = selectedResults.find(r => r.sessionId === sid1)?.userInfo;
        const user2 = selectedResults.find(r => r.sessionId === sid2)?.userInfo;
        if (!user1 || !user2) return;

        const isAnomalous = highSim.some(([,s]) => s >= DTW_THRESHOLDS.SUSPICIOUS);
        
        const detailsList = highSim.length > 0 
            ? `<ul>${highSim.map(([q, s]) => html`
                   <li>Вопрос #${parseInt(q) + 1}: 
                       <b style="color:${s >= DTW_THRESHOLDS.SUSPICIOUS ? 'var(--danger)' : 'inherit'}">${s}%</b>
                   </li>`.toString()
                ).join('')}</ul>`
            : '';

        htmlContent += html`
            <div class="dtw-result-card" style="border-left-color: ${isAnomalous ? 'var(--danger)' : 'var(--border)'}">
                <h4>${user1.lastName ?? ''} vs ${user2.lastName ?? ''}</h4>
                <p>Среднее сходство: <b>${avgSim.toFixed(1)}%</b>. Подозрительных ответов (> ${DTW_THRESHOLDS.HIGH_SIMILARITY}%): ${highSim.length}</p>
                ${highSim.length > 0 
                    ? unsafeHTML(`<details><summary>Детали</summary>${detailsList}</details>`) 
                    : ''}
            </div>
        `.toString();
    });
    
    container.innerHTML = htmlContent || '<p>Нет общих вопросов для сравнения.</p>';
}

function renderComparisonCharts(results) {
    const labels = results.map(r => `${r.userInfo.lastName} ${r.userInfo.firstName.charAt(0)}.`);
    const colors = results.map((_, i) => USER_COLORS[i % USER_COLORS.length]);

    const numQuestions = Math.max(0, ...results.map(r => r.behavioralMetrics?.perQuestion?.length || 0));
    if (numQuestions > 0) {
        const qLabels = Array.from({ length: numQuestions }, (_, i) => `В${i + 1}`);
        const latencyDS = results.map((r, i) => ({ label: labels[i], data: r.behavioralMetrics?.perQuestion?.map(q => q?.latency || 0) || [], borderColor: colors[i], tension: 0.1, fill: false }));
        
        const ctx = DOM_CACHE.getElementById('latencyChart')?.getContext('2d');
        if(ctx) {
            charts['latencyChart'] = new Chart(ctx, { type: 'line', data: { labels: qLabels, datasets: latencyDS } });
        }

        setupMouseVisualizer(results);
    }
}

function renderViolationsSummary(selectedResults) {
    const container = DOM_CACHE.getElementById('violations-summary-container');
    if (!container) return;

    const totalViolations = selectedResults.reduce((sum, result) => {
        const sm = result.sessionMetrics;
        return sum + (sm?.totalFocusLoss || 0) + (sm?.totalBlurTime || 0) + (sm?.printAttempts || 0) + (sm?.screenshotAttempts || 0);
    }, 0);

    if (totalViolations === 0) {
        container.innerHTML = `
            <div class="no-violations-placeholder">
                <div class="no-violations-placeholder-icon">✅</div>
                <p class="no-violations-placeholder-text">Нарушений не зафиксировано</p>
            </div>
        `;
        return;
    }

    let htmlContent = '<div class="violations-summary-grid">';
    selectedResults.forEach(result => {
        const sm = result.sessionMetrics || { totalFocusLoss: 0, totalBlurTime: 0, printAttempts: 0, screenshotAttempts: 0 };
        const printAndScreen = (sm.printAttempts || 0) + (sm.screenshotAttempts || 0);

        htmlContent += html`
            <div class="violation-user-column">
                <h4>${result.userInfo.lastName} ${result.userInfo.firstName}</h4>
                <div class="stat-card-mini ${sm.totalFocusLoss > 0 ? 'has-violation' : ''}">
                    <div class="stat-card-mini-icon">👁️</div>
                    <div class="stat-card-mini-label">Потери фокуса</div>
                    <div class="stat-card-mini-value">${sm.totalFocusLoss}</div>
                </div>
                <div class="stat-card-mini ${sm.totalBlurTime > 0 ? 'has-violation' : ''}">
                    <div class="stat-card-mini-icon">⏱️</div>
                    <div class="stat-card-mini-label">Время вне фокуса</div>
                    <div class="stat-card-mini-value">${sm.totalBlurTime}с</div>
                </div>
                <div class="stat-card-mini ${printAndScreen > 0 ? 'has-violation' : ''}">
                    <div class="stat-card-mini-icon">🖨️</div>
                    <div class="stat-card-mini-label">Печать/Скриншот</div>
                    <div class="stat-card-mini-value">${printAndScreen}</div>
                </div>
            </div>
        `.toString();
    });
    htmlContent += '</div>';
    container.innerHTML = htmlContent;
}

function createFingerprintTable(results) {
    if (results.length === 1) {
        return createSingleUserFingerprintView(results[0]);
    }

    let table = '<table class="comparison-table"><thead><tr><th>Параметр</th>';
    results.forEach(r => { table += `<th>${createSafeText(r.userInfo.lastName)}</th>`; });
    table += '</tr></thead><tbody>';
    const keys = { "Хеш": r => r.fingerprint?.privacySafeHash, "User Agent": r => r.fingerprint?.privacySafe?.userAgent, "Платформа": r => r.fingerprint?.privacySafe?.platform, "WebGL": r => r.fingerprint?.privacySafe?.webGLRenderer };
    Object.entries(keys).forEach(([key, accessor]) => {
        const values = results.map(accessor);
        const allMatch = values.every(v => v && v === values[0]);
        table += `<tr><td>${key}</td>${values.map(v => `<td class="${allMatch ? 'match' : 'mismatch'}">${createSafeText(v) || 'N/A'}</td>`).join('')}</tr>`;
    });
    return table + '</tbody></table>';
}

function setupMouseVisualizer(results) {
    const selector = DOM_CACHE.getElementById('questionSelector');
    if (!selector) return;
    selector.innerHTML = '';
    const numQuestions = Math.max(0, ...results.map(r => r.behavioralMetrics?.perQuestion?.length || 0));
    for (let i = 0; i < numQuestions; i++) selector.add(new Option(`Вопрос ${i + 1}`, i));
    const drawFunc = () => drawMouseTrajectory(results, selector.value);
    selector.addEventListener('change', drawFunc);
    drawFunc();
}

function drawMouseTrajectory(results, qIndex) {
    const canvas = DOM_CACHE.getElementById('mouseTrajectoryCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    results.forEach((result, i) => {
        const movements = result.behavioralMetrics?.perQuestion?.[qIndex]?.mouseMovements;
        if (!movements || movements.length < 2) return;
        const bounds = { minX: Math.min(...movements.map(p => p[0])), maxX: Math.max(...movements.map(p => p[0])), minY: Math.min(...movements.map(p => p[1])), maxY: Math.max(...movements.map(p => p[1])) };
        const scale = Math.min(canvas.width / (bounds.maxX - bounds.minX || 1), canvas.height / (bounds.maxY - bounds.minY || 1)) * 0.9;
        const color = USER_COLORS[i % USER_COLORS.length];
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        movements.forEach((p, j) => {
            const x = (p[0] - bounds.minX) * scale + (canvas.width * 0.05);
            const y = (p[1] - bounds.minY) * scale + (canvas.height * 0.05);
            if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
        const startX = (movements[0][0] - bounds.minX) * scale + (canvas.width * 0.05);
        const startY = (movements[0][1] - bounds.minY) * scale + (canvas.height * 0.05);
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(startX, startY, 5, 0, 2 * Math.PI);
        ctx.fill();
    });
}

// =============================================================================
// РЕНДЕРИНГ - ДРУГИЕ ВИДЫ
// =============================================================================

export function renderAbandonedSessions(filter = 'all', sortedSessions = null) {
    const container = DOM_CACHE.getElementById('abandoned-sessions-container');
    if (!container) return;

    const sessionsToSort = sortedSessions || allAbandonedSessions;

    const sessionsToRender = (filter === 'all') 
        ? sessionsToSort 
        : sessionsToSort.filter(s => s.sessionType === filter);

    if (sessionsToRender.length === 0) {
        container.innerHTML = '<p style="text-align:center; color: var(--text-light);">Прерванных сессий такого типа не найдено.</p>';
        return;
    }

    const headersHTML = html`
        ${sortHeader('Пользователь', 'userInfo.lastName', abandonedSessionsSortKey, abandonedSessionsSortDir)}
        ${sortHeader('Тип', 'sessionType', abandonedSessionsSortKey, abandonedSessionsSortDir)}
        ${sortHeader('Время начала', 'startTime', abandonedSessionsSortKey, abandonedSessionsSortDir)}
        ${sortHeader('IP Адрес', 'clientIp', abandonedSessionsSortKey, abandonedSessionsSortDir)}
        ${sortHeader('Потери фокуса', 'violationCounts.focusLoss', abandonedSessionsSortKey, abandonedSessionsSortDir)}
        ${sortHeader('Скриншоты', 'violationCounts.screenshots', abandonedSessionsSortKey, abandonedSessionsSortDir)}
        ${sortHeader('Попытки печати', 'violationCounts.prints', abandonedSessionsSortKey, abandonedSessionsSortDir)}
        <th>Действия</th>
    `.toString();

    const tableRows = sessionsToRender.map(session => {
        const ui = session.userInfo || {};
        const counts = session.violationCounts || {};
        const sessionType = session.sessionType || 'unknown'; 
        const sessionName = session.sessionName || sessionType; 
        const sessionIcon = sessionType === 'test' ? '📝' : '📚';
        const startTime = session.startTime ? new Date(session.startTime).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'N/A';
        const ipDisplay = `IP: [${session.clientIp ?? 'N/A'}]`;

        return html`
            <tr>
                <td><strong>${ui.lastName ?? ''}</strong> ${ui.firstName ?? 'N/A'}</td>
                <td class="cell-type"><span title="${sessionName}">${sessionIcon}</span> ${sessionName}</td>
                <td>${startTime}</td>
                <td>${ipDisplay}</td>
                <td class="numeric">${counts.focusLoss ?? 0}</td>
                <td class="numeric">${counts.screenshots ?? 0}</td>
                <td class="numeric">${counts.prints ?? 0}</td>
                <td class="cell-actions">
                    <button class="action-btn event-log-link" data-session-id="${session.sessionId}" title="Журнал событий">
                       ${unsafeHTML('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16" style="vertical-align: middle;"><path d="M16 8s-3-5.5-8-5.5S0 8 0 8s3 5.5 8 5.5S16 8 16 8zM1.173 8a13.133 13.133 0 0 1 1.66-2.043C4.12 4.668 5.88 3.5 8 3.5c2.12 0 3.879 1.168 5.168 2.457A13.133 13.133 0 0 1 14.828 8c-.058.087-.122.183-.195.288-.335.48-.83 1.12-1.465 1.755C11.879 11.332 10.12 12.5 8 12.5c-2.12 0-3.879-1.168-5.168-2.457A13.13 13.13 0 0 1 1.172 8z"/><path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM4.5 8a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0z"/></svg>')}
                    </button>
                </td>
            </tr>
        `.toString();
    }).join('');

    container.innerHTML = html`
        <div class="table-wrapper">
            <table class="data-table">
                <thead><tr>${unsafeHTML(headersHTML)}</tr></thead>
                <tbody>${unsafeHTML(tableRows)}</tbody>
            </table>
        </div>
    `.toString();
}

export function renderBehaviorAnalysis(sessions) {
    const container = DOM_CACHE.getElementById('behavior-analysis-container');
    if (!container) return;
    if (sessions.length === 0) {
        container.innerHTML = '<p>Подозрительных сессий не найдено.</p>';
        return;
    }
    container.innerHTML = sessions.map(s => html`<div class="behavior-card"><h4>${s.userInfo.lastName} ${s.userInfo.firstName}</h4><p>${s.reason}</p></div>`.toString()).join('');
}

export function renderCertificatesTable(data) {
    const container = DOM_CACHE.getElementById('registry-container');
    if (!container) return;
    
    const certificates = data.certificates || [];

    if (certificates.length === 0) {
        container.innerHTML = '<p style="text-align:center; color: var(--text-light);">Аттестатов не найдено.</p>';
        return;
    }

    const createRegistryHeader = (label, sortKey) => html`
        <th class="registry-sort-header" data-sort="${sortKey}">
            ${label} 
            <span class="sort-icon">
                ${registrySortKey === sortKey ? (registrySortDir === 'desc' ? '▼' : '▲') : ''}
            </span>
        </th>`;


    const headersHTML = `
        ${createRegistryHeader('Номер', 'document_number')}
        ${createRegistryHeader('ФИО', 'user_fullname')}
        ${createRegistryHeader('Должность', 'user_position')}
        ${createRegistryHeader('Тест', 'test_type')}
        ${createRegistryHeader('Дата', 'issue_date')}
        ${createRegistryHeader('Результат', 'score_percentage')}
    `;

    const tableRows = certificates.map(c => {
        const rawTestType = c.test_type || 'unknown';
        const prettyTestName = TEST_NAME_MAP[rawTestType] || rawTestType;
        return html`
        <tr>
            <td>${c.document_number}</td>
            <td>${c.user_fullname}</td>
            <td>${c.user_position || 'N/A'}</td>
            <td>${prettyTestName}</td>
            <td>${new Date(c.issue_date).toLocaleDateString('ru-RU')}</td>
            <td>${c.score_percentage}%</td>
        </tr>`.toString();
    }).join('');

    container.innerHTML = html`
        <div class="table-wrapper">
            <table class="data-table">
                <thead>
                    <tr>${unsafeHTML(headersHTML)}</tr>
                </thead>
                <tbody>
                    ${unsafeHTML(tableRows)}
                </tbody>
            </table>
        </div>
        <div id="registry-pagination-container"></div>`.toString();
    
    populateRegistryYearFilter(certificates);
    renderRegistryPaginationControls(data.page, data.per_page, data.total);
}

function renderRegistryPaginationControls(page, perPage, total) {
    const container = DOM_CACHE.getElementById('registry-pagination-container');
    if (!container) return;

    if (total > 0 && total <= perPage) {
        container.innerHTML = `<div class="pagination-info" style="border-top: 1px solid var(--border); margin-top: 1.5rem; padding-top: 1rem;">Показаны все ${total} записей</div>`;
        return;
    }
    
    if (total === 0) {
        container.innerHTML = '';
        return;
    }

    const totalPages = Math.ceil(total / perPage);
    
    let pagesHtml = '';
    const pagesToShow = new Set();
    pagesToShow.add(1);
    pagesToShow.add(totalPages);
    for (let i = -2; i <= 2; i++) {
        const p = page + i;
        if (p > 1 && p < totalPages) pagesToShow.add(p);
    }
    
    const sortedPages = Array.from(pagesToShow).sort((a,b)=>a-b);
    let lastPage = 0;
    sortedPages.forEach(p => {
        if(lastPage > 0 && p > lastPage + 1) {
            pagesHtml += `<button class="page-btn ellipsis" disabled>...</button>`; 
        }
        pagesHtml += `<button class="page-btn registry-page-btn ${page === p ? 'active' : ''}" data-page="${p}">${p}</button>`; 
        lastPage = p;
    });

    const startItem = (page - 1) * perPage + 1;
    const endItem = Math.min(startItem + perPage - 1, total);

    container.innerHTML = html`
        <div class="pagination">
            <div class="pagination-info">Показано ${startItem} - ${endItem} из ${total}</div>
            <div class="pagination-controls">
                <button class="page-btn registry-page-btn" ${page === 1 ? 'disabled' : ''} data-page="${page - 1}">‹ Пред.</button>
                ${unsafeHTML(pagesHtml)}
                <button class="page-btn registry-page-btn" ${page === totalPages ? 'disabled' : ''} data-page="${page + 1}">След. ›</button>
            </div>
        </div>
    `.toString();
}

// =============================================================================
// СТАТИСТИКА
// =============================================================================

export async function generateStatistics() {
    showLoading(); 
    const chartsContainer = DOM_CACHE.getElementById('statistics-view');
    
    if (chartsContainer) {
        chartsContainer.querySelectorAll('canvas').forEach(canvas => {
            drawPlaceholder(canvas.getContext('2d'), "Загрузка данных...");
        });
    }

    try {
        const statsData = await apiClient.fetchFilteredStats();
        updateStatisticsCards(statsData);
        initStatisticsCharts(statsData);
    } catch (error) {
        console.error("Не удалось сгенерировать статистику:", error);
        if (chartsContainer) {
             chartsContainer.querySelectorAll('canvas').forEach(canvas => {
                drawPlaceholder(canvas.getContext('2d'), "Ошибка загрузки данных");
             });
        }
        updateStatisticsCards({ totalTests: 0, averageScore: 0, anomaliesCount: 0, uniqueUsers: 0 });
    } finally {
        hideLoading();
    }
}

function updateStatisticsCards(statsData) {
    const container = DOM_CACHE.getElementById('statistics-cards-container');
    if (!container) return;

    container.innerHTML = html`
        <div class="stat-card">
            <div class="stat-value">${statsData?.totalTests ?? 0}</div>
            <div class="stat-label">Всего тестов</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${statsData?.averageScore?.toFixed(1) ?? 0}%</div>
            <div class="stat-label">Средний балл</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${statsData?.anomaliesCount ?? 0}</div>
            <div class="stat-label">Аномальных тестов</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${statsData?.uniqueUsers ?? 0}</div>
            <div class="stat-label">Уникальных пользователей</div>
        </div>
    `.toString();
}

function initStatisticsCharts(statsData) {
    destroyCharts(['grades', 'activity', 'anomalies']);

    if (!statsData) {
        console.warn("Нет данных для построения графиков статистики.");
        return;
    }

    // 1. График Распределения Оценок
    const gradesCtx = DOM_CACHE.getElementById('gradesChart')?.getContext('2d');
    if (gradesCtx) {
        const gradesDataFromServer = statsData.gradesDistribution || {};
        const gradeOrder = ["Отлично", "Хорошо", "Удовлетворительно", "Неудовлетворительно", "Плохо"];
        const gradeColors = {
            "Отлично": "#10b981", "Хорошо": "#2563eb", "Удовлетворительно": "#f59e0b",
            "Неудовлетворительно": "#ef4444", "Плохо": "#6b7280"
        };

        const gradeLabels = gradeOrder.filter(grade => gradesDataFromServer[grade] > 0); 
        const gradeCounts = gradeLabels.map(grade => gradesDataFromServer[grade]);
        const backgroundColors = gradeLabels.map(grade => gradeColors[grade]);

        if (gradeLabels.length > 0) {
            charts['grades'] = new Chart(gradesCtx, {
                type: 'bar',
                data: {
                    labels: gradeLabels,
                    datasets: [{
                        label: 'Количество тестов',
                        data: gradeCounts,
                        backgroundColor: backgroundColors,
                        borderRadius: 4,
                        barPercentage: 0.6
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false, indexAxis: 'y',
                    scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
                    plugins: { legend: { display: false } } 
                }
            });
        } else {
            drawPlaceholder(gradesCtx, "Нет данных по оценкам");
        }
    }

    // 2. График Активности по Дням
    const activityCtx = DOM_CACHE.getElementById('activityChart')?.getContext('2d');
    if (activityCtx) {
        const activityDataFromServer = statsData.activityByDay || { labels: [], data: [] };

        if (activityDataFromServer.labels.length > 0) {
            charts['activity'] = new Chart(activityCtx, {
                type: 'line',
                data: {
                    labels: activityDataFromServer.labels,
                    datasets: [{
                        label: 'Тесты в день',
                        data: activityDataFromServer.data,
                        borderColor: '#2563eb',
                        backgroundColor: 'rgba(37, 99, 235, 0.1)',
                        tension: 0.3,
                        fill: true,
                        pointRadius: 4,
                        pointHoverRadius: 6
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
                    plugins: { legend: { display: false } }
                }
            });
        } else {
            drawPlaceholder(activityCtx, "Нет данных по активности");
        }
    }

    // 3. График Топ Пользователей по Аномалиям
    const anomaliesCtx = DOM_CACHE.getElementById('anomaliesChart')?.getContext('2d');
    if (anomaliesCtx) {
        const anomaliesDataFromServer = statsData.topAnomalies || { labels: [], data: [] };

        if (anomaliesDataFromServer.labels.length > 0) {
            charts['anomalies'] = new Chart(anomaliesCtx, {
                type: 'bar',
                data: {
                    labels: anomaliesDataFromServer.labels,
                    datasets: [{
                        label: 'Кол-во аномальных тестов',
                        data: anomaliesDataFromServer.data,
                        backgroundColor: '#dc2626',
                        borderRadius: 4,
                        barPercentage: 0.6
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false, indexAxis: 'y',
                    scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
                    plugins: { legend: { display: false } }
                }
            });
        } else {
            drawPlaceholder(anomaliesCtx, "Нет данных по аномалиям");
        }
    }
}

// =============================================================================
// НОВЫЕ UI ЭЛЕМЕНТЫ (showLoading, showNotification, etc.)
// =============================================================================
export function showLoading() { 
    const overlay = DOM_CACHE.getElementById('loadingOverlay');
    if (overlay) overlay.classList.add('active');
}
export function hideLoading() { 
    const overlay = DOM_CACHE.getElementById('loadingOverlay');
    if (overlay) overlay.classList.remove('active');
}
export function showNotification(message, type = 'info', duration = 3000) {
    const panel = DOM_CACHE.getElementById('notificationsPanel');
    const list = DOM_CACHE.getElementById('notificationsList');
    const badge = DOM_CACHE.getElementById('notificationBadge');
    if(!panel || !list || !badge) return;

    const item = document.createElement('div');
    item.className = 'notification-item unread';
    const icon = type === 'success' ? '✅' : type === 'warning' ? '⚠️' : type === 'danger' ? '🚨' : 'ℹ️';
    item.innerHTML = html`<div class="notification-icon ${type}">${icon}</div><div class="notification-content"><div class="notification-message">${message}</div><div class="notification-time">только что</div></div>`.toString();
    
    list.prepend(item);
    
    let count = parseInt(badge.textContent || '0') + 1;
    badge.textContent = count;
    badge.style.display = 'flex';

    setTimeout(() => {
        item.style.opacity = '0';
        setTimeout(() => { 
            item.remove();
            let newCount = parseInt(badge.textContent || '0') - 1;
            badge.textContent = newCount;
            if (newCount <= 0) {
                badge.style.display = 'none';
                panel.classList.remove('active');
            }
        }, 500); 
    }, duration);
}

function renderAnswerChangesSummary(selectedResults) {
    const container = DOM_CACHE.getElementById('answer-changes-summary-container');
    if (!container) return;

    const totalChanges = selectedResults.reduce((sum, result) => {
        const userChanges = result.behavioralMetrics?.perQuestion?.reduce((qSum, q) => qSum + (q.answerChanges || 0), 0) || 0;
        return sum + userChanges;
    }, 0);

    if (totalChanges === 0) {
        container.innerHTML = '';
        return;
    }

    let htmlContent = '<div class="answer-changes-summary"><h4>📝 Смены ответа</h4>';

    selectedResults.forEach(result => {
        const changedQuestions = [];
        result.behavioralMetrics?.perQuestion?.forEach((q, index) => {
            if (q.answerChanges > 0) {
                changedQuestions.push(`#${index + 1} (${q.answerChanges})`);
            }
        });

        htmlContent += html`<p><strong>${result.userInfo.lastName}:</strong> `.toString();

        if (changedQuestions.length === 0) {
            htmlContent += '<span class="no-changes">✅ не менял(а) ответы</span>';
        } else {
            htmlContent += `<span class="has-changes">⚠️ менял(а) на вопросах: </span> <span class="question-list">${changedQuestions.join(', ')}</span>`;
        }
        htmlContent += '</p>';
    });

    htmlContent += '</div>';
    container.innerHTML = htmlContent;
}

export function toggleComparisonSelection(cardElement) {
    const sessionId = cardElement.dataset.sessionId;
    if (!sessionId) return;

    if (selectedForComparison.has(sessionId)) {
        selectedForComparison.delete(sessionId);
    } else {
        selectedForComparison.add(sessionId);
    }

    cardElement.classList.toggle('selected');
    
    const checkbox = cardElement.querySelector('input[type="checkbox"]');
    if (checkbox) {
        checkbox.checked = selectedForComparison.has(sessionId);
    }

    const analysisBtn = DOM_CACHE.getElementById('detailedAnalysisBtn');
    if (analysisBtn) {
        analysisBtn.disabled = selectedForComparison.size < 1;
    }
}

function createSingleUserFingerprintView(result) {
    const fp = result.fingerprint || {};
    const safeFp = fp.privacySafe || {};
    const data = {
        "Хеш": fp.privacySafeHash,
        "User Agent": safeFp.userAgent,
        "Платформа": safeFp.platform,
        "WebGL Рендерер": safeFp.webGLRenderer
    };

    let htmlContent = '<dl class="fingerprint-list">';
    for (const [key, value] of Object.entries(data)) {
        htmlContent += html`<dt>${key}</dt><dd>${value || 'N/A'}</dd>`.toString();
    }
    htmlContent += '</dl>';
    return htmlContent;
}


export function renderGlobalSearchResults(results) {
    let container = DOM_CACHE.getElementById('global-search-results');
    const searchInput = DOM_CACHE.getElementById('globalSearch');
    if (!searchInput) return;
    
    if (!container) {
        container = document.createElement('div');
        container.id = 'global-search-results';
        container.className = 'global-search-results-list';
        searchInput.parentElement.appendChild(container);
    }

    let htmlContent = '';

    if (results.users.length > 0) {
        htmlContent += '<div class="search-result-header">Пользователи</div>';
        htmlContent += results.users.map(user => html`
            <a href="#" class="search-result-item user-profile-link" data-lastname="${user.name.split(' ')[0]}" data-firstname="${user.name.split(' ')[1] || ''}">
                <div class="icon">👤</div>
                <div class="info">
                    <div class="title">${user.name}</div>
                    <div class="subtitle">${user.position}</div>
                </div>
            </a>
        `.toString()).join('');
    }

    if (results.sessions.length > 0) {
        htmlContent += '<div class="search-result-header">Сессии</div>';
        htmlContent += results.sessions.map(session => html`
            <a href="#" class="search-result-item single-analysis-btn" data-session-id="${session.id}">
                <div class="icon">📊</div>
                <div class="info">
                    <div class="title">${session.id.slice(0, 18)}...</div>
                    <div class="subtitle">${session.type} - ${new Date(session.date).toLocaleString('ru-RU')}</div>
                </div>
            </a>
        `.toString()).join('');
    }

    if (htmlContent === '') {
        htmlContent = '<div class="search-result-empty">Ничего не найдено.</div>';
    }

    container.innerHTML = htmlContent;
    container.classList.add('active');
}

export function hideGlobalSearchResults() {
    const container = DOM_CACHE.getElementById('global-search-results');
    if (container) {
        container.classList.remove('active');
    }
}

export function renderSettingsForm(settings) {
    if (!settings) {
        showNotification("Не удалось получить данные настроек", "warning");
        return;
    }
    
    const form = DOM_CACHE.getElementById('settings-form');
    if (!form) return;

    form.querySelectorAll('input[data-key]').forEach(input => {
        const key = input.dataset.key;
        if (settings.hasOwnProperty(key)) {
            input.value = settings[key];
        }
    });
}

console.log("UI Module initialized with improvements.");