import { fetchWithAuth, ApiError } from './fetchWithAuth';
import { HistoricalContextResponse, ReproductionResult } from '../types/history';

async function parseJson<T>(res: Response): Promise<T> {
    const type = res.headers.get("content-type");
    if (!type?.includes("application/json")) {
        throw new ApiError(res.status, "Invalid response type");
    }
    return res.json() as Promise<T>;
}

/**
 * Fetches the exact historical lifecycle bindings for a specific recommendation.
 */
export async function getHistoricalRecommendation(recommendationId: string): Promise<HistoricalContextResponse> {
    const res = await fetchWithAuth(`/api/v1/history/recommendations/${recommendationId}`);
    return parseJson<HistoricalContextResponse>(res);
}

/**
 * Fetches the ordered collection of historical recommendations for an entire journey.
 * Helps identify ambiguous states if a journey spawned multiple distinct recommendations.
 */
export async function getHistoricalJourney(journeyId: string): Promise<HistoricalContextResponse> {
    const res = await fetchWithAuth(`/api/v1/history/journeys/${journeyId}`);
    return parseJson<HistoricalContextResponse>(res);
}

/**
 * Executes the reconstruction engine against the historical binding context to
 * verify if the original recommendation would be reproduced identically today.
 */
export async function reproduceDecision(recommendationId: string): Promise<ReproductionResult> {
    const res = await fetchWithAuth(`/api/v1/history/recommendations/${recommendationId}/reproduction`);
    return parseJson<ReproductionResult>(res);
}
