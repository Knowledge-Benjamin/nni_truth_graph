import React, { useEffect, useMemo, useState } from 'react';
import {
    CartesianGrid,
    ReferenceLine,
    ResponsiveContainer,
    Scatter,
    ScatterChart,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { api } from '../api';

const bucketLabels = {
    '0.0-0.2': '0.0–0.2',
    '0.2-0.4': '0.2–0.4',
    '0.4-0.6': '0.4–0.6',
    '0.6-0.8': '0.6–0.8',
    '0.8-1.0': '0.8–1.0',
};

function CalibrationPage() {
    const [curve, setCurve] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;

        const load = async () => {
            try {
                setLoading(true);
                setError('');
                const response = await api.getCalibrationCurve();
                const data = Array.isArray(response) ? response : (response?.data ?? []);
                if (!cancelled) {
                    setCurve(data);
                }
            } catch (err) {
                if (!cancelled) {
                    console.error('Calibration curve load failed', err);
                    setError(err.message || 'Unable to load calibration data');
                }
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        };

        load();
        return () => {
            cancelled = true;
        };
    }, []);

    const chartData = useMemo(() => 
        (curve || [])
            .filter((item) => item?.actual_prob !== null && item?.actual_prob !== undefined)
            .map((item) => ({
                ...item,
                predicted: Number(item.predicted_prob ?? 0),
                actual: Number(item.actual_prob ?? 0),
                label: bucketLabels[item.bucket] || item.bucket,
            })),
        [curve]
    );

    return (
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '24px 20px 48px' }}>
            <div style={{ marginBottom: 24 }}>
                <h2 style={{ margin: 0, fontSize: 28, color: '#f8fafc' }}>Epistemic Score Calibration</h2>
                <p style={{ margin: '8px 0 0', color: '#94a3b8', maxWidth: 760, lineHeight: 1.6 }}>
                    This public view shows how often claims in each epistemic score bucket turn out to be true in practice.
                    The curve is a proof-point for buyers that the weighting formula is empirically aligned with observed outcomes.
                </p>
            </div>

            <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', marginBottom: 24 }}>
                <div style={{ background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(148,163,184,0.2)', borderRadius: 16, padding: 16 }}>
                    <div style={{ fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#38bdf8', marginBottom: 8 }}>Why α = 0.4?</div>
                    <div style={{ color: '#e2e8f0', lineHeight: 1.6 }}>
                        The chart lets enterprise buyers see whether the scored confidence reliably tracks the actual rate of truth.
                        A curve that stays near the diagonal means the formula is well calibrated rather than simply overconfident.
                    </div>
                </div>
                <div style={{ background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(148,163,184,0.2)', borderRadius: 16, padding: 16 }}>
                    <div style={{ fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#22c55e', marginBottom: 8 }}>Interpretation</div>
                    <div style={{ color: '#e2e8f0', lineHeight: 1.6 }}>
                        If the curve sits above the diagonal, the model is underestimating truth frequency. If it sits below, it is overstating confidence.
                    </div>
                </div>
            </div>

            <div style={{ background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(148,163,184,0.2)', borderRadius: 20, padding: 20 }}>
                {loading ? (
                    <div style={{ color: '#e2e8f0', padding: '40px 0', textAlign: 'center' }}>Loading calibration data…</div>
                ) : error ? (
                    <div style={{ color: '#fda4af', padding: '40px 0', textAlign: 'center' }}>{error}</div>
                ) : chartData.length === 0 ? (
                    <div style={{ color: '#e2e8f0', padding: '40px 0', textAlign: 'center' }}>No calibration data is available yet.</div>
                ) : (
                    <>
                        <div style={{ height: 380 }}>
                            <ResponsiveContainer width="100%" height="100%">
                                <ScatterChart margin={{ top: 16, right: 24, left: 8, bottom: 24 }}>
                                    <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
                                    <XAxis
                                        type="number"
                                        dataKey="predicted"
                                        name="Predicted Epistemic Score"
                                        domain={[0, 1]}
                                        tickFormatter={(value) => value.toFixed(1)}
                                        label={{ value: 'Predicted Epistemic Score', position: 'insideBottom', offset: -8, fill: '#cbd5e1' }}
                                        tick={{ fill: '#cbd5e1' }}
                                    />
                                    <YAxis
                                        type="number"
                                        dataKey="actual"
                                        name="Actual True Probability"
                                        domain={[0, 1]}
                                        tickFormatter={(value) => value.toFixed(1)}
                                        label={{ value: 'Actual True Probability', angle: -90, position: 'insideLeft', fill: '#cbd5e1' }}
                                        tick={{ fill: '#cbd5e1' }}
                                    />
                                    <Tooltip
                                        cursor={{ strokeDasharray: '3 3' }}
                                        formatter={(value) => `${Number(value * 100).toFixed(0)}%`}
                                        labelFormatter={(label) => `Bucket: ${label}`}
                                    />
                                    <ReferenceLine
                                        segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
                                        stroke="#38bdf8"
                                        strokeDasharray="5 5"
                                        label={{ value: 'Ideal calibration', position: 'insideTopRight', fill: '#38bdf8' }}
                                    />
                                    <Scatter data={chartData} fill="#22c55e" />
                                </ScatterChart>
                            </ResponsiveContainer>
                        </div>

                        <div style={{ marginTop: 16, display: 'grid', gap: 10 }}>
                            {chartData.map((item) => (
                                <div key={item.bucket} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 12px', borderRadius: 10, background: 'rgba(255,255,255,0.03)', color: '#e2e8f0' }}>
                                    <span style={{ fontWeight: 600 }}>{item.label}</span>
                                    <span style={{ color: '#94a3b8' }}>
                                        {item.total_count} claims • {item.actual_prob !== null ? `${(item.actual_prob * 100).toFixed(1)}%` : 'n/a'} true
                                    </span>
                                </div>
                            ))}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

export default CalibrationPage;
