import { fetchWithAuth } from './fetchWithAuth';
import { HistoricalContextResponse, ReproductionResult } from '../types/history';

/**
 * Fetches the exact historical lifecycle bindings for a specific recommendation.
 */
export async function getHistoricalRecommendation(recommendationId: string): Promise<HistoricalContextResponse> {
    const res = await fetchWithAuth(`/api/v1/history/recommendations/${recommendationId}`);
    return res.json();
}

/**
 * Fetches the ordered collection of historical recommendations for an entire journey.
 * Helps identify ambiguous states if a journey spawned multiple distinct recommendations.
 */
export async function getHistoricalJourney(journeyId: string): Promise<HistoricalContextResponse> {
    const res = await fetchWithAuth(`/api/v1/history/journeys/${journeyId}`);
    return res.json();
}

/**
 * Executes the reconstruction engine against the historical binding context to
 * verify if the original recommendation would be reproduced identically today.
 */
export async function reproduceDecision(recommendationId: string): Promise<ReproductionResult> {
    const res = await fetchWithAuth(`/api/v1/history/recommendations/${recommendationId}/reproduction`);
    const contentType = res.headers.get("content-type");
    if (!contentType || !contentType.includes("application/json")) {
        throw new Error("Invalid content type from server");
    }
    return res.json();
}
