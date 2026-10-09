import React, { useState, useEffect } from 'react';
import { t } from '../../localization/messages';
import { History } from 'lucide-react';
import { ProgressiveSection } from '../GovernedUI/ProgressiveSection';
import { getHistoricalJourney, reproduceDecision } from '../../utils/history';
import { HistoricalContextResponse, ReproductionResult } from '../../types/history';
import { HistorySummary } from './HistorySummary';
import { HistoricalBindings } from './HistoricalBindings';
import { AuditTimeline } from './AuditTimeline';
import { ReproductionStatus } from './ReproductionStatus';
import { ReproductionDifference } from './ReproductionDifference';
import { DiagnosticDetail } from './DiagnosticDetail';
import { MissingDependencyNotice } from './MissingDependencyNotice';

interface HistoricalTraceSectionProps {
    journeyId: string;
}

export const HistoricalTraceSection: React.FC<HistoricalTraceSectionProps> = ({ journeyId }) => {
    const [context, setContext] = useState<HistoricalContextResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [reproductionResult, setReproductionResult] = useState<ReproductionResult | null>(null);
    const [reproducing, setReproducing] = useState(false);

    useEffect(() => {
        setContext(null);
        setError(null);
        setReproductionResult(null);

        let cancelled = false;

        const loadHistory = async () => {
            setLoading(true);
            try {
                const data = await getHistoricalJourney(journeyId);
                if (cancelled) return;
                setContext(data);
                
                // If it's not ambiguous, we can auto-trigger reproduction on the first recommendation
                if (data.technical_state === 'valid' && data.bindings.recommendations.length > 0) {
                    setReproducing(true);
                    try {
                        const repro = await reproduceDecision(data.bindings.recommendations[0].recommendation_id);
                        if (cancelled) return;
                        setReproductionResult(repro);
                    } catch (e) {
                        if (cancelled) return;
                        console.error("Reproduction failed", e);
                    } finally {
                        if (!cancelled) setReproducing(false);
                    }
                }
            } catch (err: any) {
                if (cancelled) return;
                console.error("History fetch error:", err);
                setError(err.message || t.history.fetchError);
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        if (journeyId) {
            loadHistory();
        }

        return () => {
            cancelled = true;
        };
    }, [journeyId]);

    const dataState = loading ? 'loading' : error ? 'unavailable' : context ? 'ready' : 'unavailable';

    return (
        <ProgressiveSection
            id="historical-trace"
            title="D8 Historical Trace & Reproducibility"
            icon={<History className="w-5 h-5 text-indigo-400 drop-shadow-[0_0_8px_rgba(99,102,241,0.6)]" />}
            defaultExpanded={false}
            dataState={dataState}
            dataStateMessage={error || t.history.noTrace}
        >
            {context && (
                <div className="space-y-6 pt-4 pb-2">
                    <HistorySummary context={context} />
                    
                    {context.technical_state !== 'ambiguous' && (
                        <>
                            <HistoricalBindings bindings={context.bindings} />
                            <AuditTimeline bindings={context.bindings} />
                        </>
                    )}

                    {reproducing && (
                        <div className="flex items-center gap-3 mt-6 p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-xl">
                            <div className="w-4 h-4 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin" />
                            <div className="text-sm text-indigo-200 font-medium tracking-wide">{t.history.runningReproduction}</div>
                        </div>
                    )}

                    {reproductionResult && (
                        <div className="space-y-4 mt-6">
                            <ReproductionStatus result={reproductionResult} />
                            
                            {reproductionResult.status === 'MISMATCH' && reproductionResult.differences && (
                                <ReproductionDifference differences={reproductionResult.differences} />
                            )}

                            {reproductionResult.status === 'NOT_REPRODUCIBLE' && reproductionResult.diagnostic && (
                                <>
                                    <DiagnosticDetail diagnostic={reproductionResult.diagnostic} />
                                    <MissingDependencyNotice diagnostic={reproductionResult.diagnostic} />
                                </>
                            )}
                        </div>
                    )}
                </div>
            )}
        </ProgressiveSection>
    );
};
