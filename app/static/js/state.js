/**
 * js/state.js
 * * Этот модуль является единственным источником истины (Single Source of Truth)
 * для всего фронтенд-приложения. Он централизованно хранит все разделяемые данные:
 * информацию с сервера, состояние UI и настройки анализа.
 * * Все остальные модули импортируют переменные и функции-сеттеры из этого файла,
 * что обеспечивает предсказуемое и управляемое изменение состояния.
 */

// =============================================================================
// ДАННЫЕ, ПОЛУЧАЕМЫЕ С СЕРВЕРА
// =============================================================================

/** @type {Array<Object>} Массив с результатами тестов для текущей отображаемой страницы. */
export let currentPageResults = [];

/** @type {Array<Object>} Массив со всеми прерванными сессиями. */
export let allAbandonedSessions = [];

/** @type {Object.<string, {results: Array<Object>, isAnomalous: boolean}>} Группы отпечатков браузера. */
export let fingerprintGroups = {};

/** @type {Set<string>} Множество ID карточек, выбранных для детального сравнения. */
export let selectedForComparison = new Set();

/** @type {Map<string, Object>} Постоянный кэш всех загруженных результатов. Ключ - sessionId. */
export const allLoadedResults = new Map();

export let dashboardStats = null;
// =============================================================================
// СОСТОЯНИЕ ПАГИНАЦИИ
// =============================================================================

/** @type {number} Номер текущей страницы. */
export let currentPage = 1;

/** @type {number} Общее количество результатов в базе данных. */
export let totalResults = 0;

/** @type {number} Количество результатов, отображаемых на одной странице. */
export let resultsPerPage = 20;


// =============================================================================
// СОСТОЯНИЕ ИНТЕРФЕЙСА
// =============================================================================

/** @type {string} Идентификатор текущего активного вида (e.g., 'dashboard'). */
export let currentView = 'dashboard';

/** @type {Object.<string, Chart>} Объект для хранения инстансов Chart.js для их последующего уничтожения. */
export let charts = {};


// =============================================================================
// НАСТРОЙКИ АНАЛИЗА
// =============================================================================

/** @type {Object} Конфигурация пороговых значений для анализа аномалий. */
export let settings = {
    focusThreshold: 5,      // Макс. кол-во потерь фокуса
    blurThreshold: 60,      // Макс. время в секундах вне фокуса
    mouseThreshold: 85,     // Порог схожести мыши в % для DTW
    printThreshold: 0,      // Макс. кол-во попыток печати
    checkIpInFingerprint: true // Учитывать ли IP в анализе отпечатков
};


// =============================================================================
// КОНСТАНТЫ
// =============================================================================

/** @type {Array<string>} Цвета для выделения пользователей или данных на графиках. */
export const USER_COLORS = ['#2563eb', '#dc2626', '#059669', '#d97706', '#64748b', '#6d28d9'];


// =============================================================================
// ФУНКЦИИ-СЕТТЕРЫ ДЛЯ ИЗМЕНЕНИЯ СОСТОЯНИЯ
// =============================================================================

/**
 * Обновляет массив результатов тестов.
 * @param {Array<Object>} data Новый массив с результатами.
 */
export function setCurrentPageResults(data) {
    currentPageResults = data;
    // Добавляем/обновляем каждый результат в общем кэше
    allLoadedResults.clear(); // Очищаем кэш перед загрузкой новых данных
    data.forEach(result => {
        if (result.sessionId) {
            allLoadedResults.set(result.sessionId, result);
        }
    });
}

/**
 * Обновляет массив прерванных сессий.
 * @param {Array<Object>} data Новый массив с сессиями.
 */
export function setAllAbandonedSessions(data) {
    allAbandonedSessions = data;
}

/**
 * Обновляет объект с группами отпечатков.
 * @param {Object} data Новый объект групп.
 */
export function setFingerprintGroups(data) {
    fingerprintGroups = data;
}

/**
 * Устанавливает текущий активный вид.
 * @param {string} view Имя нового вида.
 */
export function setCurrentView(view) {
    currentView = view;
}

/**
 * Полностью заменяет объект настроек.
 * @param {Object} newSettings Новый объект настроек.
 */
export function setSettings(newSettings) {
    settings = newSettings;
}

/**
 * Обновляет существующие настройки новыми значениями.
 * @param {Object} partialSettings Объект с полями для обновления.
 */
export function updateSettings(partialSettings) {
    settings = { ...settings, ...partialSettings };
}


/** @type {string} The key by which the abandoned sessions table is sorted. */
export let abandonedSessionsSortKey = 'startTime';
/** @type {'asc' | 'desc'} The direction of the sort. */
export let abandonedSessionsSortDir = 'desc';


export function setAbandonedSessionsSort(key, dir) {
    abandonedSessionsSortKey = key;
    abandonedSessionsSortDir = dir;
}

/**
 * Устанавливает состояние пагинации.
 * @param {number} page Номер текущей страницы.
 * @param {number} perPage Элементов на странице.
 * @param {number} total Общее количество элементов.
 */
export function setPaginationState(page, perPage, total) {
    currentPage = page;
    resultsPerPage = perPage;
    totalResults = total;
}

/**
 * Устанавливает количество результатов на страницу.
 * @param {number|string} count - Количество для установки.
 */
export function setResultsPerPage(count) {
    // ИЗМЕНЕНИЕ: Убедитесь, что это значение соответствует
    // переменной окружения MAX_RESULTS_PER_PAGE на сервере.
    const maxLimit = 999; 
    resultsPerPage = count === 'all' ? maxLimit : parseInt(count, 10);
}


/** @type {string} Ключ для сортировки основной таблицы результатов. */
export let mainResultsSortKey = 'startTime';
/** @type {'asc' | 'desc'} Направление сортировки. */
export let mainResultsSortDir = 'desc';


export function setMainResultsSort(key, dir) {
    mainResultsSortKey = key;
    mainResultsSortDir = dir;
}
export function setDashboardStats(stats) {
    dashboardStats = stats;
}