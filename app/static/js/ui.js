/**
 * ui.js
 * Модуль для всех манипуляций с DOM. Отвечает за рендеринг, модальные окна и графики.
 * Защищен от XSS-атак.
 */

import { 
    currentPageResults, allLoadedResults, // Используем обе переменные
    settings, currentView, selectedForComparison, 
    fingerprintGroups, charts, USER_COLORS,
    setSettings, setCurrentView, totalResults, resultsPerPage, currentPage, 
    allAbandonedSessions, 
    abandonedSessionsSortKey, 
    abandonedSessionsSortDir, 
    setAbandonedSessionsSort,
    mainResultsSortKey, mainResultsSortDir, setMainResultsSort
} from './state.js';
import apiClient from './api.js';
import * as analysis from './analysis.js';

// =============================================================================
// УПРАВЛЕНИЕ ВИДАМИ (VIEW MANAGEMENT)
// =============================================================================

function updateBreadcrumbs(viewName) {
    const breadcrumbsContainer = document.getElementById('breadcrumbs');
    if (!breadcrumbsContainer) return;

    const viewTitles = {
        dashboard: "Дашборд",
        comparison: "Детальное сравнение",
        abandoned: "Прерванные сессии",
        behavior: "Поведенческий анализ",
        registry: "Реестр аттестатов",
        statistics: "Сводный отчет"
    };
    
    const currentTitle = viewTitles[viewName] || 'Аналитика';

    breadcrumbsContainer.innerHTML = `
        <a href="#" class="breadcrumb-item nav-item" data-view="dashboard">Анализ</a>
        <span class="breadcrumb-separator">/</span>
        <span class="breadcrumb-item active">${currentTitle}</span>
    `;
}

export function switchView(viewName) {
    if (currentView === viewName) return;
    setCurrentView(viewName);
    updateBreadcrumbs(viewName);

    document.querySelectorAll('.nav-item').forEach(item => 
        item.classList.toggle('active', item.dataset.view === viewName)
    );

    document.querySelectorAll('.content-area > div[id$="-view"]').forEach(div => {
        div.classList.toggle('hidden', div.id !== `${viewName}-view`);
    });

    switch (viewName) {
        case 'dashboard':
            apiClient.loadInitialData(currentPage);
            // Графики будут отрисованы автоматически через renderDashboardWidgets
            break;
        case 'comparison':
            renderComparisonUserList(Array.from(allLoadedResults.values()));
            break;
        case 'abandoned':
            apiClient.loadAndRenderAbandonedSessions();
            break;
        case 'behavior':
            apiClient.loadAndRenderBehaviorAnalysis();
            break;
        case 'registry':
            apiClient.loadAndRenderCertificates();
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
    // ВАЖНО: Пресеты теперь работают со ВСЕМИ загруженными данными, а не только с текущей страницей
    let sourceData = Array.from(allLoadedResults.values());
    let filtered = [...sourceData];
    const now = new Date();
    
    switch(presetType) {
        case 'all':
            // Для "all" мы просто используем все загруженные данные
            break;
            
        case 'today':
            // Фильтруем по сегодняшней дате
            filtered = filtered.filter(result => {
                const resultDate = new Date(result.sessionMetrics?.startTime);
                return resultDate.toDateString() === now.toDateString();
            });
            break;
            
        case 'week':
            // Фильтруем по текущей неделе
            const weekStart = new Date(now);
            weekStart.setDate(now.getDate() - now.getDay()); // Начало недели (воскресенье)
            weekStart.setHours(0, 0, 0, 0);
            
            filtered = filtered.filter(result => {
                const resultDate = new Date(result.sessionMetrics?.startTime);
                return resultDate >= weekStart;
            });
            break;
            
        case 'anomalies':
            // Фильтруем только результаты с аномалиями
            filtered = filtered.filter(result => {
                const sm = result.sessionMetrics || {};
                return (sm.totalFocusLoss > settings.focusThreshold) ||
                       (sm.totalBlurTime > settings.blurThreshold) ||
                       (sm.printAttempts > settings.printThreshold);
            });
            break;
    }
    
    // Перерисовываем только таблицу с отфильтрованными данными
    if (currentView === 'dashboard') {
        renderDataTable(filtered);
        // Пагинацию можно скрыть или показать, что это отфильтрованный вид
        const paginationContainer = document.getElementById('pagination-container');
        if (paginationContainer) {
            if (presetType !== 'all') {
                paginationContainer.innerHTML = `<div class="pagination-info">Показаны отфильтрованные результаты (${filtered.length})</div>`;
            } else {
                renderPaginationControls(); // Возвращаем обычную пагинацию
            }
        }
    }
    
    // Показываем уведомление
    const message = presetType === 'all' ? 'Показаны все данные' :
                    presetType === 'today' ? `Найдено ${filtered.length} результатов за сегодня` :
                    presetType === 'week' ? `Найдено ${filtered.length} результатов за эту неделю` :
                    `Найдено ${filtered.length} результатов с аномалиями`;
    showNotification(message, 'info', 2000);
}

export function sortAndRerenderMainResults(newSortKey) {
    let newSortDir = 'desc';

    if (mainResultsSortKey === newSortKey) {
        newSortDir = mainResultsSortDir === 'desc' ? 'asc' : 'desc';
    }
    
    setMainResultsSort(newSortKey, newSortDir);

    const dataToSort = Array.from(allLoadedResults.values());

    dataToSort.sort((a, b) => {
        const getVal = (obj, path) => path.split('.').reduce((o, i) => o?.[i], obj);
        const valA = getVal(a, newSortKey);
        const valB = getVal(b, newSortKey);

        if (newSortKey === 'startTime' || newSortKey === 'sessionMetrics.endTime') {
            return newSortDir === 'asc' ? new Date(valA) - new Date(valB) : new Date(valB) - new Date(valA);
        }
        if (typeof valA === 'number') {
            return newSortDir === 'asc' ? valA - valB : valB - valA;
        }
        return newSortDir === 'asc' 
            ? String(valA).localeCompare(String(valB)) 
            : String(valB).localeCompare(String(valA));
    });
    
    renderDataTable(dataToSort);
}
export function applyFiltersAndRender() {
    const lastName = document.getElementById('lastNameFilter').value.toLowerCase();
    const firstName = document.getElementById('firstNameFilter').value.toLowerCase();
    const fingerprint = document.getElementById('fingerprintFilter').value;

    const sourceData = Array.from(allLoadedResults.values());

    const filtered = sourceData.filter(result => {
        const ui = result.userInfo || {};
        const lastNameMatch = !lastName || (ui.lastName && ui.lastName.toLowerCase().includes(lastName));
        const firstNameMatch = !firstName || (ui.firstName && ui.firstName.toLowerCase().includes(firstName));
        const fingerprintMatch = !fingerprint || result.fingerprintHash === fingerprint;
        return lastNameMatch && firstNameMatch && fingerprintMatch;
    });
    
    if (currentView === 'dashboard') {
        renderDataTable(filtered);
    }
}

export function resetFilters() {
    document.getElementById('lastNameFilter').value = '';
    document.getElementById('firstNameFilter').value = '';
    document.getElementById('fingerprintFilter').value = '';
    document.getElementById('anomaly-reports').innerHTML = '';

    // Сбрасываем активность пресет-кнопок
    document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector('.preset-btn[data-preset="all"]')?.classList.add('active');
    
    selectedForComparison.clear();
    renderDataTable(Array.from(allLoadedResults.values())); // Показываем все загруженные данные
    renderPaginationControls();
}

// =============================================================================
// SAFE HTML HELPERS (XSS Protection)
// =============================================================================

/**
 * Escape HTML special characters to prevent XSS.
 * @param {string} unsafe - Raw string that may contain HTML
 * @returns {string} - HTML-escaped string
 */
function escapeHtml(unsafe) {
    if (typeof unsafe !== 'string' && typeof unsafe !== 'number') return '';
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

// =============================================================================
// РЕНДЕРИНГ - ДАШБОРД (НОВЫЙ ДИЗАЙН)
// =============================================================================

export function renderDashboardWidgets() {
    const container = document.getElementById('dashboard-widgets');
    if (!container) return;
    
    const resultsData = Array.from(allLoadedResults.values());
    const avgScore = resultsData.length ? Math.round(resultsData.reduce((sum, r) => sum + r.testResults.percentage, 0) / resultsData.length) : 0;
    const anomaliesCount = resultsData.filter(r => 
        (r.sessionMetrics.totalFocusLoss > settings.focusThreshold) ||
        (r.sessionMetrics.totalBlurTime > settings.blurThreshold) ||
        (r.sessionMetrics.printAttempts > settings.printThreshold)
    ).length;
    const uniqueUsers = new Set(resultsData.map(r => `${r.userInfo.lastName}${r.userInfo.firstName}`)).size;

    container.innerHTML = `
        <div class="widget">
            <div class="widget-header"><div class="widget-title">Загружено тестов</div><div class="widget-icon" style="background: rgba(37, 99, 235, 0.1); color: var(--primary);">📊</div></div>
            <div class="widget-value">${resultsData.length} <span style="font-size: 1rem; color: var(--text-light);">из ${totalResults}</span></div>
            <div class="widget-change positive"><span>↑</span><span>12.5% за неделю</span></div>
        </div>
        <div class="widget">
            <div class="widget-header"><div class="widget-title">Средний балл</div><div class="widget-icon" style="background: rgba(16, 185, 129, 0.1); color: var(--success);">📈</div></div>
            <div class="widget-value">${avgScore}%</div>
            <div class="widget-change positive"><span>↑</span><span>3.2% улучшение</span></div>
        </div>
        <div class="widget">
            <div class="widget-header"><div class="widget-title">Обнаружено аномалий</div><div class="widget-icon" style="background: rgba(239, 68, 68, 0.1); color: var(--danger);">🚨</div></div>
            <div class="widget-value">${anomaliesCount}</div>
            <div class="widget-change negative"><span>↓</span><span>8.3% снижение</span></div>
        </div>
        <div class="widget">
            <div class="widget-header"><div class="widget-title">Уникальных пользователей</div><div class="widget-icon" style="background: rgba(124, 58, 237, 0.1); color: var(--secondary);">👥</div></div>
            <div class="widget-value">${uniqueUsers}</div>
            <div class="widget-change positive"><span>↑</span><span>24 новых сегодня</span></div>
        </div>
    `;

    // Рендерим графики сразу после виджетов
    renderDashboardCharts();
}

/**
 * Renders charts on the dashboard view using data from allLoadedResults
 */
export function renderDashboardCharts() {
    // Уничтожаем старые графики, если они существуют
    if (charts['dashboardGrades']) {
        charts['dashboardGrades'].destroy();
        delete charts['dashboardGrades'];
    }
    if (charts['dashboardActivity']) {
        charts['dashboardActivity'].destroy();
        delete charts['dashboardActivity'];
    }

    const resultsArray = Array.from(allLoadedResults.values());
    
    const gradesCtx = document.getElementById('dashboardGradesChart')?.getContext('2d');
    if (gradesCtx) {
        if (resultsArray.length === 0) {
            // ... (код для случая "нет данных" остается без изменений)
            gradesCtx.clearRect(0, 0, gradesCtx.canvas.width, gradesCtx.canvas.height);
            gradesCtx.font = "16px Arial";
            gradesCtx.fillStyle = "var(--text-light)";
            gradesCtx.textAlign = "center";
            gradesCtx.fillText("Нет данных для отображения", gradesCtx.canvas.width/2, gradesCtx.canvas.height/2);
            return;
        }
        
        const gradesCounts = resultsArray.reduce((acc, r) => {
            const gradeText = r.testResults.grade.text; // Теперь здесь "Отлично", "Хорошо" и т.д.
            acc[gradeText] = (acc[gradeText] || 0) + 1;
            return acc;
        }, {});

        const gradeLabels = Object.keys(gradesCounts);
        const gradeData = Object.values(gradesCounts);
        
        // НОВОЕ: Более яркие и согласованные цвета
        const gradeColors = {
            'Отлично': 'hsla(145, 63%, 42%, 1)',
            'Хорошо': 'hsla(221, 83%, 53%, 1)',
            'Удовлетворительно': 'hsla(39, 92%, 56%, 1)',
            'Неудовлетворительно': 'hsla(0, 84%, 60%, 1)',
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
                    // НОВОЕ: Стили для красоты
                    borderColor: '#fff',
                    borderWidth: 3,
                    borderRadius: 8, // Скругляет углы сегментов
                    hoverOffset: 15    // Увеличивает сегмент при наведении
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '70%', // Делает "бублик" тоньше
                plugins: {
                    // НОВОЕ: Добавляем заголовок прямо в график
                    title: {
                        display: true,
                        text: 'Соотношение оценок',
                        padding: {
                            top: 10,
                            bottom: 10
                        },
                        font: {
                            size: 16,
                            weight: '600'
                        },
                        color: 'var(--text)'
                    },
                    legend: {
                        position: 'right', // Легенда справа выглядит лучше
                        labels: {
                            padding: 20,
                            font: { size: 14 },
                            color: 'var(--text-light)',
                            usePointStyle: true, // Используем кружки вместо квадратов
                            pointStyle: 'circle'
                        }
                    },
                    tooltip: {
                        // НОВОЕ: Улучшаем внешний вид подсказок
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

    // 2. График активности по времени
    const activityCtx = document.getElementById('dashboardActivityChart')?.getContext('2d');
    if (activityCtx) {
         if (resultsArray.length === 0) {
            activityCtx.clearRect(0, 0, activityCtx.canvas.width, activityCtx.canvas.height);
            activityCtx.font = "16px Arial";
            activityCtx.fillStyle = "var(--text-light)";
            activityCtx.textAlign = "center";
            activityCtx.fillText("Нет данных для отображения", activityCtx.canvas.width/2, activityCtx.canvas.height/2);
            return;
        }

        const dailyActivity = resultsArray.reduce((acc, r) => {
            const date = new Date(r.sessionMetrics.startTime).toLocaleDateString('ru-RU');
            acc[date] = (acc[date] || 0) + 1;
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
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            stepSize: 1,
                            precision: 0
                        },
                        grid: {
                            color: 'rgba(0, 0, 0, 0.05)'
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        },
                        ticks: {
                            maxRotation: 45,
                            minRotation: 45
                        }
                    }
                },
                interaction: {
                    mode: 'nearest',
                    axis: 'x',
                    intersect: false
                }
            }
        });
    }
}

export function renderDataTable(results) {
    const container = document.getElementById('results-container');
    if (!container) return;

    const createHeader = (label, sortKey) => {
        const isSorted = mainResultsSortKey === sortKey;
        const icon = isSorted ? (mainResultsSortDir === 'desc' ? '▼' : '▲') : '';
        return `<th data-sort="${sortKey}">${label} <span class="sort-icon">${icon}</span></th>`;
    };

    const tableRows = results.length > 0 
        ? results.map(createTableRowHTML).join('') 
        : '<tr><td colspan="9" class="loading">Результаты не найдены.</td></tr>';

    container.innerHTML = `
        <div class="table-wrapper">
            <table class="data-table">
                <thead>
                    <tr>
                        <th style="width: 50px;"><input type="checkbox" id="selectAllRows"></th>
                        ${createHeader('Пользователь', 'userInfo.lastName')}
                        ${createHeader('Тест', 'testType')}
                        ${createHeader('Дата', 'sessionMetrics.startTime')}
                        ${createHeader('IP Адрес', 'clientIp')}
                        ${createHeader('Результат', 'testResults.percentage')}
                        <th>Время</th>
                        <th>Аномалии</th>
                        <th style="text-align: center;">Действия</th>
                    </tr>
                </thead>
                <tbody id="results-table-body">
                    ${tableRows}
                </tbody>
            </table>
        </div>
    `;
}

function createTableRowHTML(result) {
    const ui = result.userInfo || {};
    const tr = result.testResults || {};
    const sm = result.sessionMetrics || {};
    
    const totalAnomalies = (sm.totalFocusLoss > settings.focusThreshold ? 1 : 0) + (sm.totalBlurTime > settings.blurThreshold ? 1 : 0) + (sm.printAttempts > settings.printThreshold ? 1 : 0);
    let anomalyLevel = 'Низкий', anomalyLevelClass = 'low', anomalyWidth = 0;
    if (totalAnomalies === 1) { anomalyWidth = 33; }
    if (totalAnomalies === 2) { anomalyWidth = 66; anomalyLevel = 'Средний'; anomalyLevelClass = 'medium'; }
    if (totalAnomalies >= 3) { anomalyWidth = 100; anomalyLevel = 'Высокий'; anomalyLevelClass = 'high'; }

    const lastName = ui.lastName || 'N/A';
    const firstName = ui.firstName || '';
    const initials = `${(lastName[0] || '')}${(firstName[0] || '')}`.toUpperCase();
    const duration = sm.endTime && sm.startTime ? `${Math.round((new Date(sm.endTime) - new Date(sm.startTime)) / 1000 / 60)} мин` : 'N/A';
    
    return `
    <tr>
        <td><input type="checkbox" class="row-checkbox" data-session-id="${escapeHtml(result.sessionId)}"></td>
        <td>
            <div class="user-cell">
                <div class="user-avatar-small">${createSafeText(initials)}</div>
                <div>
                    <a href="#" class="user-profile-link" data-lastname="${escapeHtml(lastName)}" data-firstname="${escapeHtml(firstName)}"><strong>${createSafeText(lastName)}</strong> ${createSafeText(firstName)}</a>
                </div>
            </div>
        </td>
        <td>${createSafeText(result.testType)}</td>
        <td>${new Date(sm.startTime).toLocaleString('ru-RU')}</td>
        
        <td>${createSafeText(result.clientIp || 'N/A')}</td>

        <td><span class="status-badge grade-${tr.grade?.class}">${tr.percentage}%</span></td>
        <td>${duration}</td>
        <td>
            <div class="anomaly-indicator">
                <div class="anomaly-level"><div class="anomaly-level-fill ${anomalyLevelClass}" style="width: ${anomalyWidth}%;"></div></div>
                <span style="font-size: 0.85rem;">${anomalyLevel}</span>
            </div>
        </td>
        <td class="cell-actions">
            <div class="action-buttons">
                <button class="action-btn event-log-link tooltip" data-session-id="${escapeHtml(result.sessionId)}"><span class="tooltip-content">Журнал</span>👁️</button>
                <button class="action-btn single-analysis-btn tooltip" data-session-id="${escapeHtml(result.sessionId)}"><span class="tooltip-content">Анализ</span>📊</button>

            </div>
        </td>
    </tr>`;
}

export function renderPaginationControls() {
    const container = document.getElementById('pagination-container');
    if (!container) return;

    // Скрываем пагинацию, если загружены все результаты
    if (resultsPerPage >= totalResults && totalResults > 0) {
        container.innerHTML = `<div class="pagination-info">Показаны все ${totalResults} записей</div>`;
        return;
    }
    
    if (totalResults <= resultsPerPage) {
        container.innerHTML = '';
        return;
    }

    const totalPages = Math.ceil(totalResults / resultsPerPage);
    
    let pagesHtml = '';
    const pagesToShow = new Set();
    pagesToShow.add(1);
    pagesToShow.add(totalPages);
    for (let i = -2; i <= 2; i++) {
        const p = currentPage + i;
        if (p > 1 && p < totalPages) pagesToShow.add(p);
    }
    
    const sortedPages = Array.from(pagesToShow).sort((a,b)=>a-b);
    let lastPage = 0;
    sortedPages.forEach(p => {
        if(lastPage > 0 && p > lastPage + 1) {
            pagesHtml += `<button class="page-btn ellipsis" disabled>...</button>`;
        }
        pagesHtml += `<button class="page-btn ${currentPage === p ? 'active' : ''}" data-page="${p}">${p}</button>`;
        lastPage = p;
    });

    const startItem = (currentPage - 1) * resultsPerPage + 1;
    const endItem = Math.min(startItem + resultsPerPage - 1, totalResults);

    container.innerHTML = `
        <div class="pagination">
            <div class="pagination-info">Показано ${startItem} - ${endItem} из ${totalResults}</div>
            <div class="pagination-controls">
                <button class="page-btn" ${currentPage === 1 ? 'disabled' : ''} data-page="${currentPage - 1}">‹ Пред.</button>
                ${pagesHtml}
                <button class="page-btn" ${currentPage === totalPages ? 'disabled' : ''} data-page="${currentPage + 1}">След. ›</button>
            </div>
        </div>
    `;
}

// =============================================================================
// ОТЧЕТЫ ОБ АНОМАЛИЯХ И ФИЛЬТР FINGERPRINT
// =============================================================================

export function displayAnomalyReport(type) {
    const container = document.getElementById('anomaly-reports');
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
        const anomalies = currentPageResults.filter(r => 
            (r.sessionMetrics.totalFocusLoss > settings.focusThreshold) ||
            (r.sessionMetrics.totalBlurTime > settings.blurThreshold) ||
            (r.sessionMetrics.printAttempts > settings.printThreshold)
        );
        if (anomalies.length > 0) {
            anomalies.forEach(r => {
                let details = [];
                if (r.sessionMetrics.totalFocusLoss > settings.focusThreshold) details.push(`потери фокуса: ${r.sessionMetrics.totalFocusLoss}`);
                if (r.sessionMetrics.totalBlurTime > settings.blurThreshold) details.push(`время вне фокуса: ${r.sessionMetrics.totalBlurTime}с`);
                if (r.sessionMetrics.printAttempts > settings.printThreshold) details.push(`попытки печати: ${r.sessionMetrics.printAttempts}`);
                report.details.push(`<b>${createSafeText(r.userInfo.lastName)} ${createSafeText(r.userInfo.firstName)}</b>: ${details.join(', ')}`);
            });
        } else {
            report.details.push("Нарушений не найдено.");
        }
    }
    const detailsHTML = `<ul>${report.details.map(d => `<li>${d}</li>`).join('')}</ul>`;
    container.innerHTML = `<div class="anomaly-card ${report.severity}"><div class="anomaly-header"><div class="anomaly-icon ${report.severity}">!</div><h4>${report.title}</h4></div>${detailsHTML}</div>`;
}

export function populateFingerprintFilter() {
    const select = document.getElementById('fingerprintFilter');
    select.innerHTML = '<option value="">Все группы</option>';
    Object.entries(fingerprintGroups).filter(([_, group]) => group.results.length > 1).sort().forEach(([hash, group]) => {
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
    document.getElementById('focusThreshold').value = settings.focusThreshold;
    document.getElementById('blurThreshold').value = settings.blurThreshold;
    document.getElementById('mouseThreshold').value = settings.mouseThreshold;
    document.getElementById('printThreshold').value = settings.printThreshold;
    document.getElementById('ipFingerprintCheck').checked = settings.checkIpInFingerprint;
    document.getElementById('settingsModal').style.display = 'flex';
}

export function closeSettings() {
    document.getElementById('settingsModal').style.display = 'none';
}

export function saveSettings() {
    const newSettings = {
        focusThreshold: parseInt(document.getElementById('focusThreshold').value),
        blurThreshold: parseInt(document.getElementById('blurThreshold').value),
        mouseThreshold: parseInt(document.getElementById('mouseThreshold').value),
        printThreshold: parseInt(document.getElementById('printThreshold').value),
        checkIpInFingerprint: document.getElementById('ipFingerprintCheck').checked,
    };
    setSettings(newSettings);
    localStorage.setItem('analysisSettings', JSON.stringify(newSettings));
    closeSettings();
}

export function openExportModal() {
    const modal = document.getElementById('exportModal');
    if (!modal) return;
    
    // Сбрасываем выбор формата
    document.querySelectorAll('.export-option').forEach(opt => opt.classList.remove('selected'));
    
    modal.style.display = 'flex';
}

export function closeExportModal() {
    const modal = document.getElementById('exportModal');
    if (modal) modal.style.display = 'none';
}

// Обработка выбора формата экспорта
document.body.addEventListener('click', (e) => {
    if (e.target.closest('.export-option')) {
        const option = e.target.closest('.export-option');
        document.querySelectorAll('.export-option').forEach(opt => opt.classList.remove('selected'));
        option.classList.add('selected');
    }
    
    if (e.target.closest('#exportSelectedToggle')) {
        const toggle = e.target.closest('#exportSelectedToggle');
        toggle.classList.toggle('active');
    }
    
    if (e.target.closest('#closeExportModalBtn') || e.target.closest('#cancelExportBtn')) {
        closeExportModal();
    }
    
    if (e.target.closest('#executeExportBtn')) {
        executeExport();
    }
});

function executeExport() {
    const selectedOption = document.querySelector('.export-option.selected');
    if (!selectedOption) {
        showNotification('Выберите формат экспорта', 'warning');
        return;
    }
    
    const format = selectedOption.dataset.format;
    const onlySelected = document.getElementById('exportSelectedToggle')?.classList.contains('active') || false;
    
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
        .filter(r => r.userInfo.lastName === lastName && r.userInfo.firstName === firstName)
        .sort((a, b) => new Date(b.sessionMetrics.startTime) - new Date(a.sessionMetrics.startTime));
    if (userTests.length === 0) {
        showNotification("Не найдено тестов для этого пользователя.", "warning");
        return;
    }
    
    const fullName = createSafeText(`${lastName} ${firstName}`);
    document.getElementById('profileTitle').textContent = `👤 Профиль: ${lastName} ${firstName}`;
    document.getElementById('profileContent').innerHTML = generateUserProfileContent(userTests);
    document.getElementById('userProfileModal').style.display = 'flex';
}

export function closeUserProfile() {
     document.getElementById('userProfileModal').style.display = 'none';
}

function generateUserProfileContent(userTests) {
    const latestTest = userTests[0];
    return `<div class="stats-overview" style="margin-bottom: 1.5rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));">
            <div class="stat-card"><div class="stat-value">${userTests.length}</div><div class="stat-label">Всего попыток</div></div>
            <div class="stat-card"><div class="stat-value">${latestTest.testResults.percentage}%</div><div class="stat-label">Последний результат</div></div>
            <div class="stat-card"><div class="stat-value">${Math.max(...userTests.map(t => t.testResults.percentage))}%</div><div class="stat-label">Лучший результат</div></div>
        </div>
        <div class="table-wrapper"><table class="data-table"><thead><tr><th>Дата</th><th>Результат</th><th>Оценка</th><th>Аномалии</th><th>Действия</th></tr></thead><tbody>${userTests.map(test => { const hasAnomalies = (test.sessionMetrics.totalFocusLoss > settings.focusThreshold || test.sessionMetrics.totalBlurTime > settings.blurThreshold || test.sessionMetrics.printAttempts > settings.printThreshold); return `<tr><td>${new Date(test.sessionMetrics.startTime).toLocaleString('ru-RU')}</td><td>${test.testResults.percentage}%</td><td><span class="status-badge grade-${test.testResults.grade.class}">${createSafeText(test.testResults.grade.text)}</span></td><td>${hasAnomalies ? '⚠️ Да' : '✅ Нет'}</td><td><button class="action-btn event-log-link" data-session-id="${escapeHtml(test.sessionId)}">👁️</button></td></tr>`; }).join('')}</tbody></table></div>`;
}

export function openEventLogModal(sessionId) {
    const modal = document.getElementById('eventLogModal');
    document.getElementById('eventLogTitle').textContent = `📜 Журнал событий (${createSafeText(sessionId).slice(0, 8)}...)`;
    modal.style.display = 'flex';
}

export function closeEventLog() {
    document.getElementById('eventLogModal').style.display = 'none';
}

function renderTestLog(events) {
    const content = document.getElementById('eventLogContent');
    
    events.sort((a, b) => new Date(a.event_timestamp) - new Date(b.event_timestamp));

    const uniqueIPs = [...new Set(events.map(e => {
        try { return JSON.parse(e.details || '{}').ip; } catch { return null; }
    }).filter(Boolean))];
    
    const titleEl = document.getElementById('eventLogTitle');
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
    const content = document.getElementById('eventLogContent');
    events.sort((a, b) => new Date(a.event_timestamp) - new Date(b.event_timestamp));

    const uniqueIPs = [...new Set(events.map(e => {
        try { return JSON.parse(e.details || '{}').ip; } catch { return null; }
    }).filter(Boolean))];
    
    const titleEl = document.getElementById('eventLogTitle');
    if (titleEl && uniqueIPs.length === 1) {
        titleEl.innerHTML += ` <span class="ip-address">(${uniqueIPs[0]})</span>`;
    }
    
    const startTime = new Date(events[0].event_timestamp);
    const lastEventTime = new Date(events[events.length - 1].event_timestamp);
    const totalSessionTime = Math.round((lastEventTime - startTime) / 1000);
    const totalActiveTime = events.filter(e => e.event_type === 'module_view_time').reduce((sum, e) => { try { return sum + (JSON.parse(e.details || '{}').duration || 0); } catch { return sum; } }, 0);
    const maxScrollDepth = Math.max(0, ...events.filter(e => e.event_type === 'scroll_depth_milestone').map(e => { try { return parseInt(JSON.parse(e.details || '{}').depth) || 0; } catch { return 0; } }));

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
        const content = document.getElementById('eventLogContent');
        content.innerHTML = '<p style="text-align: center; color: var(--text-light);">Для этой сессии не зафиксировано ни одного события.</p>';
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

function getTitleForEvent(event, isStudy) {
    let details = {};
    try { details = JSON.parse(event.details || '{}'); } catch {}
    
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

function getDetailsForEvent(event, isAnomaly, uniqueIPs) {
    let details = {};
    try { details = JSON.parse(event.details || '{}'); } catch {}
    
    let detailsHtml = '';
    if (isAnomaly) {
        detailsHtml += '<p class="anomaly-warning">⚠️ <strong>Действие совершено после завершения теста!</strong></p>';
    }

    switch (event.event_type) {
        case 'test_started':
        case 'study_started':
            const user = details.userInfo || {};
            detailsHtml += `<p><strong>Пользователь:</strong> ${escapeHtml(user.lastName)} ${escapeHtml(user.firstName)}</p>`;
            if (user.position) detailsHtml += `<p><strong>Должность:</strong> ${escapeHtml(user.position)}</p>`;
            break;
        case 'focus_loss':
        case 'print_attempt':
        case 'screenshot_attempt':
            detailsHtml += `<p>На <strong>вопросе №${escapeHtml(details.question || '?')}</strong></p>`;
            break;
        case 'module_view_time':
            detailsHtml += `<p><strong>Продолжительность:</strong> ${escapeHtml(details.duration || 0)} секунд</p>`;
            break;
    }
    
    if (details.ip && uniqueIPs.length > 1) {
        detailsHtml += `<p class="ip-address">IP: ${escapeHtml(details.ip)}</p>`;
    }
    return detailsHtml;
}

// =============================================================================
// РЕНДЕРИНГ - ДЕТАЛЬНОЕ СРАВНЕНИЕ
// =============================================================================

export function renderComparisonUserList(results) {
    const listContainer = document.getElementById('comparison-user-list');
    if (!listContainer) return;
    listContainer.innerHTML = results.map(result => {
        const isSelected = selectedForComparison.has(result.sessionId);
        const ui = result.userInfo || {};
        return `<div class="comparison-list-card ${isSelected ? 'selected' : ''}" data-session-id="${escapeHtml(result.sessionId)}">
            <input type="checkbox" ${isSelected ? 'checked' : ''} readOnly>
            <div class="info"><h4>${createSafeText(ui.lastName)} ${createSafeText(ui.firstName)}</h4><p>${new Date(result.sessionMetrics.startTime).toLocaleString('ru-RU')} (${createSafeText(result.testType)})</p></div>
            <div class="score grade-${result.testResults.grade?.class}" style="margin-left: auto;">${result.testResults.percentage}%</div>
            </div>`;
    }).join('');
}

export function renderComparisonResults(analysisResults, selectedResults) {
    const container = document.getElementById('comparison-results-panel');

    Object.values(charts).forEach(chart => {
        if (chart && typeof chart.destroy === 'function') chart.destroy();
    });
    Object.keys(charts).forEach(key => delete charts[key]);

    container.innerHTML = createDetailedAnalysisHTML(selectedResults);

    renderViolationsSummary(selectedResults);
    renderComparisonCharts(selectedResults);
    renderDtwResults(analysisResults, selectedResults);

    // НОВЫЙ ВЫЗОВ:
    renderAnswerChangesSummary(selectedResults);
}

function createDetailedAnalysisHTML(results) {
    const names = results.map(r => `${r.userInfo.lastName} ${r.userInfo.firstName}`).join(' vs ');
    const title = results.length > 1 ? `Детальное сравнение: ${createSafeText(names)}` : `Одиночный анализ: ${createSafeText(names)}`;

    return `<h3>${title}</h3>
        <div class="analysis-section">
            <h3>Сравнение отпечатков (Fingerprint)</h3>
            <div class="analysis-content">${createFingerprintTable(results)}</div>
        </div>

        <div class="analysis-section">
            <h3>🚨 Анализ нарушений</h3>
            <div class="analysis-content" id="violations-summary-container">
                </div>
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
        </div>`;
}

function renderDtwResults(dtwResults, selectedResults) { // Добавлен аргумент selectedResults
    const container = document.getElementById('dtw-analysis-results'); 
    if (!container) return;
    let html = '';
    if (!dtwResults || Object.keys(dtwResults).length === 0) {
        container.innerHTML = '<p>Нет данных для DTW анализа.</p>';
        return;
    }
    Object.entries(dtwResults).forEach(([pairKey, scores]) => {
        const questionScores = Object.values(scores);
        if (questionScores.length === 0) return;
        const avgSim = questionScores.reduce((a, b) => a + b, 0) / questionScores.length;
        const highSim = Object.entries(scores).filter(([, s]) => s >= 70).sort(([,a],[,b])=>b-a);
        const [sid1, sid2] = pairKey.split('_vs_');

        // ИЗМЕНЕНИЕ: Ищем пользователей в переданных полных данных
        const user1 = selectedResults.find(r => r.sessionId === sid1)?.userInfo;
        const user2 = selectedResults.find(r => r.sessionId === sid2)?.userInfo;

        // Если пользователи не найдены, пропускаем
        if (!user1 || !user2) return;

        const isAnomalous = highSim.some(([,s])=>s>=settings.mouseThreshold);
        
        html += `<div class="dtw-result-card" style="border-left-color: ${isAnomalous ? 'var(--danger)' : 'var(--border)'}">
            <h4>${createSafeText(user1.lastName)} vs ${createSafeText(user2.lastName)}</h4>
            <p>Среднее сходство: <b>${avgSim.toFixed(1)}%</b>. Подозрительных ответов (>70%): ${highSim.length}</p>
            ${highSim.length > 0 ? `<details><summary>Детали</summary><ul>${highSim.map(([q, s]) => `<li>Вопрос #${parseInt(q) + 1}: <b style="color:${s >= settings.mouseThreshold ? 'var(--danger)' : 'inherit'}">${s}%</b></li>`).join('')}</ul></details>` : ''}
        </div>`;
    });
    container.innerHTML = html || '<p>Нет общих вопросов для сравнения.</p>';
}

function renderComparisonCharts(results) {
    const labels = results.map(r => `${r.userInfo.lastName} ${r.userInfo.firstName.charAt(0)}.`);
    const colors = results.map((_, i) => USER_COLORS[i % USER_COLORS.length]);

    // УДАЛЕНО: Три вызова createBarChart для нарушений

    const numQuestions = Math.max(0, ...results.map(r => r.behavioralMetrics?.perQuestion?.length || 0));
    if (numQuestions > 0) {
        const qLabels = Array.from({ length: numQuestions }, (_, i) => `В${i + 1}`);
        const latencyDS = results.map((r, i) => ({ label: labels[i], data: r.behavioralMetrics?.perQuestion?.map(q => q?.latency || 0) || [], borderColor: colors[i], tension: 0.1, fill: false }));
        createLineChart('latencyChart', '', qLabels, latencyDS);

        // --- ЭТА СТРОКА УДАЛЯЕТСЯ ---
        // const changesDS = results.map((r, i) => ({ label: labels[i], data: r.behavioralMetrics?.perQuestion?.map(q => q?.answerChanges || 0) || [], backgroundColor: colors[i] }));
        // createBarChart('answerChangesChart', 'кол-во', qLabels, changesDS, true);

        setupMouseVisualizer(results);
    }
}
// Вставьте эту новую функцию в ui.js
/**
 * Renders a compact summary of violations instead of large charts.
 * @param {Array<object>} selectedResults - Array of full result objects for comparison.
 */
function renderViolationsSummary(selectedResults) {
    const container = document.getElementById('violations-summary-container');
    if (!container) return;

    // Считаем общее количество нарушений
    const totalViolations = selectedResults.reduce((sum, result) => {
        const sm = result.sessionMetrics;
        return sum + (sm?.totalFocusLoss || 0) + (sm?.totalBlurTime || 0) + (sm?.printAttempts || 0) + (sm?.screenshotAttempts || 0);
    }, 0);

    // Если нарушений нет, показываем заглушку и выходим
    if (totalViolations === 0) {
        container.innerHTML = `
            <div class="no-violations-placeholder">
                <div class="no-violations-placeholder-icon">✅</div>
                <p class="no-violations-placeholder-text">Нарушений не зафиксировано</p>
            </div>
        `;
        return;
    }

    // Если нарушения есть, строим карточки
    let html = '<div class="violations-summary-grid">';
    selectedResults.forEach(result => {
        const sm = result.sessionMetrics || { totalFocusLoss: 0, totalBlurTime: 0, printAttempts: 0, screenshotAttempts: 0 };
        const printAndScreen = (sm.printAttempts || 0) + (sm.screenshotAttempts || 0);

        html += `
            <div class="violation-user-column">
                <h4>${createSafeText(result.userInfo.lastName)} ${createSafeText(result.userInfo.firstName)}</h4>
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
        `;
    });
    html += '</div>';
    container.innerHTML = html;
}
function createFingerprintTable(results) {
    // ИЗМЕНЕНИЕ: Если пользователь один, вызываем новую функцию
    if (results.length === 1) {
        return createSingleUserFingerprintView(results[0]);
    }

    // Старая логика для сравнения нескольких пользователей
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
    const selector = document.getElementById('questionSelector');
    if (!selector) return;
    selector.innerHTML = '';
    const numQuestions = Math.max(0, ...results.map(r => r.behavioralMetrics?.perQuestion?.length || 0));
    for (let i = 0; i < numQuestions; i++) selector.add(new Option(`Вопрос ${i + 1}`, i));
    const drawFunc = () => drawMouseTrajectory(results, selector.value);
    selector.addEventListener('change', drawFunc);
    drawFunc();
}

function drawMouseTrajectory(results, qIndex) {
    const canvas = document.getElementById('mouseTrajectoryCanvas');
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

export function sortAndRerenderAbandoned(newSortKey) {
    const currentFilter = document.querySelector('#abandoned-filters .filter-btn.active')?.dataset.filter || 'all';
    let newSortDir = 'desc';

    if (abandonedSessionsSortKey === newSortKey) {
        newSortDir = abandonedSessionsSortDir === 'desc' ? 'asc' : 'desc';
    }
    
    setAbandonedSessionsSort(newSortKey, newSortDir);

    allAbandonedSessions.sort((a, b) => {
        const getVal = (obj, path) => path.split('.').reduce((o, i) => o?.[i], obj);
        const valA = getVal(a, newSortKey);
        const valB = getVal(b, newSortKey);

        if (typeof valA === 'number') {
            return newSortDir === 'asc' ? valA - valB : valB - valA;
        }
        if (newSortKey === 'startTime') {
            return newSortDir === 'asc' ? new Date(valA) - new Date(valB) : new Date(valB) - new Date(valA);
        }
        return newSortDir === 'asc' 
            ? String(valA).localeCompare(String(valB)) 
            : String(valB).localeCompare(String(valA));
    });
    
    renderAbandonedSessions(currentFilter);
}

export function renderAbandonedSessions(filter = 'all') {
    const container = document.getElementById('abandoned-sessions-container');
    if (!container) return;

    console.log("Данные для прерванных сессий:", allAbandonedSessions);

    const sessionsToRender = (filter === 'all') 
        ? allAbandonedSessions 
        : allAbandonedSessions.filter(s => s.sessionType === filter);

    if (sessionsToRender.length === 0) {
        container.innerHTML = '<p style="text-align:center; color: var(--text-light);">Прерванных сессий такого типа не найдено.</p>';
        return;
    }

    const createHeader = (label, sortKey) => {
        const isSorted = abandonedSessionsSortKey === sortKey;
        const icon = isSorted ? (abandonedSessionsSortDir === 'desc' ? '▼' : '▲') : '';
        return `<th data-sort="${sortKey}">${label} <span class="sort-icon">${icon}</span></th>`;
    };

    const tableRows = sessionsToRender.map(session => {
        const ui = session.userInfo || {};
        const counts = session.violationCounts || {};
        
        const userName = `<strong>${escapeHtml(ui.lastName || '')}</strong> ${escapeHtml(ui.firstName || 'N/A')}`;
        const sessionType = session.sessionType || 'unknown';
        const sessionIcon = sessionType === 'test' ? '📝' : '📚';
        const sessionText = sessionType === 'test' ? 'Тест' : 'Обучение';
        const startTime = new Date(session.startTime).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        const ipDisplay = `IP: [${escapeHtml(session.clientIp || 'N/A')}]`;

        return `
            <tr>
                <td>${userName}</td>
                <td class="cell-type"><span title="${sessionText}">${sessionIcon}</span> ${sessionText}</td>
                <td>${startTime}</td>
                <td>${ipDisplay}</td>
                <td class="numeric">${counts.focusLoss || 0}</td>
                <td class="numeric">${counts.screenshots || 0}</td>
                <td class="numeric">${counts.prints || 0}</td>
                <td class="cell-actions">
                    <button class="action-btn event-log-link" data-session-id="${session.sessionId}" title="Журнал событий">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16"><path d="M8 16A8 8 0 1 0 8 0a8 8 0 0 0 0 16zm.93-9.412-1 4.705c-.07.34.029.533.304.533.194 0 .487-.07.686-.246l1.06-1.06c.296-.296.026-.756-.352-1.012l-1.895-1.127a1.002 1.002 0 0 1-.252-.422l-1.08-3.232c-.07-.21.02-.43.25-.504.228-.074.457.022.533.246l1.08 3.232zM8 5.5a1 1 0 1 0 0-2 1 1 0 0 0 0 2z"/></svg>
                    </button>
                </td>
            </tr>
        `;
    }).join('');

    container.innerHTML = `
        <div class="table-wrapper">
            <table class="data-table">
                <thead>
                    <tr>
                        ${createHeader('Пользователь', 'userInfo.lastName')}
                        ${createHeader('Тип', 'sessionType')}
                        ${createHeader('Время начала', 'startTime')}
                        ${createHeader('IP Адрес', 'clientIp')}
                        ${createHeader('Потери фокуса', 'violationCounts.focusLoss')}
                        ${createHeader('Скриншоты', 'violationCounts.screenshots')}
                        ${createHeader('Попытки печати', 'violationCounts.prints')}
                        <th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    ${tableRows}
                </tbody>
            </table>
        </div>
    `;
}

export function renderBehaviorAnalysis(sessions) {
    const container = document.getElementById('behavior-analysis-container');
    if (sessions.length === 0) {
        container.innerHTML = '<p>Подозрительных сессий не найдено.</p>';
        return;
    }
    container.innerHTML = sessions.map(s => `<div class="behavior-card"><h4>${createSafeText(s.userInfo.lastName)} ${createSafeText(s.userInfo.firstName)}</h4><p>${createSafeText(s.reason)}</p></div>`).join('');
}

export function renderCertificatesTable(certificates) {
    const container = document.getElementById('registry-container');
    if (certificates.length === 0) {
        container.innerHTML = '<p>Аттестатов не найдено.</p>';
        return;
    }
    const tableRows = certificates.map(c => `<tr><td>${createSafeText(c.document_number)}</td><td>${createSafeText(c.user_fullname)}</td><td>${createSafeText(c.test_type)}</td><td>${new Date(c.issue_date).toLocaleDateString('ru-RU')}</td><td>${c.score_percentage}%</td></tr>`).join('');
    container.innerHTML = `<table class="comparison-table"><thead><tr><th>Номер</th><th>ФИО</th><th>Тест</th><th>Дата</th><th>Результат</th></tr></thead><tbody>${tableRows}</tbody></table>`;
}

// =============================================================================
// СТАТИСТИКА
// =============================================================================

export function generateStatistics() {
    updateStatisticsCards();
    initStatisticsCharts();
}

function updateStatisticsCards() {
    const resultsArray = Array.from(allLoadedResults.values());

    if (!resultsArray.length) return;

    document.getElementById('totalTests').textContent = totalResults;
    
    const avgScore = resultsArray.reduce((sum, r) => sum + r.testResults.percentage, 0) / resultsArray.length;
    document.getElementById('averageScore').textContent = `${Math.round(avgScore)}%`;
    
    const anomaliesCount = resultsArray.filter(r => (r.sessionMetrics.totalFocusLoss > settings.focusThreshold) || (r.sessionMetrics.totalBlurTime > settings.blurThreshold) || (r.sessionMetrics.printAttempts > settings.printThreshold)).length;
    document.getElementById('anomaliesCount').textContent = anomaliesCount;
    
    const uniqueUsers = new Set(resultsArray.map(r => `${r.userInfo.lastName} ${r.userInfo.firstName}`)).size;
    document.getElementById('uniqueUsers').textContent = uniqueUsers;
}

function initStatisticsCharts() {
    Object.values(charts).forEach(chart => chart.destroy());

    const resultsArray = Array.from(allLoadedResults.values());

    const gradesCtx = document.getElementById('gradesChart')?.getContext('2d');
    if (gradesCtx) {
        const gradesCounts = resultsArray.reduce((acc, r) => { acc[r.testResults.grade.text] = (acc[r.testResults.grade.text] || 0) + 1; return acc; }, {});
        charts['grades'] = new Chart(gradesCtx, { type: 'bar', data: { labels: Object.keys(gradesCounts), datasets: [{ label: 'Количество', data: Object.values(gradesCounts), backgroundColor: ['#10b981', '#2563eb', '#f59e0b', '#ef4444', '#6b7280'] }] } });
    }
    const activityCtx = document.getElementById('activityChart')?.getContext('2d');
    if (activityCtx) {
        const dailyActivity = resultsArray.reduce((acc, r) => { const date = new Date(r.sessionMetrics.startTime).toLocaleDateString('ru-RU'); acc[date] = (acc[date] || 0) + 1; return acc; }, {});
        const sortedDates = Object.keys(dailyActivity).sort((a,b)=>new Date(a.split('.').reverse().join('-'))-new Date(b.split('.').reverse().join('-')));
        charts['activity'] = new Chart(activityCtx, { type: 'line', data: { labels: sortedDates, datasets: [{ label: 'Тесты в день', data: sortedDates.map(d=>dailyActivity[d]), borderColor: '#2563eb', tension: 0.1 }] } });
    }
    const anomaliesCtx = document.getElementById('anomaliesChart')?.getContext('d');
    if (anomaliesCtx) {
        const userAnomalies = resultsArray.reduce((acc, r) => {
            const count = (r.sessionMetrics.totalFocusLoss > settings.focusThreshold) + (r.sessionMetrics.totalBlurTime > settings.blurThreshold) + (r.sessionMetrics.printAttempts > settings.printThreshold);
            if (count > 0) { const name = `${r.userInfo.lastName} ${r.userInfo.firstName}`; acc[name] = (acc[name] || 0) + count; } return acc;
        }, {});
        const topUsers = Object.entries(userAnomalies).sort(([, a], [, b]) => b - a).slice(0, 5);
        charts['anomalies'] = new Chart(anomaliesCtx, { type: 'bar', data: { labels: topUsers.map(([name]) => name), datasets: [{ label: 'Кол-во аномалий', data: topUsers.map(([, count]) => count), backgroundColor: '#dc2626' }] } });
    }
}

// =============================================================================
// ГРАФИКИ (Chart.js Helpers) - IMPROVED
// =============================================================================

function createBarChart(id, label, labels, data, stacked = false, colors) {
    const ctx = document.getElementById(id)?.getContext('2d');
    if (!ctx) return;
    
    if (charts[id]) {
        charts[id].destroy();
        delete charts[id];
    }
    
    const datasets = (Array.isArray(data) && typeof data[0] === 'object' && data[0] !== null) 
        ? data 
        : [{ label, data, backgroundColor: colors || USER_COLORS }];
    
    charts[id] = new Chart(ctx, { 
        type: 'bar', 
        data: { labels, datasets }, 
        options: { 
            scales: { y: { beginAtZero: true, stacked }, x: { stacked } }, 
            plugins: { legend: { display: datasets.length > 1 } } 
        } 
    });
}

function createLineChart(id, label, labels, datasets) {
    const ctx = document.getElementById(id)?.getContext('2d');
    if (!ctx) return;
    
    if (charts[id]) {
        charts[id].destroy();
        delete charts[id];
    }
    
    charts[id] = new Chart(ctx, { type: 'line', data: { labels, datasets } });
}
// =============================================================================
// НОВЫЕ UI ЭЛЕМЕНТЫ
// =============================================================================
export function showLoading() { 
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.add('active');
}
export function hideLoading() { 
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.remove('active');
}
export function showNotification(message, type = 'info', duration = 3000) {
    const panel = document.getElementById('notificationsPanel');
    const list = document.getElementById('notificationsList');
    const badge = document.getElementById('notificationBadge');
    if(!panel || !list || !badge) return;

    const item = document.createElement('div');
    item.className = 'notification-item unread';
    const icon = type === 'success' ? '✅' : type === 'warning' ? '⚠️' : type === 'danger' ? '🚨' : 'ℹ️';
    item.innerHTML = `<div class="notification-icon ${type}">${icon}</div><div class="notification-content"><div class="notification-message">${createSafeText(message)}</div><div class="notification-time">только что</div></div>`;
    
    list.prepend(item);
    
    let count = parseInt(badge.textContent) + 1;
    badge.textContent = count;
    badge.style.display = 'flex';

    setTimeout(() => {
        item.style.opacity = '0';
        setTimeout(() => { 
            item.remove();
            let newCount = parseInt(badge.textContent) - 1;
            badge.textContent = newCount;
            if (newCount === 0) {
                badge.style.display = 'none';
                panel.classList.remove('active');
            }
        }, 500); 
    }, duration);
}

// Вставьте эту новую функцию в ui.js
/**
 * Renders a text-based summary of answer changes.
 * @param {Array<object>} selectedResults Array of full result objects.
 */
function renderAnswerChangesSummary(selectedResults) {
    const container = document.getElementById('answer-changes-summary-container');
    if (!container) return;

    // Проверяем, были ли смены ответов вообще
    const totalChanges = selectedResults.reduce((sum, result) => {
        const userChanges = result.behavioralMetrics?.perQuestion?.reduce((qSum, q) => qSum + (q.answerChanges || 0), 0) || 0;
        return sum + userChanges;
    }, 0);

    // Если смен не было, ничего не выводим
    if (totalChanges === 0) {
        container.innerHTML = '';
        return;
    }

    let html = '<div class="answer-changes-summary"><h4>📝 Смены ответа</h4>';

    selectedResults.forEach(result => {
        const changedQuestions = [];
        result.behavioralMetrics?.perQuestion?.forEach((q, index) => {
            if (q.answerChanges > 0) {
                // Собираем строку: #вопроса (кол-во раз)
                changedQuestions.push(`#${index + 1} (${q.answerChanges})`);
            }
        });

        html += `<p><strong>${createSafeText(result.userInfo.lastName)}:</strong> `;

        if (changedQuestions.length === 0) {
            html += '<span class="no-changes">✅ не менял(а) ответы</span>';
        } else {
            html += `<span class="has-changes">⚠️ менял(а) на вопросах: </span> <span class="question-list">${changedQuestions.join(', ')}</span>`;
        }
        html += '</p>';
    });

    html += '</div>';
    container.innerHTML = html;
}

/**
 * Handles the selection/deselection of a user card for comparison.
 * @param {HTMLElement} cardElement - The .comparison-list-card element that was clicked.
 */
export function toggleComparisonSelection(cardElement) {
    const sessionId = cardElement.dataset.sessionId;
    if (!sessionId) return;

    // 1. Обновляем набор выбранных ID в состоянии
    if (selectedForComparison.has(sessionId)) {
        selectedForComparison.delete(sessionId);
    } else {
        selectedForComparison.add(sessionId);
    }

    // 2. Обновляем внешний вид карточки
    cardElement.classList.toggle('selected');
    
    // Обновляем состояние чекбокса внутри карточки
    const checkbox = cardElement.querySelector('input[type="checkbox"]');
    if (checkbox) {
        checkbox.checked = selectedForComparison.has(sessionId);
    }

    // 3. Включаем или выключаем кнопку анализа
    const analysisBtn = document.getElementById('detailedAnalysisBtn');
    if (analysisBtn) {
        analysisBtn.disabled = selectedForComparison.size < 2;
    }
}
// Вставьте эту новую функцию в ui.js
/**
 * Creates a definition list view for a single user's fingerprint.
 */
function createSingleUserFingerprintView(result) {
    const fp = result.fingerprint || {};
    const safeFp = fp.privacySafe || {};
    const data = {
        "Хеш": fp.privacySafeHash,
        "User Agent": safeFp.userAgent,
        "Платформа": safeFp.platform,
        "WebGL Рендерер": safeFp.webGLRenderer
    };

    let html = '<dl class="fingerprint-list">';
    for (const [key, value] of Object.entries(data)) {
        html += `<dt>${createSafeText(key)}</dt><dd>${createSafeText(value) || 'N/A'}</dd>`;
    }
    html += '</dl>';
    return html;
}
