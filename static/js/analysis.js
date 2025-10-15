/**
 * js/analysis.js
 * Этот модуль содержит "мозги" клиентской части приложения.
 * Здесь находятся функции для сложных вычислений, сравнения данных и
 * извлечения признаков, которые не связаны напрямую с DOM или API.
 * Он работает с данными из state.js и возвращает результаты для дальнейшего использования.
 */

import { allLoadedResults, settings, setFingerprintGroups, selectedForComparison } from './state.js'; // Импортируем кэш


// =============================================================================
// FINGERPRINT ANALYSIS
// =============================================================================

/**
 * Анализирует отпечатки браузеров (fingerprints) всех пользователей,
 * находит аномальные совпадения и обновляет глобальное состояние через сеттер.
 */
export function analyzeFingerprints() {
    const newFingerprintGroups = {};
    const getFingerprintHash = (result) => result.fingerprint?.privacySafeHash || result.fingerprint?.privacySafe?.webGLRenderer || 'unknown';

    // Шаг 1: Группируем все результаты по их хешу отпечатка.
    Array.from(allLoadedResults.values()).forEach(result => { 
        const hash = getFingerprintHash(result);
        result.fingerprintHash = hash; // Добавляем хеш прямо в объект для удобства
        if (hash !== 'unknown') {
            if (!newFingerprintGroups[hash]) {
                newFingerprintGroups[hash] = { results: [], isAnomalous: false };
            }
            newFingerprintGroups[hash].results.push(result);
        }
    });

    // Шаг 2: Проверяем каждую группу на аномалии.
    Object.values(newFingerprintGroups).forEach(group => {
        if (group.results.length <= 1) return; // Группа из одного человека не может быть аномальной.

        const usersByKey = {};
        group.results.forEach(result => {
            // Ключ для подгруппы зависит от настроек (учитывать IP + тест или только тест).
            const key = settings.checkIpInFingerprint
                ? `${result.clientIp || 'unknown_ip'}|${result.testType || 'unknown_test'}`
                : result.testType || 'unknown_test';
            
            if (!usersByKey[key]) {
                usersByKey[key] = new Set();
            }
            
            const userName = `${result.userInfo.lastName} ${result.userInfo.firstName}`.trim().toLowerCase();
            if (userName) {
                usersByKey[key].add(userName);
            }
        });

        // Аномалия, если в любой из подгрупп оказалось больше одного уникального пользователя.
        group.isAnomalous = Object.values(usersByKey).some(users => users.size > 1);
    });
    
    // Шаг 3: Обновляем глобальное состояние.
    setFingerprintGroups(newFingerprintGroups);
}


// =============================================================================
// MOUSE TRAJECTORY ANALYSIS (DTW)
// =============================================================================

/**
 * Объект-неймспейс, содержащий методы для анализа траекторий мыши
 * с помощью алгоритма Dynamic Time Warping (DTW).
 */
export const dtwAnalysis = {
    /**
     * Вычисляет евклидово расстояние между двумя точками.
     * @param {number[]} point1 - Координаты первой точки [x, y].
     * @param {number[]} point2 - Координаты второй точки [x, y].
     * @returns {number} Расстояние между точками.
     */
    euclideanDistance(point1, point2) {
        return Math.sqrt(Math.pow(point1[0] - point2[0], 2) + Math.pow(point1[1] - point2[1], 2));
    },

    /**
     * Реализация алгоритма Fast Dynamic Time Warping.
     * @param {number[][]} sequence1 - Первая последовательность точек [[x, y], ...].
     * @param {number[][]} sequence2 - Вторая последовательность точек [[x, y], ...].
     * @returns {number} DTW-расстояние между последовательностями.
     */
    fastDTW(sequence1, sequence2) {
        const n = sequence1.length;
        const m = sequence2.length;
        // Создаем матрицу для динамического программирования
        const dtw = Array(n + 1).fill(null).map(() => Array(m + 1).fill(Infinity));
        dtw[0][0] = 0;

        for (let i = 1; i <= n; i++) {
            for (let j = 1; j <= m; j++) {
                const cost = this.euclideanDistance(sequence1[i - 1], sequence2[j - 1]);
                dtw[i][j] = cost + Math.min(dtw[i - 1][j], dtw[i][j - 1], dtw[i - 1][j - 1]);
            }
        }
        return dtw[n][m];
    },

    /**
     * Извлекает только первое осмысленное движение из полной траектории,
     * отсекая все, что происходит после значительной паузы.
     * @param {number[][]} trajectory - Полная траектория движений [[x, y, timestamp], ...].
     * @returns {number[][]} Укороченная траектория первого "росчерка".
     */
    extractInitialStroke(trajectory) {
        if (!trajectory || trajectory.length < 2) return trajectory;
        const PAUSE_THRESHOLD_SEC = 0.25; // Порог паузы в секундах
        for (let i = 1; i < trajectory.length; i++) {
            const timeDelta = (trajectory[i][2] - trajectory[i - 1][2]) / 1000.0;
            if (timeDelta > PAUSE_THRESHOLD_SEC) {
                return trajectory.slice(0, i);
            }
        }
        return trajectory; // Если пауз не было, возвращаем всю траекторию.
    },

    /**
     * Сравнивает две траектории, нормализуя их по размеру и положению,
     * и вычисляет итоговый процент схожести их формы.
     * @param {number[][]} trajectory1 - Первая полная траектория.
     * @param {number[][]} trajectory2 - Вторая полная траектория.
     * @returns {number} Процент схожести (от 0 до 100).
     */
    compareMouseTrajectories(trajectory1, trajectory2) {
        const stroke1 = this.extractInitialStroke(trajectory1);
        const stroke2 = this.extractInitialStroke(trajectory2);

        // Если данных для анализа недостаточно, считаем их непохожими.
        if (!stroke1 || !stroke2 || stroke1.length < 10 || stroke2.length < 10) return 0.0;

        const points1 = stroke1.map(p => [p[0], p[1]]);
        const points2 = stroke2.map(p => [p[0], p[1]]);

        // Нормализация траекторий к единому размеру (1000x1000), чтобы сравнивать только форму.
        const normalize = (points) => {
            const xs = points.map(p => p[0]);
            const ys = points.map(p => p[1]);
            const minX = Math.min(...xs), maxX = Math.max(...xs);
            const minY = Math.min(...ys), maxY = Math.max(...ys);
            const rangeX = maxX - minX || 1;
            const rangeY = maxY - minY || 1;
            return points.map(p => [1000 * (p[0] - minX) / rangeX, 1000 * (p[1] - minY) / rangeY]);
        };
        
        const normP1 = normalize(points1);
        const normP2 = normalize(points2);

        const distance = this.fastDTW(normP1, normP2);
        // Максимально возможное расстояние для нормализованных данных.
        const maxPossibleDist = 1000 * Math.sqrt(2) * Math.max(normP1.length, normP2.length);
        const normalizedDistance = maxPossibleDist > 0 ? distance / maxPossibleDist : 1.0;
        
        let similarity = Math.max(0, (1 - normalizedDistance) * 100);

        // Увеличиваем контрастность для высоких значений, чтобы сделать аномалии более заметными.
        if (similarity > 80) {
            similarity = 80 + (similarity - 80) * 1.5;
        }
        
        return Math.min(100, similarity);
    }
};

// =============================================================================
// ANALYSIS ORCHESTRATION
// =============================================================================

/**
 * Запускает клиентский DTW-анализ для пользователей, выбранных для сравнения.
 * @returns {Promise<object>} Объект с результатами анализа для каждой пары сессий.
 */
export async function runClientDtwAnalysis(selectedResults) { // Принимаем полные результаты как аргумент
    const dtwContainer = document.getElementById('dtw-analysis-results');
    if (dtwContainer) {
        dtwContainer.innerHTML = `<div class="loading">Выполняется DTW анализ в браузере...</div>`;
    }
    await new Promise(resolve => setTimeout(resolve, 50)); 
    
    const analysisData = {};
    
    for (let i = 0; i < selectedResults.length; i++) {
        for (let j = i + 1; j < selectedResults.length; j++) {
            const result1 = selectedResults[i];
            const result2 = selectedResults[j];

            if (!result1 || !result2 || result1.testType !== result2.testType) continue;

            const pairKey = `${result1.sessionId}_vs_${result2.sessionId}`;
            analysisData[pairKey] = {};

            const questions1 = result1.behavioralMetrics?.perQuestion || [];
            const questions2 = result2.behavioralMetrics?.perQuestion || [];
            const numQuestions = Math.min(questions1.length, questions2.length);

            for (let qIndex = 0; qIndex < numQuestions; qIndex++) {
                const traj1 = questions1[qIndex]?.mouseMovements;
                const traj2 = questions2[qIndex]?.mouseMovements;
                if (traj1 && traj2) {
                    const similarity = dtwAnalysis.compareMouseTrajectories(traj1, traj2);
                    analysisData[pairKey][qIndex] = Math.round(similarity);
                }
            }
        }
    }
    console.log("Client DTW analysis finished:", analysisData);
    return analysisData;
}