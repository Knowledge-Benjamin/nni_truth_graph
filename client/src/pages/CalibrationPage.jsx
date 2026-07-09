import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { api } from '../api';
import { Activity, Info } from 'lucide-react';

const CalibrationPage = () => {
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        api.getCalibrationCurve()
            .then(res => {
                if (res.success && res.data) {
                    const formattedData = res.data.map(d => ({
                        ...d,
                        name: d.bucket,
                        'Predicted Score': d.predicted_prob,
                        'Actual True %': d.actual_prob !== null ? d.actual_prob : null,
                        'Ideal Calibration': d.predicted_prob
                    }));
                    setData(formattedData);
                } else {
                    setError('Invalid data format received');
                }
            })
            .catch(err => {
                console.error(err);
                setError(err.message);
            })
            .finally(() => {
                setLoading(false);
            });
    }, []);

    if (loading) {
        return (
            <div style={{ padding: 40, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 12 }}>
                <Activity className="animate-spin" /> Loading calibration data...
            </div>
        );
    }

    if (error) {
        return (
            <div style={{ padding: 40, color: '#ef4444' }}>
                Error loading calibration data: {error}
            </div>
        );
    }

    const CustomTooltip = ({ active, payload, label }) => {
        if (active && payload && payload.length) {
            const data = payload[0].payload;
            return (
                <div style={{ backgroundColor: '#1e293b', border: '1px solid #334155', padding: 12, borderRadius: 8 }}>
                    <p style={{ margin: '0 0 8px 0', fontWeight: 'bold', color: '#f8fafc' }}>Bucket: {label}</p>
                    <p style={{ margin: '4px 0', color: '#94a3b8' }}>Total Claims: {data.total_count}</p>
                    <p style={{ margin: '4px 0', color: '#4ade80' }}>True Claims: {data.true_count}</p>
                    <p style={{ margin: '4px 0', color: '#f87171' }}>False Claims: {data.false_count}</p>
                    <hr style={{ borderColor: '#334155', margin: '8px 0' }} />
                    <p style={{ margin: '4px 0', color: '#38bdf8' }}>Predicted: {(data.predicted_prob * 100).toFixed(1)}%</p>
                    {data.actual_prob !== null ? (
                        <p style={{ margin: '4px 0', color: '#a78bfa' }}>Actual: {(data.actual_prob * 100).toFixed(1)}%</p>
                    ) : (
                        <p style={{ margin: '4px 0', color: '#64748b' }}>Actual: N/A (No data)</p>
                    )}
                </div>
            );
        }
        return null;
    };

    return (
        <div style={{ padding: 40, maxWidth: 1000, margin: '0 auto', color: '#f8fafc' }}>
            <div style={{ marginBottom: 40 }}>
                <h1 style={{ fontSize: 28, fontWeight: 'bold', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
                    <Activity size={28} color="#3b82f6" />
                    Epistemic Score Calibration
                </h1>
                <p style={{ color: '#94a3b8', fontSize: 16, lineHeight: 1.6, marginBottom: 24 }}>
                    This visualization empirically demonstrates the accuracy of the Truth Graph's epistemic scoring formula 
                    (using weights α = 0.4 for prior authority, β = 0.6 for corroborating evidence). 
                    It compares the <strong>Predicted Score</strong> against the <strong>Actual True Probability</strong> of claims falling into each bucket.
                </p>

                <div style={{ backgroundColor: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)', padding: 16, borderRadius: 8, display: 'flex', gap: 12 }}>
                    <Info size={20} color="#60a5fa" style={{ flexShrink: 0, marginTop: 2 }} />
                    <div style={{ fontSize: 14, color: '#bfdbfe', lineHeight: 1.5 }}>
                        <strong>Why does this matter?</strong> A perfectly calibrated system follows the diagonal line (Actual = Predicted). 
                        If the actual curve falls significantly below the ideal line, the model is overconfident. 
                        If it's above, it's underconfident. This proves to enterprise buyers that our epistemic score correlates with ground truth verifiability.
                    </div>
                </div>
            </div>

            <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, padding: '32px 32px 16px 16px' }}>
                <ResponsiveContainer width="100%" height={400}>
                    <LineChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis 
                            dataKey="name" 
                            stroke="#64748b" 
                            label={{ value: 'Epistemic Score Bucket', position: 'insideBottom', offset: -15, fill: '#94a3b8' }} 
                        />
                        <YAxis 
                            stroke="#64748b" 
                            domain={[0, 1]} 
                            tickFormatter={(val) => `${(val * 100).toFixed(0)}%`}
                            label={{ value: 'Actual True Probability', angle: -90, position: 'insideLeft', offset: -5, fill: '#94a3b8' }} 
                        />
                        <Tooltip content={<CustomTooltip />} />
                        <Legend verticalAlign="top" height={36} />
                        
                        <Line 
                            type="monotone" 
                            dataKey="Ideal Calibration" 
                            stroke="#64748b" 
                            strokeDasharray="5 5" 
                            dot={false} 
                            name="Ideal Calibration (y=x)" 
                        />
                        <Line 
                            type="monotone" 
                            dataKey="Actual True %" 
                            stroke="#a78bfa" 
                            strokeWidth={3}
                            activeDot={{ r: 8 }} 
                            connectNulls
                            name="Empirical True Probability" 
                        />
                    </LineChart>
                </ResponsiveContainer>
            </div>

            <div style={{ marginTop: 40 }}>
                <h3 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16, color: '#e2e8f0' }}>Raw Bucket Data</h3>
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid #1e293b', color: '#94a3b8', textAlign: 'left' }}>
                                <th style={{ padding: '12px 8px' }}>Bucket</th>
                                <th style={{ padding: '12px 8px' }}>Predicted Mean</th>
                                <th style={{ padding: '12px 8px' }}>Actual True %</th>
                                <th style={{ padding: '12px 8px' }}>True Claims</th>
                                <th style={{ padding: '12px 8px' }}>False Claims</th>
                                <th style={{ padding: '12px 8px' }}>Total Samples</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.map((row, i) => (
                                <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                                    <td style={{ padding: '12px 8px', color: '#f8fafc' }}>{row.bucket}</td>
                                    <td style={{ padding: '12px 8px', color: '#38bdf8' }}>{(row['Predicted Score'] * 100).toFixed(1)}%</td>
                                    <td style={{ padding: '12px 8px', color: '#a78bfa', fontWeight: 'bold' }}>
                                        {row['Actual True %'] !== null ? `${(row['Actual True %'] * 100).toFixed(1)}%` : 'N/A'}
                                    </td>
                                    <td style={{ padding: '12px 8px', color: '#4ade80' }}>{row.true_count}</td>
                                    <td style={{ padding: '12px 8px', color: '#f87171' }}>{row.false_count}</td>
                                    <td style={{ padding: '12px 8px', color: '#94a3b8' }}>{row.total_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
};

export default CalibrationPage;
