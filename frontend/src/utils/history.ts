import { fetchWithAuth, ApiError } from './fetchWithAuth';
import { HistoricalContextResponse, ReproductionResult, TechnicalState } from '../types/history';

export class SchemaValidationError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'SchemaValidationError';
    }
}

const VALID_TECHNICAL_STATES: readonly TechnicalState[] = ['valid', 'ambiguous', 'orphaned', 'tampered'];
const VALID_REPRO_STATUSES = ['MATCH', 'MISMATCH', 'NOT_REPRODUCIBLE'] as const;

export function validateHistoricalContextResponse(data: unknown): HistoricalContextResponse {
    if (!data || typeof data !== 'object') {
        throw new SchemaValidationError('Response payload must be a non-null JSON object');
    }

    const payload = data as Record<string, unknown>;

    // 1. Validate anchor
    if (!payload.anchor || typeof payload.anchor !== 'object') {
        throw new SchemaValidationError("Missing required 'anchor' object");
    }
    const anchor = payload.anchor as Record<string, unknown>;
    if (anchor.object_type !== 'recommendation' && anchor.object_type !== 'journey') {
        throw new SchemaValidationError("anchor.object_type must be either 'recommendation' or 'journey'");
    }
    if (typeof anchor.object_id !== 'string' || anchor.object_id.trim() === '') {
        throw new SchemaValidationError("anchor.object_id must be a non-empty string");
    }
    if (typeof anchor.journey_id !== 'string') {
        throw new SchemaValidationError("anchor.journey_id must be a string");
    }
    if (typeof anchor.mode !== 'string') {
        throw new SchemaValidationError("anchor.mode must be a string");
    }

    // 2. Validate technical_state
    if (typeof payload.technical_state !== 'string' || !VALID_TECHNICAL_STATES.includes(payload.technical_state as TechnicalState)) {
        throw new SchemaValidationError(`technical_state must be one of: ${VALID_TECHNICAL_STATES.join(', ')}`);
    }

    // 3. Validate bindings
    if (!payload.bindings || typeof payload.bindings !== 'object') {
        throw new SchemaValidationError("Missing required 'bindings' object");
    }
    const bindings = payload.bindings as Record<string, unknown>;
    if (!Array.isArray(bindings.evidence_refs)) {
        throw new SchemaValidationError("bindings.evidence_refs must be an array");
    }
    if (!Array.isArray(bindings.rule_evaluations)) {
        throw new SchemaValidationError("bindings.rule_evaluations must be an array");
    }
    if (!Array.isArray(bindings.recommendations)) {
        throw new SchemaValidationError("bindings.recommendations must be an array");
    }
    if (!Array.isArray(bindings.decisions)) {
        throw new SchemaValidationError("bindings.decisions must be an array");
    }
    if (!Array.isArray(bindings.actions)) {
        throw new SchemaValidationError("bindings.actions must be an array");
    }

    return data as HistoricalContextResponse;
}

export function validateReproductionResult(data: unknown): ReproductionResult {
    if (!data || typeof data !== 'object') {
        throw new SchemaValidationError('Response payload must be a non-null JSON object');
    }

    const payload = data as Record<string, unknown>;

    if (typeof payload.status !== 'string' || !VALID_REPRO_STATUSES.includes(payload.status as any)) {
        throw new SchemaValidationError(`status must be one of: ${VALID_REPRO_STATUSES.join(', ')}`);
    }
    if (typeof payload.recommendation_id !== 'string' || payload.recommendation_id.trim() === '') {
        throw new SchemaValidationError("recommendation_id must be a non-empty string");
    }
    if (!Array.isArray(payload.differences)) {
        throw new SchemaValidationError("differences must be an array");
    }
    if (payload.diagnostic !== null && payload.diagnostic !== undefined && typeof payload.diagnostic !== 'object') {
        throw new SchemaValidationError("diagnostic must be null, undefined, or an object");
    }

    return data as ReproductionResult;
}

async function parseJson(res: Response): Promise<unknown> {
    const type = res.headers.get("content-type");
    if (!type?.includes("application/json")) {
        throw new ApiError(res.status, "Invalid response type");
    }
    return res.json();
}

/**
 * Fetches the exact historical lifecycle bindings for a specific recommendation.
 */
export async function getHistoricalRecommendation(recommendationId: string): Promise<HistoricalContextResponse> {
    const res = await fetchWithAuth(`/api/v1/history/recommendations/${recommendationId}`);
    const data = await parseJson(res);
    return validateHistoricalContextResponse(data);
}

/**
 * Fetches the ordered collection of historical recommendations for an entire journey.
 * Helps identify ambiguous states if a journey spawned multiple distinct recommendations.
 */
export async function getHistoricalJourney(journeyId: string): Promise<HistoricalContextResponse> {
    const res = await fetchWithAuth(`/api/v1/history/journeys/${journeyId}`);
    const data = await parseJson(res);
    return validateHistoricalContextResponse(data);
}

/**
 * Executes the reconstruction engine against the historical binding context to
 * verify if the original recommendation would be reproduced identically today.
 */
export async function reproduceDecision(recommendationId: string): Promise<ReproductionResult> {
    const res = await fetchWithAuth(`/api/v1/history/recommendations/${recommendationId}/reproduction`);
    const data = await parseJson(res);
    return validateReproductionResult(data);
}
